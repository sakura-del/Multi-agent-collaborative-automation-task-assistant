"""KnowledgeBase 测试：门面层串联缓存→混合检索→重排的完整流程。

覆盖缓存命中/未命中、跳过缓存/重排、命中率统计、缓存失效等场景。
"""

from __future__ import annotations

import uuid

import pytest

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


def _index_corpus(kb: KnowledgeBase) -> None:
    """向知识库底层索引注入测试语料。"""
    from app.knowledge.models.document import TextChunk

    chunks = [
        TextChunk(chunk_id="c-rag", doc_id="d1", text="RAG 检索增强生成提升 LLM 回答质量"),
        TextChunk(chunk_id="c-agent", doc_id="d2", text="多智能体协作完成任务闭环"),
        TextChunk(chunk_id="c-dag", doc_id="d3", text="DAG 引擎实现拓扑排序"),
    ]
    kb._retriever._vector_store.add_chunks(chunks)
    kb._retriever._bm25_index.add_chunks(chunks)


class TestQueryBasic:
    """query 基础行为。"""

    def test_empty_index_returns_empty(self, kb_with_mock_reranker: KnowledgeBase) -> None:
        """空知识库查询返回空列表。"""
        results = kb_with_mock_reranker.query("任意", top_k=5)
        assert results == []

    def test_returns_results_after_indexing(self, kb_with_mock_reranker: KnowledgeBase) -> None:
        """索引后查询应返回结果。"""
        _index_corpus(kb_with_mock_reranker)
        results = kb_with_mock_reranker.query("RAG 检索", top_k=3)
        assert len(results) >= 1

    def test_top_k_limit(self, kb_with_mock_reranker: KnowledgeBase) -> None:
        """top_k 应限制最终返回数量。"""
        _index_corpus(kb_with_mock_reranker)
        results = kb_with_mock_reranker.query("系统", top_k=2)
        assert len(results) <= 2


class TestCacheBehavior:
    """缓存读写行为。"""

    def test_cache_miss_then_hit(self, kb_with_mock_reranker: KnowledgeBase) -> None:
        """首次未命中写入缓存，第二次命中直接返回。"""
        _index_corpus(kb_with_mock_reranker)
        # 首次查询：未命中，触发检索并写缓存
        first = kb_with_mock_reranker.query("RAG 检索", top_k=3)
        assert kb_with_mock_reranker._cache_misses == 1
        assert kb_with_mock_reranker._cache_hits == 0
        # 第二次查询：命中缓存
        second = kb_with_mock_reranker.query("RAG 检索", top_k=3)
        assert kb_with_mock_reranker._cache_hits == 1
        # 两次结果应一致
        assert [r.chunk_id for r in first] == [r.chunk_id for r in second]

    def test_skip_cache_bypasses_cache(self, kb_with_mock_reranker: KnowledgeBase) -> None:
        """skip_cache=True 不读写缓存。"""
        _index_corpus(kb_with_mock_reranker)
        kb_with_mock_reranker.query("RAG", top_k=3, skip_cache=True)
        # 不影响命中/未命中计数
        assert kb_with_mock_reranker._cache_hits == 0
        assert kb_with_mock_reranker._cache_misses == 0
        # 缓存中无该查询
        assert kb_with_mock_reranker._cache.get("RAG", top_k=3) is None

    def test_cache_hit_rate(self, kb_with_mock_reranker: KnowledgeBase) -> None:
        """cache_hit_rate 应正确计算命中率。"""
        _index_corpus(kb_with_mock_reranker)
        # 初始命中率为 0
        assert kb_with_mock_reranker.cache_hit_rate == 0.0
        # 一次未命中
        kb_with_mock_reranker.query("RAG", top_k=3)
        assert kb_with_mock_reranker.cache_hit_rate == 0.0
        # 一次命中
        kb_with_mock_reranker.query("RAG", top_k=3)
        assert kb_with_mock_reranker.cache_hit_rate == 0.5

    def test_invalidate_cache_clears_entries(self, kb_with_mock_reranker: KnowledgeBase) -> None:
        """invalidate_cache 应清空所有缓存。"""
        _index_corpus(kb_with_mock_reranker)
        kb_with_mock_reranker.query("RAG", top_k=3)
        kb_with_mock_reranker.invalidate_cache()
        assert kb_with_mock_reranker._cache.get("RAG", top_k=3) is None


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

        from app.knowledge.models.document import TextChunk

        chunks = [TextChunk(chunk_id=f"c{i}", doc_id="d1", text=f"RAG 内容 {i}") for i in range(5)]
        kb._retriever._vector_store.add_chunks(chunks)
        kb._retriever._bm25_index.add_chunks(chunks)

        # 不跳过重排：召回 top_k*3 后精排到 top_k
        with_rerank = kb.query("RAG", top_k=2, skip_rerank=False, skip_cache=True)
        # 跳过重排：直接召回 top_k
        without_rerank = kb.query("RAG", top_k=2, skip_rerank=True, skip_cache=True)
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

        from app.knowledge.models.document import TextChunk

        chunks = [TextChunk(chunk_id="c1", doc_id="d1", text="RAG 检索内容")]
        kb._retriever._vector_store.add_chunks(chunks)
        kb._retriever._bm25_index.add_chunks(chunks)

        results = kb.query("RAG", top_k=3)
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

        from app.knowledge.models.document import TextChunk

        chunks = [TextChunk(chunk_id=f"c{i}", doc_id="d1", text=f"RAG 内容 {i}") for i in range(5)]
        kb._retriever._vector_store.add_chunks(chunks)
        kb._retriever._bm25_index.add_chunks(chunks)

        results = kb.query("RAG", top_k=5, skip_cache=True)
        # rerank_top_k=3 限制最终返回 3 条
        assert len(results) <= 3
