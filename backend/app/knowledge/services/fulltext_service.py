"""全文获取服务：缓存落库 + 用户知识库挂载 + 统计。

职责边界（Phase A Task 5）：

- 已抓取并解析过的 DOI / arXiv / PMID 直接读 ``fulltext_cache``，不重复下载；
- 命中（或新抓）的全文挂载成**当前用户**的知识库文档（``knowledge_doc`` +
  ``doc_chunk`` + ``vector_index``），章节标题与页码写进 chunk 元数据，供检索
  结果做定位引用；
- 单篇失败只记日志与计数，绝不让整批检索失败（失败降级为元数据证据）。

不负责：网络抓取（``FullTextResolver``）、PDF/HTML/JATS 解析
（``CleanFullTextParser``）、论文卡片生成（``PaperCardService``，Task 6 提供实现）。
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models.fulltext_cache import FulltextCache
from app.db.models.knowledge import DocChunk, KnowledgeDoc, VectorIndex
from app.knowledge.models.document import TextChunk
from app.knowledge.services.document_service import KnowledgeDocumentService
from app.knowledge.services.document_storage import DocumentStorage, StoredDocument
from app.knowledge.services.fulltext_parser import (
    CleanFullTextParser,
    ParsedSection,
    ParseResult,
    ParseStatus,
)
from app.tools.literature.fulltext_resolver import (
    FullTextCandidate,
    FullTextResolver,
    _record_arxiv_id,
    _record_doi,
    _record_pmid,
)
from app.tools.literature.models import LiteratureRecord

logger = get_logger(__name__)

#: 挂载出的知识库文档来源标记（与普通上传的 ``upload`` 区分）。
_SOURCE = "fulltext_oa"
#: ``meta_info`` 里的来源标记：OA 全文自动获取。
_ORIGIN = "oa_auto"

_DOC_TYPE_BY_FORMAT: dict[str, str] = {"pdf": "pdf", "html": "html", "jats_xml": "xml"}
_EXTENSION_BY_FORMAT: dict[str, str] = {"pdf": "pdf", "html": "html", "jats_xml": "xml"}


class PaperCardProvider(Protocol):
    """论文卡片生成的最小接口。

    Task 6 会提供 ``PaperCardService`` 的具体实现；这里用 Protocol 收窄依赖，
    让本 Task 的单测可以直接传替身，也避免提前把 Task 6 的代码写进来。
    """

    async def get_or_create(
        self,
        *,
        abstract: str,
        sections: list[dict[str, Any]],
    ) -> dict[str, str] | None: ...


@dataclass(slots=True)
class FulltextStats:
    """一次全文获取的计数汇总。

    ``as_dict()`` 的键顺序即对外统计契约（写入任务 ``stats["fulltext_summary"]``）。
    """

    attempted: int = 0
    succeeded: int = 0
    fallback_metadata: int = 0
    scanned_skipped: int = 0
    failed: int = 0

    def as_dict(self) -> dict[str, int]:
        """返回可直接落库/上报的扁平计数。"""
        return {
            "attempted": self.attempted,
            "succeeded": self.succeeded,
            "fallback_metadata": self.fallback_metadata,
            "scanned_skipped": self.scanned_skipped,
            "failed": self.failed,
        }


class FulltextService:
    """把 OA 全文落进 ``fulltext_cache``，并按用户挂载到知识库。"""

    def __init__(
        self,
        db: AsyncSession,
        *,
        resolver: FullTextResolver,
        parser: CleanFullTextParser,
        documents: KnowledgeDocumentService,
        storage: DocumentStorage,
        paper_cards: PaperCardProvider,
    ) -> None:
        self._db = db
        self._resolver = resolver
        self._parser = parser
        self._documents = documents
        self._storage = storage
        self._paper_cards = paper_cards
        self._settings = settings.fulltext
        # 挂载文档与 ingest() 保持同一套索引语义：复用注入的 IndexManager，
        # 但不改 KnowledgeDocumentService（Task 5 的文件清单不含它）。
        self._mgr = documents._mgr
        self._embedding_model = settings.rag.embedding_model
        self._chunk_size = max(1, settings.rag.chunk_size)
        self._chunk_overlap = max(0, settings.rag.chunk_overlap)

    async def enrich(
        self,
        user_id: UUID,
        records: list[LiteratureRecord],
    ) -> FulltextStats:
        """为一次检索的文献补全 OA 全文，并挂载到 ``user_id`` 的知识库。

        任何单篇失败都只体现在返回的统计里，不向上抛异常；``attempted`` 是本次
        实际处理的文献数（已按 ``max_documents_per_task`` 截断）。
        """
        stats = FulltextStats()
        requested = _unique_records(records)[: self._settings.max_documents_per_task]
        stats.attempted = len(requested)
        if not requested:
            return stats
        if not self._settings.enabled:
            # 关闭全文获取 = 全部退回元数据证据
            stats.fallback_metadata = stats.attempted
            return stats

        cached = await self._load_cache_rows(requested)
        uncached: list[tuple[LiteratureRecord, dict[str, str]]] = []
        for record in requested:
            identifiers = _identifiers(record)
            hit = _match_cached(cached, identifiers)
            if hit is None:
                uncached.append((record, identifiers))
                continue
            # 缓存命中：不重新下载、不重新解析，只补当前用户的知识库挂载
            if await self._mount(user_id, record, hit, stored=None):
                stats.succeeded += 1
            else:
                stats.failed += 1

        if uncached:
            await self._enrich_uncached(user_id, uncached, stats)
        return stats

    async def _enrich_uncached(
        self,
        user_id: UUID,
        uncached: list[tuple[LiteratureRecord, dict[str, str]]],
        stats: FulltextStats,
    ) -> None:
        """抓取缓存未命中的文献并逐篇落库 + 挂载（逐篇隔离失败）。"""
        try:
            downloaded = await self._resolver.resolve([record for record, _ in uncached])
        except Exception as exc:  # resolver 自身异常也不得打断检索
            logger.warning("fulltext_resolve_failed", error=str(exc))
            stats.failed += len(uncached)
            return
        resolved = {record.source_id: candidate for record, candidate in downloaded}

        for record, identifiers in uncached:
            candidate = resolved.get(record.source_id)
            if candidate is None:
                # 没拿到 OA 正文（收费墙 / 无 OA 版本）→ 该篇退回元数据证据
                stats.fallback_metadata += 1
                continue
            try:
                outcome, row, stored = await self._persist_candidate(
                    user_id, record, candidate, identifiers
                )
            except Exception as exc:
                logger.warning(
                    "fulltext_persist_failed", source_id=record.source_id, error=str(exc)
                )
                await _safe_rollback(self._db)
                stats.failed += 1
                continue
            if outcome == "scanned":
                stats.scanned_skipped += 1
                continue
            if outcome != "ok" or row is None:
                stats.failed += 1
                continue
            try:
                mounted = await self._mount(user_id, record, row, stored)
            except Exception as exc:
                logger.warning("fulltext_mount_failed", source_id=record.source_id, error=str(exc))
                mounted = False
            if mounted:
                stats.succeeded += 1
            else:
                stats.failed += 1

    # ------------------------------------------------------------------
    # 缓存
    # ------------------------------------------------------------------
    async def _load_cache_rows(self, records: Sequence[LiteratureRecord]) -> list[FulltextCache]:
        """按 DOI / arXiv / PMID 批量查缓存（一条 SQL，避免 N+1）。"""
        dois: set[str] = set()
        arxiv_ids: set[str] = set()
        pmids: set[str] = set()
        for record in records:
            identifiers = _identifiers(record)
            if doi := identifiers.get("doi"):
                dois.add(doi)
            if arxiv_id := identifiers.get("arxiv_id"):
                arxiv_ids.add(arxiv_id)
            if pmid := identifiers.get("pmid"):
                pmids.add(pmid)

        conditions: list[Any] = []
        if dois:
            conditions.append(FulltextCache.doi.in_(sorted(dois)))
        if arxiv_ids:
            conditions.append(FulltextCache.arxiv_id.in_(sorted(arxiv_ids)))
        if pmids:
            conditions.append(FulltextCache.pmid.in_(sorted(pmids)))
        if not conditions:
            return []

        stmt = select(FulltextCache).where(
            FulltextCache.status == "active",
            or_(*conditions),
        )
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def _find_cache_row(
        self,
        identifiers: dict[str, str],
        sha256: str,
    ) -> FulltextCache | None:
        """并发落库冲突后回查已有缓存行。"""
        conditions: list[Any] = [FulltextCache.sha256 == sha256]
        if doi := identifiers.get("doi"):
            conditions.append(FulltextCache.doi == doi)
        if arxiv_id := identifiers.get("arxiv_id"):
            conditions.append(FulltextCache.arxiv_id == arxiv_id)
        if pmid := identifiers.get("pmid"):
            conditions.append(FulltextCache.pmid == pmid)
        result = await self._db.execute(select(FulltextCache).where(or_(*conditions)))
        return result.scalars().first()

    async def _persist_candidate(
        self,
        user_id: UUID,
        record: LiteratureRecord,
        candidate: FullTextCandidate,
        identifiers: dict[str, str],
    ) -> tuple[str, FulltextCache | None, StoredDocument | None]:
        """解析 + 落库一篇文章的正文。

        返回 ``(outcome, cache_row, stored)``：``outcome`` 为 ``"ok"`` /
        ``"scanned"``（扫描件跳过）/ ``"failed"``（解析失败）。
        """
        parsed = self._parse(candidate)
        if parsed.status is ParseStatus.SCANNED_NO_TEXT_LAYER:
            logger.info(
                "fulltext_scanned_skipped",
                source_id=record.source_id,
                provider=candidate.provider,
            )
            return "scanned", None, None
        if parsed.status is not ParseStatus.OK or not parsed.sections:
            logger.warning(
                "fulltext_parse_failed",
                source_id=record.source_id,
                provider=candidate.provider,
                status=str(parsed.status),
            )
            return "failed", None, None

        sections = [_section_payload(section) for section in parsed.sections]
        # 只有 PDF 需要本地留档（检索/定位不依赖文件，HTML/JATS 只存解析结果）
        stored: StoredDocument | None = None
        if candidate.content_format == "pdf":
            stored = await self._storage.save_bytes(
                user_id,
                f"{_safe_stem(record)}.pdf",
                candidate.content,
            )
        paper_card = await self._generate_paper_card(record, sections)

        row = FulltextCache(
            doi=identifiers.get("doi"),
            arxiv_id=identifiers.get("arxiv_id"),
            pmid=identifiers.get("pmid"),
            sha256=candidate.sha256,
            provider=candidate.provider,
            source_url=candidate.source_url[:1024],
            license=candidate.license,
            content_format=candidate.content_format,
            file_path=stored.path.as_posix() if stored is not None else None,
            page_count=parsed.page_count,
            parsed_sections=sections,
            paper_card=paper_card,
            status="active",
        )
        self._db.add(row)
        try:
            await self._db.commit()
        except IntegrityError:
            # 同一篇被并发/跨任务同时落库：复用已有缓存行，本次不重复写
            await self._db.rollback()
            existing = await self._find_cache_row(identifiers, candidate.sha256)
            if existing is None:
                logger.warning("fulltext_cache_conflict", source_id=record.source_id)
                return "failed", None, None
            return "ok", existing, None
        await self._db.refresh(row)
        return "ok", row, stored

    async def _generate_paper_card(
        self,
        record: LiteratureRecord,
        sections: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """生成论文卡片；失败只是少一个字段，不影响正文落库与挂载。"""
        try:
            card = await self._paper_cards.get_or_create(
                abstract=record.abstract,
                sections=[dict(section) for section in sections],
            )
        except Exception as exc:  # 卡片是增强信息，任何失败都降级为 None
            logger.warning(
                "fulltext_paper_card_failed",
                source_id=record.source_id,
                error=str(exc),
            )
            return None
        return dict(card) if card else None

    # ------------------------------------------------------------------
    # 用户知识库挂载
    # ------------------------------------------------------------------
    async def _find_mounted_doc(self, user_id: UUID, cache_id: UUID) -> KnowledgeDoc | None:
        """查该用户是否已挂载过这条缓存（避免重复建文档/重复向量化）。"""
        stmt = select(KnowledgeDoc).where(
            KnowledgeDoc.user_id == user_id,
            KnowledgeDoc.source == _SOURCE,
        )
        result = await self._db.execute(stmt)
        target = str(cache_id)
        for doc in result.scalars():
            meta = doc.meta_info or {}
            if str(meta.get("fulltext_cache_id")) == target:
                return doc
        return None

    async def _mount(
        self,
        user_id: UUID,
        record: LiteratureRecord,
        row: FulltextCache,
        stored: StoredDocument | None,
    ) -> bool:
        """把一条缓存全文挂成该用户的知识库文档（含分块与向量索引）。"""
        existing = await self._find_mounted_doc(user_id, row.id)
        if existing is not None:
            logger.info(
                "fulltext_already_mounted",
                doc_id=str(existing.id),
                user_id=str(user_id),
            )
            return True

        sections = _coerce_sections(row.parsed_sections)
        if not sections:
            logger.warning("fulltext_cache_empty_sections", cache_id=str(row.id))
            return False

        doc_type = _DOC_TYPE_BY_FORMAT.get(row.content_format, "txt")
        doc_id = stored.doc_uuid if stored is not None else uuid4()
        doc_id_str = str(doc_id)
        chunks = _build_chunks(
            doc_id=doc_id_str,
            user_id=user_id,
            sections=sections,
            doc_source=_SOURCE,
            doc_type=doc_type,
            chunk_size=self._chunk_size,
            chunk_overlap=self._chunk_overlap,
        )
        if not chunks:
            return False

        doc = KnowledgeDoc(
            id=doc_id,
            user_id=user_id,
            title=record.title[:256],
            source=_SOURCE,
            doc_type=doc_type,
            file_path=stored.path.as_posix() if stored is not None else row.file_path,
            index_status="indexing",
            chunk_count=0,
            meta_info={
                "original_name": _original_name(record, row, stored),
                "fulltext_cache_id": str(row.id),
                "doi": row.doi,
                "arxiv_id": row.arxiv_id,
                "pmid": row.pmid,
                "origin": _ORIGIN,
                "content_format": row.content_format,
                "source_url": row.source_url,
            },
        )

        self._db.add(doc)
        try:
            await self._db.flush()
            # 向量库/BM25 是同步实现（与 ingest 一致，放线程避免阻塞事件循环）
            await asyncio.to_thread(self._mgr.index_chunks, chunks)
            chunk_rows: list[DocChunk] = []
            for chunk in chunks:
                chunk_row = DocChunk(
                    doc_id=doc.id,
                    chunk_index=int(chunk.metadata.get("chunk_index", 0)),
                    content=chunk.text,
                    char_count=len(chunk.text),
                    position=_chunk_position(chunk),
                    chunk_meta=_chunk_meta(chunk),
                )
                self._db.add(chunk_row)
                chunk_rows.append(chunk_row)
            await self._db.flush()
            for chunk, chunk_row in zip(chunks, chunk_rows, strict=True):
                self._db.add(
                    VectorIndex(
                        chunk_id=chunk_row.id,
                        doc_id=doc.id,
                        collection_name=self._mgr.collection_name,
                        chroma_id=chunk.chunk_id,
                        embedding_model=self._embedding_model,
                        vector_dim=self._mgr.embedding_dimension,
                        has_bm25_index=True,
                        indexed_at=datetime.now(UTC),
                    )
                )
            doc.index_status = "ready"
            doc.chunk_count = len(chunks)
            await self._db.commit()
        except Exception as exc:
            await _safe_rollback(self._db)
            try:
                await asyncio.to_thread(self._mgr.remove_document, doc_id_str)
            except Exception as cleanup_exc:  # 清理失败不能掩盖主异常
                logger.warning(
                    "fulltext_cleanup_failed",
                    doc_id=doc_id_str,
                    error=str(cleanup_exc),
                )
            logger.warning("fulltext_mount_failed", doc_id=doc_id_str, error=str(exc))
            return False

        await self._db.refresh(doc)
        logger.info(
            "fulltext_mounted",
            doc_id=doc_id_str,
            cache_id=str(row.id),
            user_id=str(user_id),
            chunks=len(chunks),
        )
        return True

    # ------------------------------------------------------------------
    # 解析
    # ------------------------------------------------------------------
    def _parse(self, candidate: FullTextCandidate) -> ParseResult:
        if candidate.content_format == "pdf":
            return self._parser.parse_pdf(candidate.content)
        if candidate.content_format == "jats_xml":
            return self._parser.parse_jats(candidate.content)
        return self._parser.parse_html(candidate.content)


# ----------------------------------------------------------------------
# 模块级辅助
# ----------------------------------------------------------------------
def _unique_records(records: Sequence[LiteratureRecord]) -> list[LiteratureRecord]:
    """按 source_id 去重并保持原顺序（同名文献只抓一次）。"""
    seen: set[str] = set()
    unique: list[LiteratureRecord] = []
    for record in records:
        if record.source_id in seen:
            continue
        seen.add(record.source_id)
        unique.append(record)
    return unique


def _identifiers(record: LiteratureRecord) -> dict[str, str]:
    """抽取缓存键（DOI 统一小写，与 resolver 归一化规则一致）。"""
    identifiers: dict[str, str] = {}
    doi = _record_doi(record)
    if doi:
        identifiers["doi"] = doi.lower()
    arxiv_id = _record_arxiv_id(record)
    if arxiv_id:
        identifiers["arxiv_id"] = arxiv_id
    pmid = _record_pmid(record)
    if pmid:
        identifiers["pmid"] = pmid
    return identifiers


def _match_cached(
    cached: Sequence[FulltextCache],
    identifiers: dict[str, str],
) -> FulltextCache | None:
    """在已查出的缓存行里按 DOI → arXiv → PMID 优先级匹配。"""
    doi = identifiers.get("doi")
    arxiv_id = identifiers.get("arxiv_id")
    pmid = identifiers.get("pmid")
    for row in cached:
        if doi and row.doi == doi:
            return row
        if arxiv_id and row.arxiv_id == arxiv_id:
            return row
        if pmid and row.pmid == pmid:
            return row
    return None


def _safe_stem(record: LiteratureRecord) -> str:
    """为落盘文件生成安全文件名（DOI 里的 ``/`` 等字符替换成 ``_``）。"""
    raw = _record_doi(record) or _record_arxiv_id(record) or record.source_id
    cleaned = "".join(ch if (ch.isalnum() or ch in "-_.") else "_" for ch in raw)
    return (cleaned or "fulltext")[:80]


def _original_name(
    record: LiteratureRecord,
    row: FulltextCache,
    stored: StoredDocument | None,
) -> str:
    if stored is not None:
        return stored.original_name
    extension = _EXTENSION_BY_FORMAT.get(row.content_format, "txt")
    return f"{_safe_stem(record)}.{extension}"


def _section_payload(section: ParsedSection) -> dict[str, Any]:
    """把解析出的章节存成 JSON 友好的 dict（``fulltext_cache.parsed_sections``）。"""
    return {
        "title": section.title,
        "role": section.role,
        "page_start": section.page_start,
        "page_end": section.page_end,
        "text": section.text,
    }


def _coerce_sections(raw: Any) -> list[dict[str, Any]]:
    """读取缓存里的 ``parsed_sections``（历史行可能是任意 JSON）。"""
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def _split_text(
    text: str,
    chunk_size: int,
    chunk_overlap: int,
) -> list[tuple[str, int, int]]:
    """按固定窗口切分单节正文，返回 ``(片段, 起偏移, 止偏移)``。"""
    size = max(1, chunk_size)
    overlap = max(0, min(chunk_overlap, size - 1))
    step = max(1, size - overlap)
    pieces: list[tuple[str, int, int]] = []
    length = len(text)
    start = 0
    while start < length:
        end = min(length, start + size)
        piece = text[start:end].strip()
        if piece:
            pieces.append((piece, start, end))
        if end >= length:
            break
        start += step
    return pieces


def _build_chunks(
    *,
    doc_id: str,
    user_id: UUID,
    sections: list[dict[str, Any]],
    doc_source: str,
    doc_type: str,
    chunk_size: int,
    chunk_overlap: int,
) -> list[TextChunk]:
    """按章节切块，章节标题/角色与页码写进 chunk 元数据。

    元数据保持扁平标量（``VectorStore._chunk_metadata`` 只转发白名单键），
    与 ``DocumentChunker`` 的产出形状保持一致，避免下游过滤/定位拿不到值。
    """
    chunks: list[TextChunk] = []
    index = 0
    for section in sections:
        text = str(section.get("text") or "")
        if not text.strip():
            continue
        title = str(section.get("title") or "")
        role = str(section.get("role") or "other")
        page_start = section.get("page_start")
        page_end = section.get("page_end")
        for piece, start, end in _split_text(text, chunk_size, chunk_overlap):
            metadata: dict[str, Any] = {
                "doc_id": doc_id,
                "doc_source": doc_source,
                "doc_type": doc_type,
                "chunk_index": index,
                "user_id": str(user_id),
                "section_title": title,
                "section_role": role,
            }
            if isinstance(page_start, int) and page_start >= 1:
                metadata["page_start"] = page_start
                metadata["page_end"] = (
                    page_end if isinstance(page_end, int) and page_end >= page_start else page_start
                )
            chunks.append(
                TextChunk(
                    chunk_id=hashlib.md5(f"{doc_id}:{index}".encode()).hexdigest()[:16],
                    doc_id=doc_id,
                    text=piece,
                    metadata=metadata,
                    start_offset=start,
                    end_offset=end,
                )
            )
            index += 1
    return chunks


def _chunk_position(chunk: TextChunk) -> dict[str, Any]:
    """``DocChunk.position``：偏移 + 页码（页码只在解析器给出时写）。"""
    position: dict[str, Any] = {
        "start_offset": chunk.start_offset,
        "end_offset": chunk.end_offset,
    }
    page_start = chunk.metadata.get("page_start")
    if isinstance(page_start, int) and page_start >= 1:
        position["page_start"] = page_start
        page_end = chunk.metadata.get("page_end")
        position["page_end"] = (
            page_end if isinstance(page_end, int) and page_end >= page_start else page_start
        )
    return position


def _chunk_meta(chunk: TextChunk) -> dict[str, Any] | None:
    """``DocChunk.chunk_meta``：章节标题 + 章节角色（供证据定位展示）。"""
    meta: dict[str, Any] = {}
    title = chunk.metadata.get("section_title")
    if isinstance(title, str) and title:
        meta["section_title"] = title[:160]
    role = chunk.metadata.get("section_role")
    if isinstance(role, str) and role:
        meta["section_role"] = role
    return meta or None


async def _safe_rollback(db: AsyncSession) -> None:
    """回滚会话；回滚本身失败时只记日志，不掩盖原始异常。"""
    try:
        await db.rollback()
    except Exception as exc:
        logger.warning("fulltext_rollback_failed", error=str(exc))
