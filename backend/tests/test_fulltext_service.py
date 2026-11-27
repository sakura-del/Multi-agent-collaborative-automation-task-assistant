"""``FulltextService`` 测试（Issue #73 Phase A / 实施计划 Task 5）。

覆盖：

- 同一 DOI 二次调用命中 ``fulltext_cache``：不再下载/解析、不新增缓存行、不重复挂载；
- 成功挂载后的 ``KnowledgeDoc.meta_info`` / ``DocChunk.chunk_meta`` / ``position``
  定位信息，以及 ``VectorIndex`` 与分块一一对应；
- 扫描件（无文本层）只累计 ``scanned_skipped``，不落缓存、不建文档；
- 单篇解析失败不影响同批其它文献（``failed`` 计数，成功篇照常挂载）；
- ``DocumentStorage.save_bytes`` 的扩展名/签名/大小校验。

全部外部请求经 ``httpx.MockTransport`` 伪造，SSRF 校验替换为放行（不发真实 DNS）；
数据库用 ``db_session``（函数级内存 SQLite），不触外网、不加载真实嵌入模型。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import fitz
import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationException
from app.db.models.fulltext_cache import FulltextCache
from app.db.models.knowledge import DocChunk, KnowledgeDoc, VectorIndex
from app.knowledge.services.document_storage import DocumentStorage
from app.knowledge.services.fulltext_parser import CleanFullTextParser
from app.knowledge.services.fulltext_service import FulltextService
from app.tools.literature.fulltext_resolver import FullTextResolver
from app.tools.literature.models import LiteratureRecord

#: 正文固定用 ASCII：PDF 内置 Helvetica 编不了中文（测试只关心解析/挂载链路）。
_SAMPLE_PDF_LINES: tuple[tuple[str, float, float], ...] = (
    ("1 Introduction", 90.0, 12.0),
    (
        "Background text about retrieval evaluation and dense methods comparison.",
        116.0,
        10.0,
    ),
    ("3 Method", 158.0, 12.0),
    (
        "Our method uses dense retrieval with reranking over a large paper corpus.",
        184.0,
        10.0,
    ),
)


def _sample_pdf() -> bytes:
    """生成一页带章节标题与正文的 PDF（超过扫描件密度阈值）。"""
    document = fitz.open()
    page = document.new_page()
    for text, y, size in _SAMPLE_PDF_LINES:
        page.insert_text((72.0, y), text, fontsize=size)
    content = document.tobytes()
    document.close()
    return content


def _blank_pdf() -> bytes:
    """生成一页没有任何文本层的扫描件 PDF。"""
    document = fitz.open()
    document.new_page()
    content = document.tobytes()
    document.close()
    return content


def _doi_pdf_name(doi: str) -> str:
    return doi.replace("/", "_")


def _record(source_id: str = "10.1234/abc") -> LiteratureRecord:
    return LiteratureRecord(
        source_id=source_id,
        title="Retrieval evaluation with dense methods",
        authors=["Ada Lovelace"],
        abstract="We evaluate dense retrieval methods.",
        published_at=datetime(2024, 5, 1, tzinfo=UTC),
        source_url=f"https://doi.org/{source_id}",
        pdf_url=f"https://oa.example.org/{_doi_pdf_name(source_id)}.pdf",
    )


class _OaTransport:
    """假 OA 网络：OpenAlex 返回 ``best_oa_location``，正文按 DOI 映射返回。"""

    def __init__(self, bodies: dict[str, bytes]) -> None:
        self._bodies = bodies
        self.requests: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        for doi, body in self._bodies.items():
            name = _doi_pdf_name(doi)
            if request.url.host == "api.openalex.org" and doi in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "best_oa_location": {
                            "pdf_url": f"https://oa.example.org/{name}.pdf",
                            "license": "cc-by-4.0",
                        }
                    },
                )
            if request.url.host == "oa.example.org" and request.url.path == f"/{name}.pdf":
                return httpx.Response(
                    200, content=body, headers={"content-type": "application/pdf"}
                )
        return httpx.Response(404)


class _FakeIndexManager:
    """``IndexManager`` 替身：只记录写入双索引的分块，不加载模型。"""

    def __init__(self) -> None:
        self.collection_name = "knowledge_base"
        self.embedding_dimension = 8
        self.indexed: list[Any] = []
        self.removed: list[str] = []

    def index_chunks(self, chunks: list[Any]) -> int:
        self.indexed.extend(chunks)
        return len(chunks)

    def remove_document(self, doc_id: str) -> int:
        self.removed.append(doc_id)
        return 0


class _FakeDocuments:
    """只暴露 ``FulltextService`` 用到的 ``_mgr``（不改动 ``document_service``）。"""

    def __init__(self, manager: _FakeIndexManager) -> None:
        self._mgr = manager


class _FakePaperCards:
    """``PaperCardService``（Task 6）的最小替身。"""

    def __init__(self, card: dict[str, str] | None = None) -> None:
        self.card = card
        self.calls = 0

    async def get_or_create(
        self, *, abstract: str, sections: list[dict[str, Any]]
    ) -> dict[str, str] | None:
        assert isinstance(abstract, str)
        assert isinstance(sections, list)
        self.calls += 1
        return self.card


@pytest.fixture(autouse=True)
def _allow_all_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    """SSRF 校验放行：测试不出网，MockTransport 也不做真实 DNS 解析。"""
    monkeypatch.setattr("app.tools.literature.fulltext_resolver._is_safe_url", lambda url: True)


def _service(
    db: AsyncSession,
    tmp_path: Path,
    client: httpx.AsyncClient,
    *,
    manager: _FakeIndexManager | None = None,
    paper_cards: _FakePaperCards | None = None,
) -> tuple[FulltextService, _FakeIndexManager, _FakePaperCards]:
    manager = manager or _FakeIndexManager()
    cards = paper_cards or _FakePaperCards()
    service = FulltextService(
        db,
        resolver=FullTextResolver(client=client),
        parser=CleanFullTextParser(),
        documents=_FakeDocuments(manager),  # type: ignore[arg-type]
        storage=DocumentStorage(tmp_path / "uploads", max_file_bytes=1024 * 1024),
        paper_cards=cards,  # type: ignore[arg-type]
    )
    return service, manager, cards


async def _count(db: AsyncSession, model: Any) -> int:
    return int(await db.scalar(select(func.count()).select_from(model)) or 0)


async def test_second_call_for_same_doi_uses_cache(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """同 DOI 二次调用：不重复外呼/下载、不新增缓存行、不重复挂载文档。"""
    pdf = _sample_pdf()
    transport = _OaTransport({"10.1234/abc": pdf})
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        service, manager, cards = _service(db_session, tmp_path, client)
        user_id = uuid4()

        first = await service.enrich(user_id, [_record()])
        assert list(first.as_dict()) == [
            "attempted",
            "succeeded",
            "fallback_metadata",
            "scanned_skipped",
            "failed",
        ]
        assert first.as_dict() == {
            "attempted": 1,
            "succeeded": 1,
            "fallback_metadata": 0,
            "scanned_skipped": 0,
            "failed": 0,
        }
        # 首次必须外呼两次：OpenAlex 元数据 + 正文下载
        assert len(transport.requests) == 2
        chunk_count = await _count(db_session, DocChunk)
        assert chunk_count >= 1

        second = await service.enrich(user_id, [_record()])

    assert second.succeeded == 1
    assert second.attempted == 1
    # 关键断言：缓存命中后完全不再下载（resolver 输入为空，不发任何请求）
    assert len(transport.requests) == 2
    assert await _count(db_session, FulltextCache) == 1
    assert await _count(db_session, KnowledgeDoc) == 1
    assert await _count(db_session, DocChunk) == chunk_count
    assert len(manager.indexed) == chunk_count
    assert cards.calls == 1


async def test_successful_mount_writes_user_doc_with_locators(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """成功挂载：缓存行落库 + 用户知识库文档/分块/向量齐全且带章节页码定位。"""
    pdf = _sample_pdf()
    card = {"research_question": "dense retrieval 是否有效"}
    transport = _OaTransport({"10.1234/abc": pdf})
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        service, manager, cards = _service(
            db_session, tmp_path, client, paper_cards=_FakePaperCards(card)
        )
        user_id = uuid4()
        stats = await service.enrich(user_id, [_record()])

    assert stats.succeeded == 1
    assert cards.calls == 1

    cache = (await db_session.execute(select(FulltextCache))).scalar_one()
    assert cache.doi == "10.1234/abc"
    assert cache.provider == "openalex_oa"
    assert cache.source_url == "https://oa.example.org/10.1234_abc.pdf"
    assert cache.license == "cc-by-4.0"
    assert cache.content_format == "pdf"
    assert cache.page_count == 1
    assert cache.paper_card == card
    assert cache.status == "active"
    # 正文必须真的落盘（知识库挂载是异步链路，路径必须可回溯）
    assert cache.file_path is not None
    assert Path(cache.file_path).is_file()
    assert Path(cache.file_path).read_bytes() == pdf
    sections = cache.parsed_sections
    assert sections
    assert sections[0]["title"] == "1 Introduction"
    assert sections[0]["role"] == "background"
    assert sections[0]["page_start"] == 1
    assert sections[0]["page_end"] == 1

    doc = (await db_session.execute(select(KnowledgeDoc))).scalar_one()
    assert doc.user_id == user_id
    assert doc.source == "fulltext_oa"
    assert doc.doc_type == "pdf"
    assert doc.index_status == "ready"
    assert doc.file_path == cache.file_path
    assert doc.meta_info is not None
    assert doc.meta_info["fulltext_cache_id"] == str(cache.id)
    assert doc.meta_info["doi"] == "10.1234/abc"
    assert doc.meta_info["origin"] == "oa_auto"

    chunks = (
        (
            await db_session.execute(
                select(DocChunk).where(DocChunk.doc_id == doc.id).order_by(DocChunk.chunk_index)
            )
        )
        .scalars()
        .all()
    )
    assert chunks
    assert doc.chunk_count == len(chunks)
    assert chunks[0].content.startswith("1 Introduction")
    assert chunks[0].chunk_meta is not None
    assert chunks[0].chunk_meta["section_title"] == "1 Introduction"
    assert chunks[0].chunk_meta["section_role"] == "background"
    assert chunks[0].position is not None
    assert chunks[0].position["page_start"] == 1
    assert chunks[0].position["page_end"] == 1

    # 双索引与持久化分块一一对应
    assert len(manager.indexed) == len(chunks)
    vector_rows = (
        (await db_session.execute(select(VectorIndex).where(VectorIndex.doc_id == doc.id)))
        .scalars()
        .all()
    )
    assert len(vector_rows) == len(chunks)


async def test_scanned_pdf_only_counts_skipped(db_session: AsyncSession, tmp_path: Path) -> None:
    """扫描件（无文本层）：只计 skipped，不写缓存、不建文档、不调 LLM 生成卡片。"""
    transport = _OaTransport({"10.1234/abc": _blank_pdf()})
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        service, manager, cards = _service(db_session, tmp_path, client)
        stats = await service.enrich(uuid4(), [_record()])

    assert stats.scanned_skipped == 1
    assert stats.succeeded == 0
    assert stats.failed == 0
    assert stats.fallback_metadata == 0
    assert cards.calls == 0
    assert manager.indexed == []
    assert await _count(db_session, FulltextCache) == 0
    assert await _count(db_session, KnowledgeDoc) == 0


async def test_single_document_failure_does_not_block_batch(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """单篇异常只计 failed，同批其它文献照常挂载；无 OA 命中计 fallback。"""
    transport = _OaTransport(
        {
            "10.1234/good": _sample_pdf(),
            # 魔数合法但内容损坏：resolver 能下载，解析阶段失败
            "10.1234/broken": b"%PDF-1.7\nthis body is not a real PDF document",
        }
    )
    records = [
        _record("10.1234/good"),
        _record("10.1234/broken"),
        _record("10.9999/missing"),
    ]
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        service, manager, _ = _service(db_session, tmp_path, client)
        stats = await service.enrich(uuid4(), records)

    assert stats.attempted == 3
    assert stats.succeeded == 1
    assert stats.failed == 1
    assert stats.fallback_metadata == 1
    assert stats.scanned_skipped == 0
    assert await _count(db_session, FulltextCache) == 1
    assert await _count(db_session, KnowledgeDoc) == 1
    docs = (await db_session.execute(select(KnowledgeDoc))).scalars().all()
    assert docs[0].meta_info is not None
    assert docs[0].meta_info["doi"] == "10.1234/good"
    assert manager.indexed


async def test_save_bytes_validates_extension_signature_and_size(tmp_path: Path) -> None:
    """``save_bytes`` 与 ``save`` 共用白名单/签名/大小校验，落盘仍按用户隔离。"""
    storage = DocumentStorage(tmp_path / "uploads", max_file_bytes=1024)
    user_id = uuid4()
    pdf = _sample_pdf()

    stored = await storage.save_bytes(user_id, "oa-paper.pdf", pdf)
    assert stored.original_name == "oa-paper.pdf"
    assert stored.extension == ".pdf"
    assert stored.size == len(pdf)
    assert stored.path.is_file()
    assert stored.path.parent == (tmp_path / "uploads" / str(user_id)).resolve()
    # 落盘文件名 stem 就是文档 UUID（`uuid4().hex`），doc_uuid 由它反解
    assert stored.doc_uuid == UUID(stored.path.stem)

    with pytest.raises(ValidationException, match="不支持的文件类型"):
        await storage.save_bytes(user_id, "paper.bin", b"data")
    with pytest.raises(ValidationException, match="文件签名"):
        await storage.save_bytes(user_id, "fake.pdf", b"not a pdf body")
    with pytest.raises(ValidationException, match="30 MB"):
        await storage.save_bytes(user_id, "big.txt", b"x" * 1025)
