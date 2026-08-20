"""知识库组件工厂测试：单例初始化、获取与重置。

使用 force_mock 模式避免下载模型，chroma_persist_dir="" 启用内存模式。
每个测试后调用 reset_kb 清理全局状态。
"""

from __future__ import annotations

import pytest

from app.knowledge.services import factory
from app.knowledge.services.bm25_index import BM25Index
from app.knowledge.services.cache import RetrievalCache
from app.knowledge.services.chunker import DocumentChunker
from app.knowledge.services.document_parser import DocumentParser
from app.knowledge.services.embedding import MockEmbeddingService
from app.knowledge.services.factory import (
    KnowledgeBaseNotInitializedError,
    get_index_manager,
    get_kb,
    init_kb,
    reset_kb,
)
from app.knowledge.services.hybrid_retriever import HybridRetriever
from app.knowledge.services.index_manager import IndexManager
from app.knowledge.services.knowledge_base import KnowledgeBase
from app.knowledge.services.reranker import MockReranker
from app.knowledge.services.vector_store import VectorStore


@pytest.fixture(autouse=True)
def _isolate_kb_state():
    """每个测试前后清理全局单例，避免相互污染。"""
    reset_kb()
    yield
    reset_kb()


class TestInitKb:
    """init_kb 初始化全部组件。"""

    def test_init_returns_knowledge_base(self) -> None:
        """init_kb 应返回 KnowledgeBase 实例。"""
        kb = init_kb(
            force_mock_embedding=True,
            force_mock_reranker=True,
            chroma_persist_dir="",
        )
        assert isinstance(kb, KnowledgeBase)

    def test_init_creates_all_components(self) -> None:
        """初始化后所有组件单例应已创建。"""
        init_kb(
            force_mock_embedding=True,
            force_mock_reranker=True,
            chroma_persist_dir="",
        )
        assert factory._embedding is not None
        assert factory._reranker is not None
        assert factory._vector_store is not None
        assert factory._bm25_index is not None
        assert factory._parser is not None
        assert factory._chunker is not None
        assert factory._index_manager is not None
        assert factory._cache is not None
        assert factory._kb is not None

    def test_init_idempotent(self) -> None:
        """重复调用 init_kb 应返回同一单例。"""
        kb1 = init_kb(
            force_mock_embedding=True,
            force_mock_reranker=True,
            chroma_persist_dir="",
        )
        kb2 = init_kb()
        assert kb1 is kb2

    def test_init_with_mock_components(self) -> None:
        """force_mock 应使用 Mock 嵌入与重排器。"""
        init_kb(
            force_mock_embedding=True,
            force_mock_reranker=True,
            chroma_persist_dir="",
        )
        assert isinstance(factory._embedding, MockEmbeddingService)
        assert isinstance(factory._reranker, MockReranker)

    def test_init_component_types(self) -> None:
        """各组件应为期望类型。"""
        init_kb(
            force_mock_embedding=True,
            force_mock_reranker=True,
            chroma_persist_dir="",
        )
        assert isinstance(factory._parser, DocumentParser)
        assert isinstance(factory._chunker, DocumentChunker)
        assert isinstance(factory._vector_store, VectorStore)
        assert isinstance(factory._bm25_index, BM25Index)
        assert isinstance(factory._index_manager, IndexManager)
        assert isinstance(factory._cache, RetrievalCache)
        assert isinstance(factory._kb._retriever, HybridRetriever)


class TestGetKb:
    """get_kb 单例获取：未初始化时显式报错（不隐式 Mock 回退）。"""

    def test_get_kb_raises_when_not_initialized(self) -> None:
        """未初始化时 get_kb 应抛出明确的初始化错误。"""
        with pytest.raises(KnowledgeBaseNotInitializedError):
            get_kb()

    def test_get_kb_returns_same_instance(self) -> None:
        """初始化后多次调用 get_kb 应返回同一单例。"""
        kb1 = init_kb(
            force_mock_embedding=True,
            force_mock_reranker=True,
            chroma_persist_dir="",
        )
        kb2 = get_kb()
        assert kb1 is kb2


class TestGetIndexManager:
    """get_index_manager 单例获取。"""

    def test_raises_when_not_initialized(self) -> None:
        """未初始化时应抛出明确的初始化错误。"""
        with pytest.raises(KnowledgeBaseNotInitializedError):
            get_index_manager()

    def test_returns_index_manager(self) -> None:
        """初始化后应返回 IndexManager 实例。"""
        init_kb(
            force_mock_embedding=True,
            force_mock_reranker=True,
            chroma_persist_dir="",
        )
        mgr = get_index_manager()
        assert isinstance(mgr, IndexManager)
        assert mgr is factory._index_manager


class TestResetKb:
    """reset_kb 清理全局状态。"""

    def test_reset_clears_singletons(self) -> None:
        """reset 后所有单例应为 None。"""
        init_kb(
            force_mock_embedding=True,
            force_mock_reranker=True,
            chroma_persist_dir="",
        )
        reset_kb()
        assert factory._kb is None
        assert factory._index_manager is None
        assert factory._embedding is None
        assert factory._reranker is None

    def test_reset_allows_reinit(self) -> None:
        """reset 后可重新初始化。"""
        init_kb(force_mock_embedding=True, force_mock_reranker=True, chroma_persist_dir="")
        reset_kb()
        # 重新初始化应正常工作
        kb = init_kb(force_mock_embedding=True, force_mock_reranker=True, chroma_persist_dir="")
        assert isinstance(kb, KnowledgeBase)

    def test_reset_without_init_no_error(self) -> None:
        """未初始化时 reset 不应抛出异常。"""
        reset_kb()  # 不应抛出
