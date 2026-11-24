"""按 DocChunk 重建向量索引（2026-10-22 向量一致性 Task 2）。

## 为什么不能用 `IndexManager.rebuild_all()`

它按**文件路径**重新解析文档：

```python
self.clear()                    # 清空 Chroma
for source in sources:
    self.add_document(source)   # 重新分块 → 生成**新的** chunk id
```

后果：向量库 id 与 SQLite `doc_chunk.id` **不一致** →
`KnowledgeDocumentService._attach_storage_ids()` 无法把检索结果回溯到存储行 →
**引用追溯断裂**（本项目的核心能力：每条证据要能点回原文）。

## 正确做法

从 SQLite 读 `DocChunk.content`（内容已在库，**不需重新解析文件**），
用**原 chunk_id** 作为向量库 id 写入。

这样 `vector_index.chroma_id` 映射继续有效，回溯不断。

## 分批与容错

- **分批 500 条**：28344 条不能一次全塞（内存 + 单次 HTTP 请求体过大）；
- **单批失败不中断**：一条坏数据不该让 28344 条全废。记录错误，继续下一批，
  最后汇总。运维可重复运行（幂等 upsert）。
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import String, cast, select, update

from app.core.logging import get_logger
from app.db.models.knowledge import DocChunk, KnowledgeDoc, VectorIndex
from app.knowledge.services.document_service import extract_locator_metadata

logger = get_logger(__name__)

#: 默认每批条数。500 条 × 约 500 字 ≈ 250KB，单次请求体安全。
DEFAULT_BATCH_SIZE = 500


@dataclass
class ReindexResult:
    """重建结果。"""

    planned: int
    indexed: int
    failed_batches: int = 0
    errors: list[str] = field(default_factory=list)
    #: 对账时发现的孤儿数与实际删除数；普通全量模式始终为 0
    orphans_present: int = 0
    deleted_orphans: int = 0
    #: 端到端耗时（毫秒）：远程库上「网络往返」是主要成本，必须可观测
    elapsed_ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "planned": self.planned,
            "indexed": self.indexed,
            "failed_batches": self.failed_batches,
            "errors": list(self.errors),
            "orphans_present": self.orphans_present,
            "deleted_orphans": self.deleted_orphans,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass
class BackfillLocatorResult:
    """存量分块定位元数据回填结果（保持 chunk_id 与分块数量不变）。"""

    docs_scanned: int
    docs_updated: int
    chunks_updated: int
    skipped_missing_files: int = 0


def _locate_chunk_offsets(full_text: str, content: str, cursor: int) -> tuple[int, int] | None:
    """在全文中反查既有分块的字符区间，允许重叠窗口但绝不重切分块。"""
    if not full_text or not content:
        return None
    idx = full_text.find(content, max(0, cursor))
    if idx < 0:
        idx = full_text.find(content)
    if idx >= 0:
        return idx, idx + len(content)
    anchor = content[:40].strip()
    if len(anchor) >= 8:
        idx = full_text.find(anchor, max(0, cursor))
        if idx < 0:
            idx = full_text.find(anchor)
        if idx >= 0:
            return idx, min(len(full_text), idx + len(content))
    return None


@asynccontextmanager
async def _open_local_doc(file_path: str, storage: Any | None) -> AsyncIterator[Path | None]:
    """优先走受控存储 localize()，无 storage 时直接读取本地路径。"""
    if storage is not None:
        try:
            async with storage.localize(file_path) as local_path:
                resolved = Path(local_path)
                yield resolved if resolved.is_file() else None
                return
        except FileNotFoundError:
            yield None
            return
    local = Path(file_path)
    yield local if local.is_file() else None


async def backfill_chunk_locators(
    *,
    session: Any,
    storage: Any | None = None,
    user_id: Any | None = None,
    doc_id: Any | None = None,
    dry_run: bool = False,
) -> BackfillLocatorResult:
    """为存量 `DocChunk` 补回页码/章节/图表定位字段，保持原 `chunk_id` 与分块数不变。"""
    from app.knowledge.services.chunker import _index_section_spans, _resolve_chunk_locator
    from app.knowledge.services.document_parser import DocumentParser

    stmt = (
        select(KnowledgeDoc)
        .where(KnowledgeDoc.index_status == "ready")
        .order_by(KnowledgeDoc.created_at, KnowledgeDoc.id)
    )
    if user_id is not None:
        stmt = stmt.where(KnowledgeDoc.user_id == user_id)
    if doc_id is not None:
        stmt = stmt.where(KnowledgeDoc.id == doc_id)

    docs = (await session.execute(stmt)).scalars().all()
    parser = DocumentParser()
    docs_scanned = 0
    docs_updated = 0
    chunks_updated = 0
    skipped_missing_files = 0

    for doc in docs:
        docs_scanned += 1
        if not doc.file_path:
            skipped_missing_files += 1
            continue
        chunk_rows = (
            (
                await session.execute(
                    select(DocChunk).where(DocChunk.doc_id == doc.id).order_by(DocChunk.chunk_index)
                )
            )
            .scalars()
            .all()
        )
        if not chunk_rows:
            continue

        async with _open_local_doc(doc.file_path, storage) as local_path:
            if local_path is None:
                skipped_missing_files += 1
                continue
            parsed = parser.parse(str(local_path), doc_id=str(doc.id))

        spans = _index_section_spans(parsed)
        cursor = 0
        doc_changed = False
        for chunk_row in chunk_rows:
            existing_pos = chunk_row.position if isinstance(chunk_row.position, dict) else {}
            raw_start = existing_pos.get("start_offset")
            raw_end = existing_pos.get("end_offset")
            if (
                isinstance(raw_start, int)
                and not isinstance(raw_start, bool)
                and isinstance(raw_end, int)
                and not isinstance(raw_end, bool)
                and 0 <= raw_start < raw_end <= len(parsed.text)
            ):
                start, end = raw_start, raw_end
                cursor = max(cursor, start + 1)
            else:
                located = _locate_chunk_offsets(parsed.text, chunk_row.content, cursor)
                if located is None:
                    start, end = 0, 0
                else:
                    start, end = located
                    cursor = max(cursor, start + 1)

            locator_meta = _resolve_chunk_locator(chunk_row.content, start, end, spans)
            new_pos: dict[str, Any] = {**existing_pos, "start_offset": start, "end_offset": end}
            if "page_start" in locator_meta:
                new_pos["page_start"] = locator_meta["page_start"]
                new_pos["page_end"] = locator_meta.get("page_end", locator_meta["page_start"])

            existing_meta = (
                dict(chunk_row.chunk_meta) if isinstance(chunk_row.chunk_meta, dict) else {}
            )
            if locator_meta.get("section_title"):
                existing_meta["section_title"] = locator_meta["section_title"]
            if locator_meta.get("table_ref"):
                existing_meta["table_ref"] = locator_meta["table_ref"]
            new_meta: dict[str, Any] | None = existing_meta or None

            if new_pos != chunk_row.position or new_meta != chunk_row.chunk_meta:
                chunks_updated += 1
                doc_changed = True
                if not dry_run:
                    chunk_row.position = new_pos
                    chunk_row.chunk_meta = new_meta

        if doc_changed:
            docs_updated += 1

    if chunks_updated and not dry_run:
        commit = getattr(session, "commit", None)
        if commit is not None:
            await commit()

    logger.info(
        "chunk_locators_backfilled",
        docs_scanned=docs_scanned,
        docs_updated=docs_updated,
        chunks_updated=chunks_updated,
        skipped_missing_files=skipped_missing_files,
        dry_run=dry_run,
    )
    return BackfillLocatorResult(
        docs_scanned=docs_scanned,
        docs_updated=docs_updated,
        chunks_updated=chunks_updated,
        skipped_missing_files=skipped_missing_files,
    )


async def reindex_from_chunks(
    *,
    session: Any,
    vector_store: Any,
    batch_size: int = DEFAULT_BATCH_SIZE,
    user_id: Any | None = None,
    dry_run: bool = False,
    limit: int | None = None,
    reconcile: bool = False,
    backfill_locators: bool = False,
    storage: Any | None = None,
) -> ReindexResult:
    """从 `DocChunk` 重建向量（保持 chunk_id 不变）。

    参数全部可注入（便于测试）：
    - `session`：`AsyncSession`；
    - `vector_store`：需提供 `add_chunks(list[TextChunk])`；
    - `user_id`：只重建某用户的（多租户场景下按需）；
    - `dry_run`：只统计计划数量，不写入；
    - `reconcile`：只补目标库缺失 ID，并在缺项全部成功后删除孤儿；
      普通模式仍全量 upsert，不删除任何向量；
    - `backfill_locators`：先从原文件回填存量分块 `position` / `chunk_meta` 定位，再写向量。
    """
    from app.knowledge.services.chunker import TextChunk

    if backfill_locators:
        await backfill_chunk_locators(
            session=session,
            storage=storage,
            user_id=user_id,
            dry_run=dry_run,
        )

    stmt = (
        select(DocChunk, KnowledgeDoc.user_id)
        .join(KnowledgeDoc, KnowledgeDoc.id == DocChunk.doc_id)
        .where(KnowledgeDoc.index_status == "ready")
        .order_by(DocChunk.doc_id, DocChunk.chunk_index)
    )
    if user_id is not None:
        stmt = stmt.where(KnowledgeDoc.user_id == user_id)

    rows = (await session.execute(stmt)).all()
    orphan_ids: list[str] = []
    if reconcile:
        # 影子表后台重建期间，原表仍可能新增/删除文档。只补缺失 ID，
        # 已同步的 ID 不再生成嵌入；孤儿在缺项全部成功后才删除。
        current_ids = {str(row.id) for row, _owner in rows}
        store_ids = set(vector_store.list_ids())
        missing_ids = current_ids - store_ids
        orphan_ids = sorted(store_ids - current_ids)
        rows = [row for row in rows if str(row[0].id) in missing_ids]

    # `limit` 只用于远程环境的小样本演练；0/None 都表示不限制
    if limit:
        rows = rows[: max(0, int(limit))]
    planned = len(rows)

    if dry_run:
        logger.info(
            "reindex_dry_run",
            planned=planned,
            orphans=len(orphan_ids),
            reconcile=reconcile,
        )
        return ReindexResult(
            planned=planned,
            indexed=0,
            orphans_present=len(orphan_ids),
        )

    indexed = 0
    failed_batches = 0
    errors: list[str] = []
    started_at = time.perf_counter()

    for start in range(0, planned, batch_size):
        batch = rows[start : start + batch_size]
        chunks: list[Any] = []
        for chunk_row, owner_id in batch:
            meta: dict[str, Any] = {
                "user_id": str(owner_id),
                "chunk_index": chunk_row.chunk_index,
            }
            meta.update(
                extract_locator_metadata(
                    getattr(chunk_row, "position", None),
                    getattr(chunk_row, "chunk_meta", None),
                )
            )
            chunks.append(
                TextChunk(
                    # **关键**：用原 chunk_id 作为向量库 id，保证回溯能力
                    chunk_id=str(chunk_row.id),
                    doc_id=str(chunk_row.doc_id),
                    text=chunk_row.content,
                    metadata=meta,
                )
            )
        try:
            vector_store.add_chunks(chunks)
            indexed += len(chunks)
            # **关键**：同步更新 vector_index.chroma_id
            #
            # 向量库里的 id 现在是 chunk_id（UUID），但 `vector_index.chroma_id`
            # 仍是建库时生成的 16 位 hex —— 不更新的话该表变成**死数据**，
            # 任何按 chroma_id 反查的逻辑都会失败。
            #
            # 为什么之前漏了：`_attach_storage_ids` 里「结果 id 已是 UUID →
            # 原样保留」，所以检索恰好还能工作，掩盖了这个不一致。
            #
            # **必须批量**：曾写成逐条 `_sync_vector_index`，28341 条在
            # Supabase 上就是 28341 × 0.48s ≈ 3.8 小时（本地库 2ms 时看不出来）。
            await _sync_vector_index_batch(session, [row.id for row, _owner in batch])
            logger.info(
                "reindex_batch",
                batch=start // batch_size + 1,
                total_batches=(planned + batch_size - 1) // batch_size,
                indexed=indexed,
                planned=planned,
            )
        except Exception as exc:
            # 单批失败不中断：记录并继续，运维可重复运行（upsert 幂等）
            failed_batches += 1
            message = f"批次 {start // batch_size + 1} 失败：{type(exc).__name__}: {exc}"
            errors.append(message)
            logger.error("reindex_batch_failed", batch=start // batch_size + 1, error=str(exc))

    if indexed:
        # 一次性提交（逐条 flush 太慢；28344 条只提交一次）
        commit = getattr(session, "commit", None)
        if commit is not None:
            await commit()

    deleted_orphans = 0
    if reconcile and failed_batches == 0 and orphan_ids:
        deleted_orphans = int(vector_store.remove_chunks(orphan_ids))
        logger.info(
            "reindex_orphans_deleted",
            planned=len(orphan_ids),
            deleted=deleted_orphans,
        )

    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
    logger.info(
        "reindex_done",
        planned=planned,
        indexed=indexed,
        failed_batches=failed_batches,
        orphans=len(orphan_ids),
        deleted_orphans=deleted_orphans,
        reconcile=reconcile,
        elapsed_ms=elapsed_ms,
    )
    return ReindexResult(
        planned=planned,
        indexed=indexed,
        failed_batches=failed_batches,
        errors=errors,
        orphans_present=len(orphan_ids),
        deleted_orphans=deleted_orphans,
        elapsed_ms=elapsed_ms,
    )


async def _sync_vector_index_batch(session: Any, chunk_ids: Sequence[Any]) -> None:
    """把**一批** chunk 的 `vector_index.chroma_id` 同步为 `chunk_id`（一条 SQL）。

    为什么批量：逐条更新在远程库上是「每行一次往返」——28341 条 × 0.48s ≈ 3.8 小时。
    批量后每批一条，57 批 ≈ 57 次往返。

    两个类型细节（PostgreSQL 上不写就报错）：

    - `chunk_id` 是 `uuid` 列，**必须传 UUID 对象**（传 str 会得到
      `operator does not exist: uuid = text`；SQLite 下看不出来）；
    - `chroma_id` 是 `varchar(64)`，值是列引用时必须显式
      `cast(chunk_id AS varchar)`，否则报
      `column "chroma_id" is of type character varying but expression is of type uuid`。

    语义等价于原来的逐条 `values(chroma_id=str(chunk_id))`：两者都满足
    「向量库 id 就是 chunk_id」。
    """
    ids = list(chunk_ids)
    if not ids:
        return
    await session.execute(
        update(VectorIndex)
        .where(VectorIndex.chunk_id.in_(ids))
        .values(chroma_id=cast(VectorIndex.chunk_id, String))
    )
