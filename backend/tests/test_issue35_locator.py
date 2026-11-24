"""Issue #35：BM25/向量混合检索定位透传、真实 PDF 入库与存量索引回填测试。"""

from __future__ import annotations

import io
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
import pytest
from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models.knowledge import DocChunk, KnowledgeDoc, VectorIndex
from app.knowledge.models.document import ChunkStrategy, RetrievalResult, TextChunk
from app.knowledge.services.bm25_index import BM25Index
from app.knowledge.services.cache import RetrievalCache
from app.knowledge.services.chunker import DocumentChunker
from app.knowledge.services.context_builder import LiteratureContextBuilder
from app.knowledge.services.document_parser import DocumentParser
from app.knowledge.services.document_service import KnowledgeDocumentService
from app.knowledge.services.document_storage import DocumentStorage
from app.knowledge.services.embedding import create_embedding_service
from app.knowledge.services.hybrid_retriever import HybridRetriever
from app.knowledge.services.index_manager import IndexManager
from app.knowledge.services.knowledge_base import KnowledgeBase
from app.knowledge.services.reindex import backfill_chunk_locators, reindex_from_chunks
from app.knowledge.services.reranker import create_reranker
from app.knowledge.services.vector_store import VectorStore


def _build_three_page_pdf(target_path: Path) -> bytes:
    """使用 PyMuPDF 生成 3 页英文测试 PDF（含数字小节与 Table 2，避免字体缺失）。"""
    pdf = fitz.open()
    pages_text = [
        "1 Introduction\nThis baseline overview introduces multi-agent retrieval systems.",
        "3.2 Experimental Setup\nTable 2 summarizes accuracy benchmarks reaching 94.2 percent.",
        "4 Conclusion\nFinal discussion covers future scalability improvements.",
    ]
    for content in pages_text:
        page = pdf.new_page()
        rect = fitz.Rect(50, 50, 540, 760)
        page.insert_textbox(rect, content, fontsize=11, fontname="helv")
    raw = pdf.tobytes()
    pdf.close()
    target_path.write_bytes(raw)
    return raw


