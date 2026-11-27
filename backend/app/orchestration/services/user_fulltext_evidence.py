"""用户已关联全文 → 带页码 locator 的 excerpt 证据（Issue #73 Phase B / Task B2）。

为什么需要这一层：Phase A 的 OA 全文抓取与 Task B1 的知识库上传都会把正文挂进
`knowledge_doc`（`meta_info.doi` 指回检索记录的 DOI），但写作节点此前只拿得到
检索命中的 **metadata** 证据（「存在这样一篇文献」），拿不到「作者指出……」的
原文片段。本服务按检索记录的 DOI（优先）/标题反查**当前用户** ready 的全文分块，
把证据升级成带页码/章节 locator 的 excerpt。

边界（计划 Task B2）：

- 纯 DB 读取，不产生任何外部调用（离线任务零外呼不受影响）；
- 只认 `user_id` 是当前用户、`index_status == "ready"` 的文档 —— 半成品索引的
  分块可能缺失/错位，宁可不给证据；
- 每篇默认最多 2 条（`max_chunks_per_paper` 可配），优先 `result` / `method`
  章节，再按 `chunk_index`；
- 「元数据过滤」放在 Python 侧（不写 JSON 表达式）：SQLite（测试）与
  PostgreSQL（生产）的 JSON 访问语法不同，而单用户的文档量很小，内存过滤足够；
- 标题是弱标识：归一化后短于 20 字符、或同名文档不止一篇时**不关联** ——
  关联错了比不关联更糟（与 Task B1 `ReferenceLinker` 同一取舍）。
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.knowledge import DocChunk, KnowledgeDoc
from app.knowledge.services.document_service import extract_locator_metadata
from app.knowledge.services.reference_library_service import normalize_doi
from app.knowledge.services.reference_linker import doi_from_ref_id, normalize_title
from app.orchestration.contracts import Evidence, EvidenceLocator
from app.tools.literature.models import LiteratureRecord

__all__ = ["UserFulltextEvidenceService", "record_doi"]

#: 单条证据的正文上限 = `Evidence.excerpt` 的契约上限（2000 字符）。
_MAX_EXCERPT_CHARS = 2000

#: 优先使用的章节角色：结果/方法最能支撑「作者指出……」。
_PREFERRED_SECTION_ROLES = frozenset({"result", "method"})

#: `https://doi.org/<DOI>` 形态的 source_url（引用库来源的兜底 DOI 提取）。
_DOI_URL_RE = re.compile(r"^https?://(?:dx\.)?doi\.org/(.+)$", re.IGNORECASE)


def _doi_from_source_url(source_url: Any) -> str | None:
    """从 `source_url` 提取 DOI；不是 doi.org 形态返回 None。"""
    if source_url is None:
        return None
    match = _DOI_URL_RE.match(str(source_url).strip())
    if match is None:
        return None
    return normalize_doi(match.group(1))


def record_doi(record: LiteratureRecord) -> str | None:
    """检索记录 → 归一化 DOI。

    `source_id` 的 `doi:` 形态最可靠（引用库 `CslItem.id` / 检索源构造的
    `ref_id` 都遵循这个约定）；否则退到 `https://doi.org/<DOI>` 的 source_url。
    """
    return doi_from_ref_id(record.source_id) or _doi_from_source_url(record.source_url)


def _meta_of(doc: KnowledgeDoc) -> dict[str, Any]:
    meta = doc.meta_info
    return meta if isinstance(meta, dict) else {}


def _doc_doi(doc: KnowledgeDoc) -> str | None:
    raw = _meta_of(doc).get("doi")
    return normalize_doi(raw) if isinstance(raw, str) else None


def _section_role(chunk: DocChunk) -> str | None:
    meta = chunk.chunk_meta
    if not isinstance(meta, dict):
        return None
    role = meta.get("section_role")
    return role.lower() if isinstance(role, str) else None


class UserFulltextEvidenceService:
    """按检索记录反查用户已关联全文，产出 excerpt 证据（带页码 locator）。"""

    def __init__(self, db: AsyncSession, *, max_chunks_per_paper: int = 2) -> None:
        self._db = db
        self._max_chunks_per_paper = max(0, int(max_chunks_per_paper))

    async def evidence_for(self, user_id: UUID, records: list[LiteratureRecord]) -> list[Evidence]:
        """返回命中用户 ready 全文的 excerpt 证据；无命中返回空列表。

        调用方（`LiteratureReviewWorkflow._prepare_node`）在空列表时继续使用
        metadata 证据，因此这里**不抛异常、不返回占位证据**。
        """
        if not records or self._max_chunks_per_paper == 0:
            return []
        docs = await self._ready_docs(user_id)
        if not docs:
            return []

        by_doi: dict[str, KnowledgeDoc] = {}
        titles: list[tuple[str, KnowledgeDoc]] = []
        for doc in docs:
            doi = _doc_doi(doc)
            if doi is not None and doi not in by_doi:
                by_doi[doi] = doc
            title_key = normalize_title(doc.title)
            if title_key is not None:
                titles.append((title_key, doc))

        evidence: list[Evidence] = []
        seen_docs: set[UUID] = set()
        for record in records:
            matched_doc = self._match(record, by_doi=by_doi, titles=titles)
            if matched_doc is None or matched_doc.id in seen_docs:
                continue
            seen_docs.add(matched_doc.id)
            evidence.extend(await self._evidence_for_doc(matched_doc))
        return evidence

    # ===== 内部 =====

    async def _ready_docs(self, user_id: UUID) -> list[KnowledgeDoc]:
        """当前用户、索引已就绪的文档（`index_status` 在内存过滤，兼容两种方言）。"""
        result = await self._db.execute(select(KnowledgeDoc).where(KnowledgeDoc.user_id == user_id))
        return [doc for doc in result.scalars().all() if doc.index_status == "ready"]

    def _match(
        self,
        record: LiteratureRecord,
        *,
        by_doi: dict[str, KnowledgeDoc],
        titles: list[tuple[str, KnowledgeDoc]],
    ) -> KnowledgeDoc | None:
        """DOI 精确 → 标题归一（唯一候选才生效）。"""
        doi = record_doi(record)
        if doi is not None:
            doc = by_doi.get(doi)
            if doc is not None:
                return doc
        title_key = normalize_title(record.title)
        if title_key is not None:
            candidates = [doc for key, doc in titles if key == title_key]
            if len(candidates) == 1:
                return candidates[0]
        return None

    async def _evidence_for_doc(self, doc: KnowledgeDoc) -> list[Evidence]:
        """取文档分块 → 排序裁剪 → 组装 Evidence（locator 来自 position/chunk_meta）。"""
        result = await self._db.execute(
            select(DocChunk).where(DocChunk.doc_id == doc.id).order_by(DocChunk.chunk_index)
        )
        chunks = list(result.scalars().all())
        if not chunks:
            return []
        preferred = [chunk for chunk in chunks if _section_role(chunk) in _PREFERRED_SECTION_ROLES]
        others = [chunk for chunk in chunks if _section_role(chunk) not in _PREFERRED_SECTION_ROLES]
        selected = (preferred + others)[: self._max_chunks_per_paper]

        items: list[Evidence] = []
        for chunk in selected:
            content = str(chunk.content or "").strip()
            if not content:
                continue
            locator_meta = extract_locator_metadata(chunk.position, chunk.chunk_meta)
            items.append(
                Evidence(
                    doc_id=doc.id,
                    chunk_id=chunk.id,
                    doc_title=doc.title or None,
                    excerpt=content[:_MAX_EXCERPT_CHARS],
                    kind="excerpt",
                    locator=EvidenceLocator(**locator_meta) if locator_meta else None,
                )
            )
        return items
