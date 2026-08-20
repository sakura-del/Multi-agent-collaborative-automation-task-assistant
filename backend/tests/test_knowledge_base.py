"""KnowledgeBase 测试：门面层串联缓存→混合检索→重排的完整流程。

覆盖缓存命中/未命中、跳过缓存/重排、命中率统计、按 namespace 隔离等场景。
query() 需要显式 where（过滤条件）与 namespace（缓存隔离），按 user_id 传参。
"""

from __future__ import annotations

import uuid

import pytest

from app.knowledge.models.document import TextChunk
from app.knowledge.services.bm25_index import BM25Index
from app.knowledge.services.cache import RetrievalCache
from app.knowledge.services.embedding import MockEmbeddingService
from app.knowledge.services.hybrid_retriever import HybridRetriever
from app.knowledge.services.knowledge_base import KnowledgeBase
from app.knowledge.services.reranker import MockReranker
from app.knowledge.services.vector_store import VectorStore


@pytest.fixture
def kb_with_mock_reranker() -> KnowledgeBase:
    """构造带 Mock 重排器的知识库：内存模式，全链路可运行。"""
    vector_store = VectorStore(
        embedding_service=MockEmbeddingService(dimension=64),
        persist_directory="",
        collection_name=f"test_{uuid.uuid4().hex[:8]}",
    )
    bm25_index = BM25Index()
    retriever = HybridRetriever(vector_store=vector_store, bm25_index=bm25_index)
    return KnowledgeBase(
        retriever=retriever,
        reranker=MockReranker(),
        cache=RetrievalCache(),
    )


def _index_corpus(kb: KnowledgeBase, user_id: str = "user-a") -> None:
    """向知识库底层索引注入带 user_id 元数据的测试语料。"""
    chunks = [
        TextChunk(
            chunk_id="c-rag",
            doc_id="d1",
            text="RAG 检索增强生成提升 LLM 回答质量",
            metadata={"user_id": user_id},
        ),
        TextChunk(
            chunk_id="c-agent",
            doc_id="d2",
            text="多智能体协作完成任务闭环",
            metadata={"user_id": user_id},
        ),
        TextChunk(
            chunk_id="c-dag",
            doc_id="d3",
            text="DAG 引擎实现拓扑排序",
            metadata={"user_id": user_id},
        ),
    ]
    kb._retriever._vector_store.add_chunks(chunks)
    kb._retriever._bm25_index.add_chunks(chunks)


class TestQueryBasic:
    """query 基础行为。"""

    def test_empty_index_returns_empty(self, kb_with_mock_reranker: KnowledgeBase) -> None:
        """空知识库查询返回空列表。"""
        results = kb_with_mock_reranker.query(
            "任意", top_k=5, where={"user_id": "user-a"}, namespace="user-a"
        )
        assert results == []

    def test_returns_results_after_indexing(self, kb_with_mock_reranker: KnowledgeBase) -> None:
        """索引后查询应返回结果。"""
        _index_corpus(kb_with_mock_reranker)
        results = kb_with_mock_reranker.query(
            "RAG 检索", top_k=3, where={"user_id": "user-a"}, namespace="user-a"
        )
        assert len(results) >= 1
        assert all(r.metadata.get("user_id") == "user-a" for r in results)

    def test_top_k_limit(self, kb_with_mock_reranker: KnowledgeBase) -> None:
        """top_k 应限制最终返回数量。"""
        _index_corpus(kb_with_mock_reranker)
        results = kb_with_mock_reranker.query(
            "系统", top_k=2, where={"user_id": "user-a"}, namespace="user-a"
        )
        assert len(results) <= 2

    def test_cross_user_isolation(self, kb_with_mock_reranker: KnowledgeBase) -> None:
        """两个用户的语料同库共存时，where 过滤保证互不可见。"""
        _index_corpus(kb_with_mock_reranker, user_id="user-a")
        extra = [
            TextChunk(
                chunk_id=f"c-b{i}",
                doc_id=f"db{i}",
                text=f"用户B的专属内容 编号 {i}",
                metadata={"user_id": "user-b"},
            )
            for i in range(3)
        ]
        kb_with_mock_reranker._retriever._vector_store.add_chunks(extra)
        kb_with_mock_reranker._retriever._bm25_index.add_chunks(extra)

        results_a = kb_with_mock_reranker.query(
            "内容", top_k=10, where={"user_id": "user-a"}, namespace="user-a"
        )
        results_b = kb_with_mock_reranker.query(
            "内容", top_k=10, where={"user_id": "user-b"}, namespace="user-b"
        )
        if results_a:
            assert all(r.metadata.get("user_id") == "user-a" for r in results_a)
        if results_b:
            assert all(r.metadata.get("user_id") == "user-b" for r in results_b)