async def _create_sqlite_session_factory(
    db_path: Path,
) -> tuple[Any, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


class _RecordingVectorStore:
    """记录写入向量库的分块与 metadata（验证存量回填后透传定位标量）。"""

    def __init__(self) -> None:
        self.chunks: list[TextChunk] = []

    def list_ids(self) -> list[str]:
        return [c.chunk_id for c in self.chunks]

    def add_chunks(self, chunks: list[TextChunk]) -> int:
        self.chunks.extend(chunks)
        return len(chunks)

    def remove_chunks(self, chunk_ids: list[str]) -> int:
        return len(chunk_ids)


class TestHybridAndBm25Locator:
    """向量库、BM25 与 RRF 混合检索均须保留定位 metadata。"""

    def test_vector_store_and_bm25_preserve_locator_metadata(self) -> None:
        emb = create_embedding_service(force_mock=True)
        vector_store = VectorStore(embedding_service=emb, persist_directory="")
        bm25 = BM25Index()

        chunk = TextChunk(
            chunk_id=str(uuid.uuid4()),
            doc_id=str(uuid.uuid4()),
            text="Experimental setup in Table 2 achieves 94.2 accuracy.",
            metadata={
                "user_id": "u1",
                "chunk_index": 0,
                "doc_source": "paper.pdf",
                "page_start": 5,
                "page_end": 6,
                "section_title": "3.2 Experimental Setup",
                "table_ref": "Table 2",
            },
            start_offset=10,
            end_offset=64,
        )
        vector_store.add_chunks([chunk])
        bm25.add_chunks([chunk])

        vec_hits = vector_store.search("Table 2 accuracy", top_k=2, where={"user_id": "u1"})
        bm25_hits = bm25.search("Table 2 accuracy", top_k=2, where={"user_id": "u1"})

        for hits in (vec_hits, bm25_hits):
            assert len(hits) == 1
            meta = hits[0].metadata
            assert meta["page_start"] == 5
            assert meta["page_end"] == 6
            assert meta["section_title"] == "3.2 Experimental Setup"
            assert meta["table_ref"] == "Table 2"

    def test_hybrid_rrf_merges_locator_from_bm25_when_vector_hit_lacks_it(self) -> None:
        cid = str(uuid.uuid4())
        did = str(uuid.uuid4())

        class _StubVec:
            is_ready = True

            def search(
                self, query: str, top_k: int = 5, where: Any = None
            ) -> list[RetrievalResult]:
                return [
                    RetrievalResult(
                        chunk_id=cid,
                        doc_id=did,
                        text="Table 2 results",
                        score=0.9,
                        metadata={"user_id": "u1"},
                        source="vector",
                    )
                ]

        class _StubBm25:
            is_ready = True

            def search(
                self, query: str, top_k: int = 5, where: Any = None
            ) -> list[RetrievalResult]:
                return [
                    RetrievalResult(
                        chunk_id=cid,
                        doc_id=did,
                        text="Table 2 results",
                        score=4.2,
                        metadata={
                            "user_id": "u1",
                            "page_start": 2,
                            "page_end": 2,
                            "section_title": "3.2 Experimental Setup",
                            "table_ref": "Table 2",
                        },
                        source="bm25",
                    )
                ]

        retriever = HybridRetriever(
            vector_store=_StubVec(),  # type: ignore[arg-type]
            bm25_index=_StubBm25(),  # type: ignore[arg-type]
        )
        fused = retriever.search("Table 2", top_k=5)

        assert len(fused) == 1
        assert fused[0].metadata["page_start"] == 2
        assert fused[0].metadata["section_title"] == "3.2 Experimental Setup"
        assert fused[0].metadata["table_ref"] == "Table 2"


class TestDocumentServiceLocatorPipeline:
    """真实 PDF 入库 → rebuild_bm25 → search_for_context → Evidence.locator 全链验证。"""

    @pytest.mark.asyncio
    async def test_ingest_rebuild_bm25_and_search_backfill_with_real_pdf(
        self, tmp_path: Path
    ) -> None:
        pdf_bytes = _build_three_page_pdf(tmp_path / "source.pdf")
        engine, session_factory = await _create_sqlite_session_factory(tmp_path / "issue35.db")
        try:
            emb = create_embedding_service(force_mock=True)
            reranker = create_reranker(force_mock=True)
            vector_store = VectorStore(embedding_service=emb, persist_directory="")
            bm25 = BM25Index()
            mgr = IndexManager(
                parser=DocumentParser(),
                chunker=DocumentChunker(chunk_size=120, chunk_overlap=20),
                vector_store=vector_store,
                bm25_index=bm25,
            )
            retriever = HybridRetriever(vector_store=vector_store, bm25_index=bm25)
            kb = KnowledgeBase(
                retriever=retriever,
                reranker=reranker,
                cache=RetrievalCache(redis_client=None),
            )
            storage = DocumentStorage(tmp_path / "uploads", max_file_bytes=2 * 1024 * 1024)
            user_id = uuid.uuid4()

            async with session_factory() as session:
                service = KnowledgeDocumentService(
                    session, storage=storage, index_manager=mgr, kb=kb
                )
                doc = await service.ingest(
                    user_id,
                    UploadFile(filename="paper.pdf", file=io.BytesIO(pdf_bytes)),
                    title="Real 3-Page PDF",
                    strategy=ChunkStrategy.SEMANTIC,
                )
                chunks = (
                    (
                        await session.execute(
                            select(DocChunk)
                            .where(DocChunk.doc_id == doc.id)
                            .order_by(DocChunk.chunk_index)
                        )
                    )
                    .scalars()
                    .all()
                )
                assert len(chunks) == 3
                assert [c.position["page_start"] for c in chunks] == [1, 2, 3]
                assert chunks[1].chunk_meta == {
                    "section_title": "3.2 Experimental Setup",
                    "table_ref": "Table 2",
                }

                # 模拟进程重启：清空内存 BM25 与向量库，从 DB 重建 BM25 后验证定位字段仍在
                bm25.clear()
                rebuilt_count = await service.rebuild_bm25()
                assert rebuilt_count == 3

                bm25_hits = bm25.search(
                    "Table 2 benchmark", top_k=3, where={"user_id": str(user_id)}
                )
                assert bm25_hits
                assert bm25_hits[0].metadata["page_start"] == 2
                assert bm25_hits[0].metadata["table_ref"] == "Table 2"

                # 若结果丢失 locator metadata（模拟旧索引），_backfill_locator_metadata 应从 DocChunk 补回
                stripped = [replace(hit, metadata={"user_id": str(user_id)}) for hit in bm25_hits]
                backfilled = await service._backfill_locator_metadata(stripped)
                assert backfilled[0].metadata["page_start"] == 2
                assert backfilled[0].metadata["section_title"] == "3.2 Experimental Setup"
                assert backfilled[0].metadata["table_ref"] == "Table 2"

                # 上下文构建完整透传 EvidenceLocator
                context = await LiteratureContextBuilder(service).build(
                    user_id, "Table 2 benchmarks", top_k=3
                )
                assert any(
                    ev.locator is not None
                    and ev.locator.page_start == 2
                    and ev.locator.table_ref == "Table 2"
                    for ev in context.evidence
                )
        finally:
            await engine.dispose()


class TestReindexBackfillLegacyPdf:
    """存量 PDF 文档补全验证：回填 position['page_start'] 且不引发重复分块或 chunk_id 变更。"""

    @pytest.mark.asyncio
    async def test_reindex_backfills_existing_pdf_without_rechunking(self, tmp_path: Path) -> None:
        pdf_path = tmp_path / "legacy_paper.pdf"
        _build_three_page_pdf(pdf_path)
        parsed = DocumentParser().parse(str(pdf_path))
        chunks = DocumentChunker(chunk_size=120, chunk_overlap=20).chunk(
            parsed, strategy=ChunkStrategy.SEMANTIC
        )
        assert len(chunks) == 3

        engine, session_factory = await _create_sqlite_session_factory(tmp_path / "legacy.db")
        try:
            user_id = uuid.uuid4()
            doc_id = uuid.uuid4()
            original_ids: list[uuid.UUID] = []

            async with session_factory() as session:
                doc = KnowledgeDoc(
                    id=doc_id,
                    user_id=user_id,
                    title="Legacy PDF Without Locators",
                    source="upload",
                    doc_type="pdf",
                    file_path=str(pdf_path),
                    index_status="ready",
                    chunk_count=len(chunks),
                )
                session.add(doc)
                await session.flush()

                for idx, ch in enumerate(chunks):
                    row = DocChunk(
                        doc_id=doc_id,
                        chunk_index=idx,
                        content=ch.text,
                        char_count=len(ch.text),
                        position=None,
                        chunk_meta=None,
                    )
                    session.add(row)
                    await session.flush()
                    original_ids.append(row.id)
                    session.add(
                        VectorIndex(
                            chunk_id=row.id,
                            doc_id=doc_id,
                            collection_name="knowledge_base",
                            chroma_id=ch.chunk_id,
                            embedding_model="mock",
                            vector_dim=8,
                            has_bm25_index=True,
                            indexed_at=datetime.now(UTC),
                        )
                    )
                await session.commit()

                store = _RecordingVectorStore()
                result = await reindex_from_chunks(
                    session=session,
                    vector_store=store,
                    backfill_locators=True,
                )

                assert result.planned == 3
                assert result.indexed == 3
                assert result.failed_batches == 0

                refreshed = (
                    (
                        await session.execute(
                            select(DocChunk)
                            .where(DocChunk.doc_id == doc_id)
                            .order_by(DocChunk.chunk_index)
                        )
                    )
                    .scalars()
                    .all()
                )
                # 核心不变量：分块数量与既有 DocChunk.id 完全不变，不引发重复分块
                assert len(refreshed) == len(original_ids)
                assert [row.id for row in refreshed] == original_ids

                # 所有 PDF 分块已回填物理页码与第 2 页章节/表格编号
                assert [row.position["page_start"] for row in refreshed] == [1, 2, 3]
                assert all(
                    isinstance(row.position.get("page_start"), int)
                    and row.position["page_start"] >= 1
                    for row in refreshed
                )
                assert refreshed[1].chunk_meta == {
                    "section_title": "3.2 Experimental Setup",
                    "table_ref": "Table 2",
                }

                # 写入向量库的分块 metadata 同步保留标量定位字段
                assert [c.chunk_id for c in store.chunks] == [str(cid) for cid in original_ids]
                assert store.chunks[1].metadata["page_start"] == 2
                assert store.chunks[1].metadata["section_title"] == "3.2 Experimental Setup"
                assert store.chunks[1].metadata["table_ref"] == "Table 2"

                # 源文件缺失时安全跳过而不抛错
                pdf_path.unlink()
                missing_res = await backfill_chunk_locators(session=session, doc_id=doc_id)
                assert missing_res.docs_scanned == 1
                assert missing_res.skipped_missing_files == 1
        finally:
            await engine.dispose()
