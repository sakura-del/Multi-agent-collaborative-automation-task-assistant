"""文献元数据 → 证据（2026-10-26 引用库遗留计划 §3.1/§3.2）。

## 为什么需要这个模块

取证（计划 §1.1）：证据只从用户**知识库**构造 ——

    extract/write → LiteratureContextBuilder.build()
                  → knowledge.search_for_context(user_id, query)

而文献源（引用库 / arXiv）检索到的记录走的是**另一条线**：

    search → self._source.search() → artifacts

两条线互不相干。后果：引用库任务即使检索到 20 篇文献，
``extract``/``write`` 节点的 ``evidence`` 仍然为空 ——
写作 Agent 在没有任何证据的情况下生成内容。

## 为什么不能直接用摘要

CSL 条目里没有摘要字段，``csl_item_to_record()`` 把 ``abstract`` 写成 ``""``
是**故意的**：编一个摘要就是造假。

但检索记录里有**真实可用**的字段：题名、作者、年份、URL。它们足以构造一条
**可核验的书目元数据证据** —— 能支撑「存在这样一篇文献」，不能支撑
「作者指出……」。所以证据带 ``kind="metadata"`` 显式标注，下游不得混用。
"""

from __future__ import annotations

from app.orchestration.contracts import Evidence
from app.tools.literature.models import LiteratureRecord

MAX_METADATA_EXCERPT_CHARS = 2000
"""与 ``Evidence.excerpt`` 的 ``max_length`` 对齐，避免超长题名撑爆上下文。"""

MAX_LISTED_AUTHORS = 3
"""最多列三位作者，其余用「等」收尾 —— 整份作者名单塞进证据没有信息增量。"""


def metadata_excerpt(record: LiteratureRecord) -> str:
    """把文献元数据压成一句**可核验**的陈述。

    刻意不出现「原文」「引述」「摘录」「作者指出」这类措辞 ——
    我们没有原文，任何暗示有原文的措辞都会让下游误判证据强度。
    """
    authors = "、".join(record.authors[:MAX_LISTED_AUTHORS])
    if len(record.authors) > MAX_LISTED_AUTHORS:
        authors = f"{authors} 等"
    if not authors:
        # 没有作者就如实写「佚名」，不编一个
        authors = "佚名"
    year = record.published_at.year
    text = f"《{record.title}》— {authors}，{year}，{record.source_url}"
    return text[:MAX_METADATA_EXCERPT_CHARS]


def metadata_evidence(record: LiteratureRecord) -> Evidence:
    """把一条文献记录转成 ``kind="metadata"`` 的证据。

    ``doc_id`` / ``chunk_id`` 保持 ``None``：元数据证据不绑定知识库分块，
    填一个假的 UUID 会让「可回溯」变成假话。
    """
    return Evidence(
        doc_id=None,
        chunk_id=None,
        source_url=record.source_url,
        excerpt=metadata_excerpt(record),
        kind="metadata",
    )


__all__ = [
    "MAX_LISTED_AUTHORS",
    "MAX_METADATA_EXCERPT_CHARS",
    "metadata_evidence",
    "metadata_excerpt",
]