class TestCacheBehavior:
    """缓存读写行为。"""

    def test_cache_miss_then_hit(self, kb_with_mock_reranker: KnowledgeBase) -> None:
        """首次未命中写入缓存，第二次命中直接返回。"""
        _index_corpus(kb_with_mock_reranker)
        # 首次查询：未命中，触发检索并写缓存
        first = kb_with_mock_reranker.query(
            "RAG 检索", top_k=3, where={"user_id": "user-a"}, namespace="user-a"
        )
        assert kb_with_mock_reranker._cache_misses == 1
        assert kb_with_mock_reranker._cache_hits == 0
        # 第二次查询：命中缓存
        second = kb_with_mock_reranker.query(
            "RAG 检索", top_k=3, where={"user_id": "user-a"}, namespace="user-a"
        )
        assert kb_with_mock_reranker._cache_hits == 1
        # 两次结果应一致
        assert [r.chunk_id for r in first] == [r.chunk_id for r in second]

    def test_cache_namespace_isolation(self, kb_with_mock_reranker: KnowledgeBase) -> None:
        """同 query 不同 namespace 不串缓存。"""
        _index_corpus(kb_with_mock_reranker)
        kb_with_mock_reranker.query("RAG", top_k=3, where={"user_id": "user-a"}, namespace="user-a")
        assert kb_with_mock_reranker._cache.get("user-b", "RAG", top_k=3) is None
        # user-b 查询不会命中 user-a 的缓存
        kb_with_mock_reranker.query("RAG", top_k=3, where={"user_id": "user-b"}, namespace="user-b")
        assert kb_with_mock_reranker._cache_hits == 0

    def test_skip_cache_bypasses_cache(self, kb_with_mock_reranker: KnowledgeBase) -> None:
        """skip_cache=True 不读写缓存。"""
        _index_corpus(kb_with_mock_reranker)
        kb_with_mock_reranker.query(
            "RAG", top_k=3, where={"user_id": "user-a"}, namespace="user-a", skip_cache=True
        )
        # 不影响命中/未命中计数
        assert kb_with_mock_reranker._cache_hits == 0
        assert kb_with_mock_reranker._cache_misses == 0
        # 缓存中无该查询
        assert kb_with_mock_reranker._cache.get("user-a", "RAG", top_k=3) is None

    def test_cache_hit_rate(self, kb_with_mock_reranker: KnowledgeBase) -> None:
        """cache_hit_rate 应正确计算命中率。"""
        _index_corpus(kb_with_mock_reranker)
        # 初始命中率为 0
        assert kb_with_mock_reranker.cache_hit_rate == 0.0
        # 一次未命中
        kb_with_mock_reranker.query("RAG", top_k=3, where={"user_id": "user-a"}, namespace="user-a")
        assert kb_with_mock_reranker.cache_hit_rate == 0.0
        # 一次命中
        kb_with_mock_reranker.query("RAG", top_k=3, where={"user_id": "user-a"}, namespace="user-a")
        assert kb_with_mock_reranker.cache_hit_rate == 0.5

    def test_invalidate_cache_clears_namespace(self, kb_with_mock_reranker: KnowledgeBase) -> None:
        """invalidate_cache(namespace) 只清空指定 namespace。"""
        _index_corpus(kb_with_mock_reranker)
        kb_with_mock_reranker.query("RAG", top_k=3, where={"user_id": "user-a"}, namespace="user-a")
        kb_with_mock_reranker.invalidate_cache("user-a")
        assert kb_with_mock_reranker._cache.get("user-a", "RAG", top_k=3) is None


