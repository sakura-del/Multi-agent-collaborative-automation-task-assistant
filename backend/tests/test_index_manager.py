"""IndexManager 测试：解析→分块→双索引入库流水线与统计。

覆盖文档入库、文本入库、删除、清空、统计、增量更新等场景。
"""

from __future__ import annotations

import uuid

import pytest

from app.knowledge.models.document import ChunkStrategy
from app.knowledge.services.bm25_index import BM25Index
from app.knowledge.services.chunker import DocumentChunker
from app.knowledge.services.document_parser import DocumentParser
from app.knowledge.services.embedding import MockEmbeddingService
from app.knowledge.services.index_manager import IndexManager
from app.knowledge.services.vector_store import VectorStore


@pytest.fixture
def index_manager() -> IndexManager:
    """构造内存模式索引管理器：Mock 嵌入 + 内存 Chroma。"""
    return IndexManager(
        parser=DocumentParser(),
        chunker=DocumentChunker(chunk_size=100, chunk_overlap=20),
        vector_store=VectorStore(
            embedding_service=MockEmbeddingService(dimension=64),
            persist_directory="",
            collection_name=f"im_{uuid.uuid4().hex[:8]}",
        ),
        bm25_index=BM25Index(),
    )


class TestAddDocument:
    """add_document：文本入库与文件入库。"""

    def test_add_text_indexes_in_both_stores(self, index_manager: IndexManager) -> None:
        """文本入库后向量库与 BM25 索引均应包含分块。"""
        doc = index_manager.add_text(
            "RAG 检索增强生成通过向量检索与重排提升 LLM 回答质量",
            doc_id="doc-rag",
        )
        assert doc.doc_id == "doc-rag"
        assert index_manager._vector_store.size > 0
        assert index_manager._bm25_index.size > 0

    def test_add_text_no_chunks(self, index_manager: IndexManager) -> None:
        """空文本入库不产生分块，但仍返回 ParsedDocument。"""
        doc = index_manager.add_text("")
        assert doc.text == ""
        assert index_manager._vector_store.size == 0

    def test_add_text_with_custom_strategy(self, index_manager: IndexManager) -> None:
        """指定 semantic 策略应正确分块入库。"""
        text = "第一段落内容\n\n第二段落内容\n\n第三段落内容"
        index_manager.add_text(text, strategy=ChunkStrategy.SEMANTIC)
        # 三段应被独立分块
        assert index_manager._vector_store.size >= 3

    def test_add_text_explicit_doc_id(self, index_manager: IndexManager) -> None:
        """显式 doc_id 应被使用。"""
        doc = index_manager.add_text("内容", doc_id="custom-id")
        assert doc.doc_id == "custom-id"

    def test_add_document_nonexistent_path(self, index_manager: IndexManager) -> None:
        """不存在的文件路径作为文本入库。"""
        doc = index_manager.add_document("not a file path just text")
        assert doc.doc_type.value == "txt"
        assert index_manager._vector_store.size > 0


class TestRemoveDocument:
    """remove_document：从双索引同步移除。"""

    def test_remove_existing_doc(self, index_manager: IndexManager) -> None:
        """删除已入库文档应同步清除双索引分块。"""
        index_manager.add_text("内容片段一\n\n内容片段二", doc_id="d1")
        before_vec = index_manager._vector_store.size
        removed = index_manager.remove_document("d1")
        assert removed >= 1
        assert index_manager._vector_store.size < before_vec

    def test_remove_nonexistent_returns_zero(self, index_manager: IndexManager) -> None:
        """删除不存在的文档返回 0。"""
        assert index_manager.remove_document("nonexistent") == 0


class TestStatsAndList:
    """get_stats 与 list_documents。"""

    def test_empty_stats(self, index_manager: IndexManager) -> None:
        """空索引统计应为零。"""
        stats = index_manager.get_stats()
        assert stats.total_docs == 0
        assert stats.total_chunks == 0
        assert not stats.vector_index_ready
        assert not stats.bm25_index_ready
        assert stats.last_update is None

    def test_stats_after_add(self, index_manager: IndexManager) -> None:
        """入库后统计应反映文档数与分块数。"""
        index_manager.add_text("内容一\n\n内容二", doc_id="d1")
        stats = index_manager.get_stats()
        assert stats.total_docs == 1
        assert stats.total_chunks > 0
        assert stats.vector_index_ready
        assert stats.bm25_index_ready
        assert stats.last_update is not None

    def test_list_documents_empty(self, index_manager: IndexManager) -> None:
        """空索引文档列表为空。"""
        assert index_manager.list_documents() == []

    def test_list_documents_after_add(self, index_manager: IndexManager) -> None:
        """入库后文档列表应包含文档信息。"""
        index_manager.add_text("内容", doc_id="d1")
        docs = index_manager.list_documents()
        assert len(docs) == 1
        assert docs[0]["doc_id"] == "d1"
        assert "chunks" in docs[0]
        assert "time" in docs[0]
        assert "source" in docs[0]


class TestClearAndRebuild:
    """clear 与 rebuild_all。"""

    def test_clear_empties_all(self, index_manager: IndexManager) -> None:
        """clear 应清空向量库、BM25 索引与文档记录。"""
        index_manager.add_text("内容一", doc_id="d1")
        index_manager.add_text("内容二", doc_id="d2")
        index_manager.clear()
        assert index_manager._vector_store.size == 0
        assert index_manager._bm25_index.size == 0
        assert index_manager.list_documents() == []

    def test_rebuild_all_from_texts(self, index_manager: IndexManager) -> None:
        """rebuild_all 应清空后重新入库。"""
        # 先入库一份
        index_manager.add_text("旧内容", doc_id="old")
        # rebuild 入库新内容（不存在的路径视为文本）
        count = index_manager.rebuild_all(["新内容一", "新内容二"])
        assert count == 2
        assert index_manager.get_stats().total_docs == 2


class TestIncrementalUpdate:
    """增量索引：新增文档不影响已有索引。"""

    def test_add_new_doc_preserves_existing(self, index_manager: IndexManager) -> None:
        """新增文档不应删除已有文档的分块。"""
        index_manager.add_text("第一篇文档内容", doc_id="d1")
        size_after_first = index_manager._vector_store.size
        index_manager.add_text("第二篇文档内容", doc_id="d2")
        # 两篇文档分块应同时存在
        assert index_manager._vector_store.size > size_after_first
        assert index_manager.get_stats().total_docs == 2

    def test_add_same_doc_id_twice(self, index_manager: IndexManager) -> None:
        """相同 doc_id 文本不同时应作为增量更新（chunk_id 不同则新增）。"""
        index_manager.add_text("内容版本一", doc_id="dup")
        # 注意：当前实现下相同 doc_id 不同内容会生成不同 chunk_id
        # 第二次入库会新增分块到双索引
        index_manager.add_text("内容版本二", doc_id="dup")
        # 内部 _docs 字典以 doc_id 为键，重复入库会覆盖记录
        # 但向量库与 BM25 索引会累积
        assert index_manager._vector_store.size >= 1
