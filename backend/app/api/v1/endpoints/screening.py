"""PRISMA 筛选工作台 HTTP adapter（Phase 2）。

    POST /api/v1/screening/search-logs              记录一次检索（落快照）
    GET  /api/v1/screening/records?task_id=…        列出候选记录
    POST /api/v1/screening/records/{id}/decisions   追加一条筛选决策
    GET  /api/v1/screening/prisma?task_id=…         PRISMA 计数 + 渲染结果
    GET  /api/v1/screening/priority?task_id=…       主动学习优先级排序（只读）

约定：
- 全部要求 Bearer access token；
- 业务在 `ScreeningService`（含事务边界）；本层只做 DTO 映射与**归属校验**；
- 归属不通过一律 404（不用 403，避免泄漏他人任务/记录是否存在）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import HttpUrl
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user
from app.db.models.screening import DecisionStage
from app.db.models.user import User
from app.db.session import get_db
from app.knowledge.services.screening_priority import PriorityUnavailableError
from app.knowledge.services.screening_service import ScreeningService
from app.schemas.screening import (
    DecisionCreate,
    DecisionOut,
    LiteratureRecordIn,
    PrismaOut,
    ScreeningPriorityItemOut,
    ScreeningPriorityLabelsOut,
    ScreeningPriorityOut,
    ScreeningRecordListOut,
    ScreeningRecordOut,
    SearchLogCreate,
    SearchLogOut,
)
from app.tools.literature.models import LiteratureRecord
from app.tools.prisma.flow import render_prisma_markdown, render_prisma_mermaid

router = APIRouter(prefix="/screening", tags=["screening"])

__all__ = ["get_current_active_user", "router"]


def get_screening_service(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ScreeningService:
    return ScreeningService(db)


ServiceDep = Annotated[ScreeningService, Depends(get_screening_service)]
CurrentUserDep = Annotated[User, Depends(get_current_active_user)]


def _bad_request(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)


def _to_literature_record(item: LiteratureRecordIn) -> LiteratureRecord:
    """HTTP DTO → 领域记录。

    ⚠️ **`doi` 必须转成 `https://doi.org/…` 形式的 `source_url`**：
    领域层靠这个 URL 提取 DOI（`_record_doi`），而 DOI 是跨源去重的最高优先级键。
    若这里忽略 `doi`、把 `source_url` 填成与 DOI 无关的地址，
    「同 DOI 不同源」的记录就永远去不掉重，PRISMA 的去重后记录数会虚高。
    """
    if item.doi:
        # 规范化 DOI 链接；DOI 可能含 `/`、`(` 等字符，需转义成合法 URL
        canonical = f"https://doi.org/{quote(item.doi.strip(), safe='/()')}"
    else:
        # 无 DOI 时给一个稳定的占位 URL（领域模型要求 HttpUrl；仅用于去重与展示，
        # 不参与 DOI 判重）
        canonical = f"https://example.invalid/record/{quote(item.source_id, safe='')}"
    url = HttpUrl(canonical)
    return LiteratureRecord(
        source_id=item.source_id,
        title=item.title,
        authors=list(item.authors),
        abstract=item.abstract,
        published_at=datetime.now(UTC),
        source_url=url,
        pdf_url=url,
        categories=[],
    )


def _to_record_out(
    row: object,
    *,
    verdict: str | None = None,
    exclusion_reason: str | None = None,
) -> ScreeningRecordOut:
    """ORM 行 → 响应契约（用显式字段映射，避免意外暴露内部列）。"""
    return ScreeningRecordOut(
        record_id=row.id,  # type: ignore[attr-defined]
        source=row.source,  # type: ignore[attr-defined]
        source_id=row.source_id,  # type: ignore[attr-defined]
        title=row.title,  # type: ignore[attr-defined]
        authors=list(row.authors or []),  # type: ignore[attr-defined]
        abstract=row.abstract,  # type: ignore[attr-defined]
        doi=row.doi,  # type: ignore[attr-defined]
        fulltext_status=row.fulltext_status,  # type: ignore[attr-defined]
        current_stage=row.current_stage,  # type: ignore[attr-defined]
        current_verdict=verdict,
        current_exclusion_reason=exclusion_reason,
    )


@router.post(
    "/search-logs",
    response_model=SearchLogOut,
    status_code=status.HTTP_201_CREATED,
    summary="记录一次检索（落快照）",
)
async def create_search_log(
    payload: SearchLogCreate,
    service: ServiceDep,
    current_user: CurrentUserDep,
) -> SearchLogOut:
    """把一次检索落成可复现日志 + 结果快照（跨源去重）。

    **快照而非实时重查**：上游内容会变，筛选时重查会让 PRISMA 数字无法复现。
    """
    await service.ensure_task_owned(current_user.id, payload.task_id)
    log = await service.record_search(
        payload.task_id,
        query=payload.query,
        sources=list(payload.sources),
        records=[_to_literature_record(item) for item in payload.records],
        hits_by_source=dict(payload.hits_by_source) or None,
        hits_total=payload.hits_total,
    )
    return SearchLogOut(
        search_log_id=log.id,
        task_id=log.task_id,
        query=log.query,
        sources=list(log.sources or []),
        hits_total=log.hits_total,
        hits_by_source=dict(log.hits_by_source or {}),
        hits_deduped=log.hits_deduped,
        executed_at=log.executed_at,
    )


@router.get("/records", response_model=ScreeningRecordListOut, summary="列出候选记录")
async def list_records(
    service: ServiceDep,
    current_user: CurrentUserDep,
    task_id: Annotated[uuid.UUID, Query(description="任务 ID")],
    stage: Annotated[str | None, Query(description="按当前阶段过滤")] = None,
) -> ScreeningRecordListOut:
    await service.ensure_task_owned(current_user.id, task_id)
    stage_enum: DecisionStage | None = None
    if stage:
        try:
            stage_enum = DecisionStage(stage)
        except ValueError as exc:
            raise _bad_request(
                f"未知阶段：{stage}（可选 {', '.join(item.value for item in DecisionStage)}）"
            ) from exc
    records = await service.list_records(task_id, stage=stage_enum)
    # 带上「当前筛选者在本阶段的最新结论」：前端据此隐藏已排除记录的决策按钮
    verdicts = await service.latest_verdicts(records, current_user.id)
    return ScreeningRecordListOut(
        items=[
            _to_record_out(
                row,
                verdict=verdicts[row.id][0] if row.id in verdicts else None,
                exclusion_reason=verdicts[row.id][1] if row.id in verdicts else None,
            )
            for row in records
        ]
    )


@router.post(
    "/records/{record_id}/decisions",
    response_model=DecisionOut,
    status_code=status.HTTP_201_CREATED,
    summary="追加筛选决策",
)
async def create_decision(
    record_id: uuid.UUID,
    payload: DecisionCreate,
    service: ServiceDep,
    current_user: CurrentUserDep,
) -> DecisionOut:
    """追加一条决策（**追加式**：不覆盖历史，保留审计轨迹）。

    `include` 会把记录推进到下一阶段；`exclude` 必须给理由（否则 400）。
    """
    record = await service.get_owned_record(current_user.id, record_id)
    try:
        updated = await service.decide(
            record.id,
            stage=payload.stage,
            verdict=payload.verdict,
            reviewer_id=current_user.id,
            exclusion_reason=payload.exclusion_reason,
            note=payload.note,
        )
    except ValueError as exc:
        # 排除必须给理由等业务规则
        raise _bad_request(str(exc)) from exc
    return DecisionOut(record_id=updated.id, current_stage=updated.current_stage)


@router.get(
    "/priority",
    response_model=ScreeningPriorityOut,
    summary="列出主动学习优先级排序（只读）",
)
async def get_priority(
    service: ServiceDep,
    current_user: CurrentUserDep,
    task_id: Annotated[uuid.UUID, Query(description="任务 ID")],
    stage: Annotated[str, Query(description="排序阶段")] = "title_abstract",
) -> ScreeningPriorityOut:
    """只排序标题/摘要阶段的未筛记录，不自动决策、不改 PRISMA 计数。

    只读端点：不写库、不落决策。排序失败（模型/依赖不可用）映射 503，
    不能伪装成「空结果」——否则前端会把故障显示成「没有推荐」。
    """
    await service.ensure_task_owned(current_user.id, task_id)
    if stage != DecisionStage.TITLE_ABSTRACT.value:
        raise _bad_request("主动排序仅支持 title_abstract 阶段")
    try:
        ranking = await service.priority(task_id)
    except PriorityUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return ScreeningPriorityOut(
        status=ranking.status,
        algorithm=ranking.algorithm,
        stage=stage,
        items=[
            ScreeningPriorityItemOut(record_id=uuid.UUID(item.record_id), rank=item.rank)
            for item in ranking.items
        ],
        labels=ScreeningPriorityLabelsOut(
            positive=ranking.positive,
            negative=ranking.negative,
            ignored=ranking.ignored,
        ),
        candidates=ranking.candidates,
        message=ranking.message,
    )


@router.get("/prisma", response_model=PrismaOut, summary="PRISMA 计数与流程图")
async def get_prisma(
    service: ServiceDep,
    current_user: CurrentUserDep,
    task_id: Annotated[uuid.UUID, Query(description="任务 ID")],
    language: Annotated[str, Query(pattern="^(zh|en)$")] = "zh",
) -> PrismaOut:
    """派生 PRISMA 2020 各阶段计数，并给出可直接进论文的 Markdown / Mermaid。

    计数不落库、每次现算：汇总数字与明细分开存储必然漂移，
    而这些数字是要写进论文的，必须永远与明细一致。
    """
    await service.ensure_task_owned(current_user.id, task_id)
    counts = await service.prisma_counts(task_id)
    return PrismaOut(
        **counts,
        markdown=render_prisma_markdown(counts, language=language),
        mermaid=render_prisma_mermaid(counts, language=language),
    )
