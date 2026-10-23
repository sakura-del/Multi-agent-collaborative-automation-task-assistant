"""向量库一致性校验（2026-10-22 建立，2026-10-23 补 id 级校验）。

## 为什么需要这个模块

实测发现：SQLite 有 104 篇文档 / 28344 个分块 / 28344 条 `vector_index`，
但 Chroma 实际只有 **4** 条向量。`index_status` 全部是 `ready` ——
**数据库在撒谎**。

后果：向量检索对所有用户完全失效，检索退化为 BM25 单路。而这个问题是
「实验跑不出引用标记」才**偶然**追查到的。

## 为什么必须做 id 级校验（2026-10-23 补）

首版只比对**数量**。实测踩到最危险的一类漂移：

| 项 | 值 |
| --- | --- |
| Chroma 实际 id | UUID（28344 条） |
| `vector_index.chroma_id` | 16 位 hex（28344 条） |
| 交集 | **0** |
| 数量差 | **0** |

数量完全相等 → 首版报「一致」。**数量对得上，数据全错。**

所以默认 `verify_ids=True`：拉全量 id 做集合比对。28344 条拉取约 2-5 秒，
运维命令完全可接受；只有明确要「快速探活」时才用 `verify_ids=False`。

## 设计原则

1. **只读**：不修改任何数据，可随时在线上跑；
2. **可操作**：报告带 `hint`，告诉运维下一步跑什么命令；
3. **空库不算异常**：新部署/清库后重建前，0 vs 0 是正常的；
4. **id 样本有上限**：避免 28344 条全塞进日志。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select

from app.core.logging import get_logger
from app.db.models.knowledge import DocChunk, KnowledgeDoc, VectorIndex

logger = get_logger(__name__)

#: 数量或 id 不一致时给运维的操作提示。
_REINDEX_HINT = (
    "向量库与数据库不一致。请运行：\n"
    "  python -m scripts.reindex_vectors --dry-run   # 先看规模\n"
    "  python -m scripts.reindex_vectors             # 实际重建"
)

#: 报告里 id 样本的条数上限（防止 28344 条撑爆日志）。
ID_SAMPLE_LIMIT = 20


@dataclass
class VectorConsistencyReport:
    """一致性检查结果。

    `missing_vectors` = 期望 - 实际（数据库有、向量库没有）
    `orphan_vectors` = 实际 - 期望（向量库有、数据库没有）

    开启 `verify_ids` 时，这两个数字改为 **id 集合差集** 的大小
    （更准确：数量相等但 id 不同时也能报出来）。
    """

    expected_vectors: int
    actual_vectors: int
    document_count: int
    chunk_count: int
    hint: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    #: id 级校验结果（verify_ids=False 时为空）
    missing_ids: list[str] = field(default_factory=list)
    orphan_ids: list[str] = field(default_factory=list)
    ids_verified: bool = False

    @property
    def consistent(self) -> bool:
        """数量与 id 集合都一致才算一致。"""
        if self.expected_vectors != self.actual_vectors:
            return False
        if self.ids_verified:
            return not self.missing_ids and not self.orphan_ids
        return True

    @property
    def missing_vectors(self) -> int:
        """数据库有、向量库缺的条数（>0 说明检索会漏）。"""
        if self.ids_verified:
            return int(self.details.get("missing_id_count", len(self.missing_ids)))
        return max(0, self.expected_vectors - self.actual_vectors)

    @property
    def orphan_vectors(self) -> int:
        """向量库有、数据库没有的条数（孤儿数据，占空间且无法回溯）。"""
        if self.ids_verified:
            return int(self.details.get("orphan_id_count", len(self.orphan_ids)))
        return max(0, self.actual_vectors - self.expected_vectors)

    def as_dict(self) -> dict[str, Any]:
        return {
            "consistent": self.consistent,
            "expected_vectors": self.expected_vectors,
            "actual_vectors": self.actual_vectors,
            "missing_vectors": self.missing_vectors,
            "orphan_vectors": self.orphan_vectors,
            "missing_ids": list(self.missing_ids),
            "orphan_ids": list(self.orphan_ids),
            "ids_verified": self.ids_verified,
            "document_count": self.document_count,
            "chunk_count": self.chunk_count,
            "hint": self.hint,
            "details": self.details,
        }


async def _db_chroma_ids(session: Any) -> list[str]:
    """取 `vector_index.chroma_id` 全量。"""
    rows = (await session.execute(select(VectorIndex.chroma_id))).all()
    return [str(row[0]) for row in rows]


def _store_ids(vector_store: Any) -> list[str]:
    """取向量库全量 id（优先 `list_ids()`，回退到 Chroma collection）。"""
    lister = getattr(vector_store, "list_ids", None)
    if callable(lister):
        return [str(i) for i in lister()]
    collection = getattr(vector_store, "_collection", None)
    if collection is not None:
        payload = collection.get(include=[])
        return [str(i) for i in (payload.get("ids") or [])]
    return []


async def check_vector_consistency(
    *,
    session: Any,
    vector_store: Any,
    verify_ids: bool = True,
) -> VectorConsistencyReport:
    """比对「数据库期望的分块数」与「向量库实际条数」（可含 id 级比对）。

    参数都可注入（便于测试）：
    - `session`：`AsyncSession`（或提供 `.scalar()` / `.execute()` 的替身）；
    - `vector_store`：提供 `.size` 与 `.list_ids()` 的向量库（或替身）；
    - `verify_ids`：是否做 id 集合比对（**默认 True**；设为 False 只比数量）。
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

    # `VectorStore.size` 是**属性**（见 index_manager.get_stats 的用法）。
    # 测试替身可以给属性或方法，这里兼容两种写法。
    raw_size = getattr(vector_store, "size", 0)
    actual_vectors = int(raw_size() if callable(raw_size) else raw_size)

    report = VectorConsistencyReport(
        expected_vectors=chunk_count,
        actual_vectors=actual_vectors,
        document_count=document_count,
        chunk_count=chunk_count,
    )

    if verify_ids:
        db_ids = await _db_chroma_ids(session)
        store_ids = _store_ids(vector_store)
        db_set = set(db_ids)
        store_set = set(store_ids)
        missing = db_set - store_set
        orphan = store_set - db_set
        report.ids_verified = True
        report.missing_ids = sorted(missing)[:ID_SAMPLE_LIMIT]
        report.orphan_ids = sorted(orphan)[:ID_SAMPLE_LIMIT]
        report.details["missing_id_count"] = len(missing)
        report.details["orphan_id_count"] = len(orphan)

    report.hint = "" if report.consistent else _REINDEX_HINT

    if report.consistent:
        logger.info(
            "vector_consistency_ok",
            documents=document_count,
            chunks=chunk_count,
            vectors=actual_vectors,
            ids_verified=report.ids_verified,
        )
    else:
        logger.warning(
            "vector_consistency_broken",
            documents=document_count,
            expected=chunk_count,
            actual=actual_vectors,
            missing=report.missing_vectors,
            orphan=report.orphan_vectors,
            ids_verified=report.ids_verified,
        )
    return report
