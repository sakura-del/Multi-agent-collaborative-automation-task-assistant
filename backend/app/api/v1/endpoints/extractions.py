"""自定义论文抽取表 HTTP 路由（Issue #36）。

端点清单（全部要求 Bearer JWT，owner-scoped，跨用户统一返回 404）：
- POST   /api/v1/extractions                      创建抽取表（auto_run=True 时后台触发抽取）
- GET    /api/v1/extractions                      列出当前用户的抽取表摘要（可选 ?task_id=）
- GET    /api/v1/extractions/{table_id}           获取抽取表完整详情与单元格
- POST   /api/v1/extractions/{table_id}/columns   追加自定义列（不覆盖旧列）
- POST   /api/v1/extractions/{table_id}/run       重跑整表或仅补抽指定列/指定论文
- GET    /api/v1/extractions/{table_id}/export    导出 CSV（UTF-8 BOM + 防公式注入）
- DELETE /api/v1/extractions/{table_id}           删除抽取表
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response, status
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_active_user
from app.core.exceptions import NotFoundException, ValidationException
from app.core.logging import get_logger
from app.db.models.extraction import ExtractionCell, ExtractionTable
from app.db.models.knowledge import KnowledgeDoc
from app.db.models.screening import ScreeningRecord
from app.db.models.task import Task
from app.db.models.user import User
from app.db.session import AsyncSessionLocal, get_db
from app.knowledge.services.table_extractor import TableExtractor, export_table_csv
from app.llm.byok import get_credential_resolver
from app.llm.factory import get_llm_client
from app.orchestration.contracts import EvidenceLocator
from app.schemas.extraction import (
    ExtractionCellOut,
    ExtractionColumnAddRequest,
    ExtractionColumnSpec,
    ExtractionRunRequest,
    ExtractionSourceOut,
    ExtractionSourceRef,
    ExtractionTableCreate,
    ExtractionTableListOut,
    ExtractionTableOut,
    ExtractionTableSummaryOut,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/extractions", tags=["extractions"])

DbDep = Annotated[AsyncSession, Depends(get_db)]
CurrentUserDep = Annotated[User, Depends(get_current_active_user)]


async def _resolve_llm_for_user(
    session: AsyncSession,
    user_id: uuid.UUID,
    llm_credential_id: uuid.UUID | None,
) -> Any:
    """按 BYOK 凭据或平台默认客户端解析本次抽取用 LLM。"""
    if llm_credential_id is not None:
        return await get_credential_resolver().resolve(session, user_id, llm_credential_id)
    return get_llm_client()


async def run_extraction_job(
    table_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    column_keys: Sequence[str] = (),
    source_ids: Sequence[uuid.UUID] = (),
    llm_credential_id: uuid.UUID | None = None,
) -> None:
    """后台任务入口：使用独立数据库会话执行抽取（不复用已关闭的请求会话）。"""
    async with AsyncSessionLocal() as session:
        try:
            llm = await _resolve_llm_for_user(session, user_id, llm_credential_id)
        except Exception as exc:
            logger.warning(
                "extraction_llm_resolve_failed",
                table_id=str(table_id),
                error=str(exc),
            )
            table = (
                await session.execute(
                    select(ExtractionTable).where(
                        ExtractionTable.id == table_id,
                        ExtractionTable.user_id == user_id,
                    )
                )
            ).scalar_one_or_none()
            if table is not None:
                table.status = "failed"
                table.error = str(exc)[:1000]
                table.updated_at = datetime.now(UTC)
                await session.commit()
            return

        extractor = TableExtractor(llm)
        await extractor.run(
            session,
            table_id,
            user_id,
            column_keys=column_keys,
            source_ids=source_ids,
        )


def _to_table_out(table: ExtractionTable) -> ExtractionTableOut:
    cells_out: list[ExtractionCellOut] = []
    for cell in sorted(table.cells, key=lambda c: (c.source_type, c.source_id, c.column_key)):
        locator = (
            EvidenceLocator.model_validate(cell.locator)
            if isinstance(cell.locator, dict) and cell.locator
            else None
        )
        cells_out.append(
            ExtractionCellOut(
                source_type=cell.source_type,  # type: ignore[arg-type]
                source_id=cell.source_id,
                column_key=cell.column_key,
                status=cell.status,  # type: ignore[arg-type]
                value=cell.value,
                quote=cell.quote,
                passage=cell.passage,
                locator=locator,
                evidence_scope=cell.evidence_scope,  # type: ignore[arg-type]
                confidence=cell.confidence,
            )
        )
    return ExtractionTableOut(
        id=table.id,
        task_id=table.task_id,
        title=table.title,
        status=table.status,  # type: ignore[arg-type]
        error=table.error,
        columns=[ExtractionColumnSpec.model_validate(c) for c in (table.columns or [])],
        sources=[ExtractionSourceOut.model_validate(s) for s in (table.sources or [])],
        cells=cells_out,
        created_at=table.created_at,
        updated_at=table.updated_at,
    )


async def _load_owned_table(
    db: AsyncSession,
    table_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    with_cells: bool = True,
) -> ExtractionTable:
    stmt = select(ExtractionTable).where(
        ExtractionTable.id == table_id,
        ExtractionTable.user_id == user_id,
    )
    if with_cells:
        stmt = stmt.options(selectinload(ExtractionTable.cells))
    table = (await db.execute(stmt)).scalar_one_or_none()
    if table is None:
        raise NotFoundException("抽取表不存在")
    return table


async def _resolve_sources(
    db: AsyncSession,
    user_id: uuid.UUID,
    refs: Sequence[ExtractionSourceRef],
) -> list[dict[str, Any]]:
    """校验全部论文归属当前用户，组装行源快照；任一越权/不存在一律 404。"""
    deduped: list[ExtractionSourceRef] = []
    seen: set[tuple[str, uuid.UUID]] = set()
    for ref in refs:
        key = (ref.source_type, ref.source_id)
        if key not in seen:
            seen.add(key)
            deduped.append(ref)

    resolved: list[dict[str, Any]] = []
    for ref in deduped:
        if ref.source_type == "knowledge_doc":
            doc = (
                await db.execute(
                    select(KnowledgeDoc).where(
                        KnowledgeDoc.id == ref.source_id,
                        KnowledgeDoc.user_id == user_id,
                    )
                )
            ).scalar_one_or_none()
            if doc is None:
                raise NotFoundException("指定的知识库文档不存在")
            meta = doc.meta_info if isinstance(doc.meta_info, dict) else {}
            raw_year = meta.get("year")
            year = (
                int(raw_year)
                if isinstance(raw_year, int) and not isinstance(raw_year, bool)
                else None
            )
            source_url = str(meta.get("source_url")) if meta.get("source_url") else None
            resolved.append(
                {
                    "source_type": "knowledge_doc",
                    "source_id": str(doc.id),
                    "title": doc.title,
                    "year": year,
                    "source_url": source_url,
                }
            )
        elif ref.source_type == "screening_record":
            record = (
                await db.execute(
                    select(ScreeningRecord)
                    .join(Task, Task.id == ScreeningRecord.task_id)
                    .where(
                        ScreeningRecord.id == ref.source_id,
                        Task.user_id == user_id,
                    )
                )
            ).scalar_one_or_none()
            if record is None:
                raise NotFoundException("指定的筛选文献不存在")
            source_url = f"https://doi.org/{record.doi.strip()}" if record.doi else None
            resolved.append(
                {
                    "source_type": "screening_record",
                    "source_id": str(record.id),
                    "title": record.title,
                    "year": None,
                    "source_url": source_url,
                }
            )
    return resolved


@router.post("", response_model=ExtractionTableOut, status_code=status.HTTP_201_CREATED)
async def create_extraction_table(
    payload: ExtractionTableCreate,
    background_tasks: BackgroundTasks,
    db: DbDep,
    current_user: CurrentUserDep,
) -> ExtractionTableOut:
    """创建自定义论文抽取表；`auto_run=True` 时在后台立即启动抽取。"""
    if payload.task_id is not None:
        owned_task = (
            await db.execute(
                select(Task.id).where(
                    Task.id == payload.task_id,
                    Task.user_id == current_user.id,
                )
            )
        ).scalar_one_or_none()
        if owned_task is None:
            raise NotFoundException("关联任务不存在")

    if payload.llm_credential_id is not None:
        await get_credential_resolver().resolve(db, current_user.id, payload.llm_credential_id)

    resolved_sources = await _resolve_sources(db, current_user.id, payload.sources)
    columns_data = [col.model_dump(mode="json") for col in payload.columns]
    initial_status = "running" if payload.auto_run else "draft"

    table = ExtractionTable(
        user_id=current_user.id,
        task_id=payload.task_id,
        title=payload.title.strip(),
        columns=columns_data,
        sources=resolved_sources,
        status=initial_status,
    )
    db.add(table)
    await db.flush()

    for src in resolved_sources:
        scope = "full_text" if src["source_type"] == "knowledge_doc" else "abstract_only"
        for col in payload.columns:
            db.add(
                ExtractionCell(
                    table_id=table.id,
                    source_type=src["source_type"],
                    source_id=src["source_id"],
                    column_key=col.key,
                    status="pending",
                    evidence_scope=scope,
                )
            )

    await db.commit()
    loaded = await _load_owned_table(db, table.id, current_user.id, with_cells=True)

    if payload.auto_run:
        background_tasks.add_task(
            run_extraction_job,
            loaded.id,
            current_user.id,
            llm_credential_id=payload.llm_credential_id,
        )

    return _to_table_out(loaded)


@router.get("", response_model=ExtractionTableListOut)
async def list_extraction_tables(
    db: DbDep,
    current_user: CurrentUserDep,
    task_id: uuid.UUID | None = Query(default=None),
) -> ExtractionTableListOut:
    """列出当前用户的抽取表摘要（支持按 task_id 过滤）。"""
    stmt = (
        select(ExtractionTable)
        .where(ExtractionTable.user_id == current_user.id)
        .order_by(ExtractionTable.updated_at.desc(), ExtractionTable.id.desc())
    )
    if task_id is not None:
        stmt = stmt.where(ExtractionTable.task_id == task_id)
    rows = (await db.execute(stmt)).scalars().all()
    if not rows:
        return ExtractionTableListOut(items=[])

    # 单次聚合查询全部可见表的细胞进度，避免 N+1 或拉取大文本列
    table_ids = [row.id for row in rows]
    agg_stmt = (
        select(
            ExtractionCell.table_id,
            func.coalesce(
                func.sum(
                    case(
                        (ExtractionCell.status.in_(("extracted", "not_reported")), 1),
                        else_=0,
                    )
                ),
                0,
            ),
            func.coalesce(
                func.sum(case((ExtractionCell.status == "error", 1), else_=0)),
                0,
            ),
            func.count(ExtractionCell.id),
        )
        .where(ExtractionCell.table_id.in_(table_ids))
        .group_by(ExtractionCell.table_id)
    )
    counts_by_table: dict[uuid.UUID, tuple[int, int, int]] = {}
    for tid, completed, failed, total in (await db.execute(agg_stmt)).all():
        counts_by_table[tid] = (int(completed or 0), int(failed or 0), int(total or 0))

    items: list[ExtractionTableSummaryOut] = []
    for row in rows:
        col_count = len(row.columns or [])
        src_count = len(row.sources or [])
        completed_cells, failed_cells, cell_total = counts_by_table.get(row.id, (0, 0, 0))
        items.append(
            ExtractionTableSummaryOut(
                id=row.id,
                task_id=row.task_id,
                title=row.title,
                status=row.status,  # type: ignore[arg-type]
                error=row.error,
                column_count=col_count,
                source_count=src_count,
                completed_cells=completed_cells,
                failed_cells=failed_cells,
                total_cells=max(cell_total, col_count * src_count),
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
        )
    return ExtractionTableListOut(items=items)


@router.get("/{table_id}", response_model=ExtractionTableOut)
async def get_extraction_table(
    table_id: uuid.UUID,
    db: DbDep,
    current_user: CurrentUserDep,
) -> ExtractionTableOut:
    """获取单张抽取表详情及所有单元格（跨用户返回 404）。"""
    table = await _load_owned_table(db, table_id, current_user.id, with_cells=True)
    return _to_table_out(table)


@router.post("/{table_id}/columns", response_model=ExtractionTableOut)
async def add_extraction_column(
    table_id: uuid.UUID,
    payload: ExtractionColumnAddRequest,
    background_tasks: BackgroundTasks,
    db: DbDep,
    current_user: CurrentUserDep,
) -> ExtractionTableOut:
    """向既有抽取表追加一列；可选仅对该新列触发增量抽取，不覆盖旧列。"""
    table = await _load_owned_table(db, table_id, current_user.id, with_cells=True)
    existing_columns = list(table.columns or [])
    if len(existing_columns) >= 12:
        raise ValidationException("单张抽取表最多支持 12 列")
    if any(str(c.get("key")) == payload.column.key for c in existing_columns):
        raise ValidationException(f"列标识已存在：{payload.column.key}")

    if payload.llm_credential_id is not None:
        await get_credential_resolver().resolve(db, current_user.id, payload.llm_credential_id)

    table.columns = [*existing_columns, payload.column.model_dump(mode="json")]
    for src in table.sources or []:
        scope = "full_text" if src.get("source_type") == "knowledge_doc" else "abstract_only"
        db.add(
            ExtractionCell(
                table_id=table.id,
                source_type=str(src["source_type"]),
                source_id=str(src["source_id"]),
                column_key=payload.column.key,
                status="pending",
                evidence_scope=scope,
            )
        )
    if payload.auto_run:
        table.status = "running"
        table.error = None
    table.updated_at = datetime.now(UTC)

    await db.commit()
    loaded = await _load_owned_table(db, table.id, current_user.id, with_cells=True)

    if payload.auto_run:
        background_tasks.add_task(
            run_extraction_job,
            loaded.id,
            current_user.id,
            column_keys=[payload.column.key],
            llm_credential_id=payload.llm_credential_id,
        )

    return _to_table_out(loaded)


@router.post(
    "/{table_id}/run",
    response_model=ExtractionTableOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_extraction_table(
    table_id: uuid.UUID,
    payload: ExtractionRunRequest,
    background_tasks: BackgroundTasks,
    db: DbDep,
    current_user: CurrentUserDep,
) -> ExtractionTableOut:
    """触发整表重跑，或仅对指定 `column_keys` / `source_ids` 局部补抽。"""
    table = await _load_owned_table(db, table_id, current_user.id, with_cells=True)
    valid_keys = {str(c.get("key")) for c in (table.columns or [])}
    if payload.column_keys:
        unknown = [k for k in payload.column_keys if k not in valid_keys]
        if unknown:
            raise ValidationException(f"未知列标识：{', '.join(unknown)}")

    if payload.llm_credential_id is not None:
        await get_credential_resolver().resolve(db, current_user.id, payload.llm_credential_id)

    target_cols = set(payload.column_keys) if payload.column_keys else valid_keys
    target_sources = (
        {str(sid) for sid in payload.source_ids}
        if payload.source_ids
        else {str(src.get("source_id")) for src in (table.sources or [])}
    )

    now = datetime.now(UTC)
    existing_coords: set[tuple[str, str, str]] = set()
    for cell in table.cells:
        existing_coords.add((cell.source_type, cell.source_id, cell.column_key))
        if cell.column_key in target_cols and cell.source_id in target_sources:
            cell.status = "pending"
            cell.updated_at = now

    for src in table.sources or []:
        src_type = str(src.get("source_type"))
        src_id = str(src.get("source_id"))
        if src_id not in target_sources:
            continue
        scope = "full_text" if src_type == "knowledge_doc" else "abstract_only"
        for col_key in target_cols:
            if (src_type, src_id, col_key) not in existing_coords:
                db.add(
                    ExtractionCell(
                        table_id=table.id,
                        source_type=src_type,
                        source_id=src_id,
                        column_key=col_key,
                        status="pending",
                        evidence_scope=scope,
                    )
                )

    table.status = "running"
    table.error = None
    table.updated_at = now
    await db.commit()

    loaded = await _load_owned_table(db, table.id, current_user.id, with_cells=True)
    background_tasks.add_task(
        run_extraction_job,
        loaded.id,
        current_user.id,
        column_keys=list(payload.column_keys),
        source_ids=list(payload.source_ids),
        llm_credential_id=payload.llm_credential_id,
    )
    return _to_table_out(loaded)


@router.get("/{table_id}/export")
async def export_extraction_table(
    table_id: uuid.UUID,
    db: DbDep,
    current_user: CurrentUserDep,
    format: Literal["csv"] = Query(default="csv"),
    include_quotes: bool = Query(default=True),
) -> Response:
    """导出抽取表为 CSV（UTF-8 BOM + 公式注入防护）。"""
    _ = format  # 一期仅支持 csv，由 Query Literal 校验
    table = await _load_owned_table(db, table_id, current_user.id, with_cells=True)
    csv_bytes = export_table_csv(table, include_quotes=include_quotes)
    safe_name = re.sub(r'[\\/:*?"<>|\r\n]+', "_", table.title).strip() or "extraction_table"
    encoded_name = quote(safe_name, safe="")
    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}.csv",
        },
    )


@router.delete("/{table_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_extraction_table(
    table_id: uuid.UUID,
    db: DbDep,
    current_user: CurrentUserDep,
) -> Response:
    """删除当前用户的抽取表（跨用户返回 404）。"""
    table = await _load_owned_table(db, table_id, current_user.id, with_cells=False)
    await db.delete(table)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