class TestRerankerBehavior:
    """重排行为。"""

    def test_skip_rerank_returns_more_results(self) -> None:
        """skip_rerank=True 时召回 top_k 不放大，直接截断。"""
        vector_store = VectorStore(
            MockEmbeddingService(64),
            persist_directory="",
            collection_name=f"sr_{uuid.uuid4().hex[:8]}",
        )
        bm25_index = BM25Index()
        retriever = HybridRetriever(vector_store=vector_store, bm25_index=bm25_index)
        kb = KnowledgeBase(retriever=retriever, reranker=MockReranker(), cache=None)

        chunks = [
            TextChunk(
                chunk_id=f"c{i}", doc_id="d1", text=f"RAG 内容 {i}", metadata={"user_id": "user-a"}
            )
            for i in range(5)
        ]
        kb._retriever._vector_store.add_chunks(chunks)
        kb._retriever._bm25_index.add_chunks(chunks)

        # 不跳过重排：召回 top_k*3 后精排到 top_k
        with_rerank = kb.query(
            "RAG", top_k=2, where={"user_id": "user-a"}, namespace="user-a", skip_rerank=False
        )
        # 跳过重排：直接召回 top_k
        without_rerank = kb.query(
            "RAG", top_k=2, where={"user_id": "user-a"}, namespace="user-a", skip_rerank=True
        )
        # 两者都最多 2 条
        assert len(with_rerank) <= 2
        assert len(without_rerank) <= 2

    def test_no_reranker_skips_rerank_step(self) -> None:
        """未配置重排器时应直接截断返回。"""
        vector_store = VectorStore(
            MockEmbeddingService(64),
            persist_directory="",
            collection_name=f"nr_{uuid.uuid4().hex[:8]}",
        )
        bm25_index = BM25Index()
        retriever = HybridRetriever(vector_store=vector_store, bm25_index=bm25_index)
        kb = KnowledgeBase(retriever=retriever, reranker=None, cache=None)

        chunks = [
            TextChunk(
                chunk_id="c1", doc_id="d1", text="RAG 检索内容", metadata={"user_id": "user-a"}
            )
        ]
        kb._retriever._vector_store.add_chunks(chunks)
        kb._retriever._bm25_index.add_chunks(chunks)

        results = kb.query("RAG", top_k=3, where={"user_id": "user-a"}, namespace="user-a")
        assert len(results) >= 1


class TestRerankTopK:
    """rerank_top_k 配置。"""

    def test_custom_rerank_top_k(self) -> None:
        """自定义 rerank_top_k 应限制重排后结果数。"""
        vector_store = VectorStore(
            MockEmbeddingService(64),
            persist_directory="",
            collection_name=f"rk_{uuid.uuid4().hex[:8]}",
        )
        bm25_index = BM25Index()
        retriever = HybridRetriever(vector_store=vector_store, bm25_index=bm25_index)
        kb = KnowledgeBase(
            retriever=retriever,
            reranker=MockReranker(),
            cache=None,
            rerank_top_k=3,
        )

        chunks = [
            TextChunk(
                chunk_id=f"c{i}", doc_id="d1", text=f"RAG 内容 {i}", metadata={"user_id": "user-a"}
            )
            for i in range(5)
        ]
        kb._retriever._vector_store.add_chunks(chunks)
        kb._retriever._bm25_index.add_chunks(chunks)

        results = kb.query(
            "RAG", top_k=5, where={"user_id": "user-a"}, namespace="user-a", skip_cache=True
        )
        # rerank_top_k=3 限制最终返回 3 条
        assert len(results) <= 3
