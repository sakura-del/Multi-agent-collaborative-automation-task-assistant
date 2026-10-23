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

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from app.core.logging import get_logger
from app.db.models.knowledge import DocChunk, KnowledgeDoc

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

    def as_dict(self) -> dict[str, Any]:
        return {
            "planned": self.planned,
            "indexed": self.indexed,
            "failed_batches": self.failed_batches,
            "errors": list(self.errors),
        }


async def reindex_from_chunks(
    *,
    session: Any,
    vector_store: Any,
    batch_size: int = DEFAULT_BATCH_SIZE,
    user_id: Any | None = None,
    dry_run: bool = False,
) -> ReindexResult:
    """从 `DocChunk` 重建向量（保持 chunk_id 不变）。

    参数全部可注入（便于测试）：
    - `session`：`AsyncSession`；
    - `vector_store`：需提供 `add_chunks(list[TextChunk])`；
    - `user_id`：只重建某用户的（多租户场景下按需）；
    - `dry_run`：只统计计划数量，不写入。
    """
    from app.knowledge.services.chunker import TextChunk

    stmt = (
        select(DocChunk, KnowledgeDoc.user_id)
        .join(KnowledgeDoc, KnowledgeDoc.id == DocChunk.doc_id)
        .where(KnowledgeDoc.index_status == "ready")
        .order_by(DocChunk.doc_id, DocChunk.chunk_index)
    )
    if user_id is not None:
        stmt = stmt.where(KnowledgeDoc.user_id == user_id)

    rows = (await session.execute(stmt)).all()
    planned = len(rows)

    if dry_run:
        logger.info("reindex_dry_run", planned=planned)
        return ReindexResult(planned=planned, indexed=0)

    indexed = 0
    failed_batches = 0
    errors: list[str] = []

    for start in range(0, planned, batch_size):
        batch = rows[start : start + batch_size]
        chunks: list[Any] = []
        for chunk_row, owner_id in batch:
            chunks.append(
                TextChunk(
                    # **关键**：用原 chunk_id 作为向量库 id，保证回溯能力
                    chunk_id=str(chunk_row.id),
                    doc_id=str(chunk_row.doc_id),
                    text=chunk_row.content,
                    metadata={
                        "user_id": str(owner_id),
                        "chunk_index": chunk_row.chunk_index,
                    },
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
            for chunk_row, _owner in batch:
                await _sync_vector_index(session, str(chunk_row.id))
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

    logger.info(
        "reindex_done",
        planned=planned,
        indexed=indexed,
        failed_batches=failed_batches,
    )
    return ReindexResult(
        planned=planned,
        indexed=indexed,
        failed_batches=failed_batches,
        errors=errors,
    )


async def _sync_vector_index(session: Any, chunk_id: str) -> None:
    """把 `vector_index.chroma_id` 更新为新的向量 id（即 chunk_id）。

    抽成模块级函数而不是方法：`reindex_from_chunks` 是**函数**（不是类），
    上一版注释里写的 `self._sync_vector_index` 是笔误。

    使用 ORM `update()` 构造语句，`session.execute()` 执行。
    测试替身只要提供 `execute()` 即可。
    """
    from sqlalchemy import update

    from app.db.models.knowledge import VectorIndex

    await session.execute(
        update(VectorIndex).where(VectorIndex.chunk_id == chunk_id).values(chroma_id=chunk_id)
    )
