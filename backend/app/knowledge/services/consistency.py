"""向量库一致性校验（2026-10-22 向量一致性 Task 1）。

## 为什么需要这个模块

实测发现：SQLite 有 104 篇文档 / 28344 个分块 / 28344 条 `vector_index`，
但 Chroma 实际只有 **4** 条向量。`index_status` 全部是 `ready` ——
**数据库在撒谎**。

后果：向量检索对所有用户完全失效，检索退化为 BM25 单路。而这个问题是
「实验跑不出引用标记」才**偶然**追查到的。

**应该有一个可随时运行的检查**，而不是靠碰运气。

## 设计原则

1. **只读**：不修改任何数据，可以随时在线上跑；
2. **快速**：只查 `count()`，不遍历向量；
3. **可操作**：报告里带 `hint`，告诉运维下一步该跑什么命令；
4. **空库不算异常**：新部署/清库后重建前，0 vs 0 是正常的。

## 为什么不逐个比对 id

`count()` 能在毫秒级回答「规模是否一致」。逐个比对 28344 个 id 需要
几十秒且没有额外价值 —— 数量不一致就已经足够触发重建了。

如果数量一致但内容漂移（理论上可能），那是更细的问题，需要抽样校验；
当前规模（几万条）下，重建比精确定位更快。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select

from app.core.logging import get_logger
from app.db.models.knowledge import DocChunk, KnowledgeDoc

logger = get_logger(__name__)

#: 数量不一致时给运维的操作提示。
_REINDEX_HINT = (
    "向量库与数据库不一致。请运行：\n"
    "  python -m scripts.reindex_vectors --dry-run   # 先看规模\n"
    "  python -m scripts.reindex_vectors             # 实际重建"
)


@dataclass
class VectorConsistencyReport:
    """一致性检查结果。

    `missing_vectors` = 期望 - 实际（数据库有、向量库没有）
    `orphan_vectors` = 实际 - 期望（向量库有、数据库没有）
    """

    expected_vectors: int
    actual_vectors: int
    document_count: int
    chunk_count: int
    hint: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def consistent(self) -> bool:
        """数量完全一致才算一致（多和少都是问题）。"""
        return self.expected_vectors == self.actual_vectors

    @property
    def missing_vectors(self) -> int:
        """数据库有、向量库缺的条数（>0 说明检索会漏）。"""
        return max(0, self.expected_vectors - self.actual_vectors)

    @property
    def orphan_vectors(self) -> int:
        """向量库有、数据库没有的条数（孤儿数据，占空间且无法回溯）。"""
        return max(0, self.actual_vectors - self.expected_vectors)

    def as_dict(self) -> dict[str, Any]:
        return {
            "consistent": self.consistent,
            "expected_vectors": self.expected_vectors,
            "actual_vectors": self.actual_vectors,
            "missing_vectors": self.missing_vectors,
            "orphan_vectors": self.orphan_vectors,
            "document_count": self.document_count,
            "chunk_count": self.chunk_count,
            "hint": self.hint,
            "details": self.details,
        }


async def check_vector_consistency(
    *,
    session: Any,
    vector_store: Any,
) -> VectorConsistencyReport:
    """比对「数据库期望的分块数」与「向量库实际条数」。

    参数都可注入（便于测试）：
    - `session`：`AsyncSession`（或提供 `.scalar()` 的替身）；
    - `vector_store`：提供 `.count()` 的向量库（或替身）。
    """
    document_count = int(
        await session.scalar(
            select(func.count())
            .select_from(KnowledgeDoc)
            .where(KnowledgeDoc.index_status == "ready")
        )
        or 0
    )
    # 只统计 ready 文档的分块 —— failed/indexing 的文档本来就不该有向量
    chunk_count = int(
        await session.scalar(
            select(func.count())
            .select_from(DocChunk)
            .join(KnowledgeDoc, KnowledgeDoc.id == DocChunk.doc_id)
            .where(KnowledgeDoc.index_status == "ready")
        )
        or 0
    )

    # 真实接口是 `size()`（见 VectorStore.size）；测试替身也提供同名方法。
    actual_vectors = int(vector_store.size())

    report = VectorConsistencyReport(
        expected_vectors=chunk_count,
        actual_vectors=actual_vectors,
        document_count=document_count,
        chunk_count=chunk_count,
        hint="" if chunk_count == actual_vectors else _REINDEX_HINT,
    )

    if report.consistent:
        logger.info(
            "vector_consistency_ok",
            documents=document_count,
            chunks=chunk_count,
            vectors=actual_vectors,
        )
    else:
        logger.warning(
            "vector_consistency_broken",
            documents=document_count,
            expected=chunk_count,
            actual=actual_vectors,
            missing=report.missing_vectors,
            orphan=report.orphan_vectors,
        )
    return report
