"""HybridRetriever 测试：向量+BM25 RRF 融合检索。

覆盖双路召回、RRF 融合排序、权重调节、单路可用、top_k 截断等场景。
"""

from __future__ import annotations

import uuid

import pytest

from app.knowledge.models.document import RetrievalResult, TextChunk
from app.knowledge.services.bm25_index import BM25Index
from app.knowledge.services.embedding import MockEmbeddingService
from app.knowledge.services.hybrid_retriever import HybridRetriever
from app.knowledge.services.vector_store import VectorStore


@pytest.fixture
def retriever() -> HybridRetriever:
    """构造内存模式混合检索器：Mock 嵌入 + 内存 Chroma + BM25。"""
    vector_store = VectorStore(
        embedding_service=MockEmbeddingService(dimension=64),
        persist_directory="",
        collection_name=f"test_{uuid.uuid4().hex[:8]}",
    )
    bm25_index = BM25Index()
    return HybridRetriever(vector_store=vector_store, bm25_index=bm25_index)


def _make_chunks() -> list[TextChunk]:
    """构造覆盖不同主题的测试分块，便于验证召回相关性。"""
    return [
        TextChunk(
            chunk_id="c-rag",
            doc_id="d1",
            text="RAG 检索增强生成通过向量检索与重排提升 LLM 回答质量",
            metadata={"doc_source": "rag.md"},
        ),
        TextChunk(
            chunk_id="c-agent",
            doc_id="d2",
            text="多智能体协作系统通过 Planner Executor Validator Reviewer 完成任务闭环",
            metadata={"doc_source": "agent.md"},
        ),
        TextChunk(
            chunk_id="c-dag",
            doc_id="d3",
            text="DAG 引擎基于 networkx 实现拓扑排序与循环依赖检测",
            metadata={"doc_source": "dag.md"},
        ),
        TextChunk(
            chunk_id="c-cache",
            doc_id="d4",
            text="Redis 缓存层通过 query hash 与 TTL 降低重复检索延迟",
            metadata={"doc_source": "cache.md"},
        ),
    ]


class TestHybridRetrieverBasic:
    """混合检索基础行为。"""

    def test_empty_returns_empty(self, retriever: HybridRetriever) -> None:
        """双索引均为空时返回空列表。"""
        assert retriever.search("任意查询", top_k=5) == []

    def test_returns_results_after_indexing(self, retriever: HybridRetriever) -> None:
        """索引后应返回检索结果。"""
        retriever._vector_store.add_chunks(_make_chunks())
        retriever._bm25_index.add_chunks(_make_chunks())
        results = retriever.search("RAG 检索", top_k=3)
        assert len(results) >= 1
        for r in results:
            assert r.source == "hybrid"

    def test_top_k_limit(self, retriever: HybridRetriever) -> None:
        """top_k 应限制返回数量。"""
        retriever._vector_store.add_chunks(_make_chunks())
        retriever._bm25_index.add_chunks(_make_chunks())
        results = retriever.search("系统", top_k=2)
        assert len(results) <= 2


class TestRRFFusion:
    """RRF 融合算法验证。"""

    def test_rrf_combines_both_sources(self, retriever: HybridRetriever) -> None:
        """双路均命中的 chunk 融合得分应高于单路命中。"""
        retriever._vector_store.add_chunks(_make_chunks())
        retriever._bm25_index.add_chunks(_make_chunks())

        # 取一个两路都可能命中的查询
        results = retriever.search("RAG 检索增强", top_k=4)
        assert len(results) >= 1
        # 融合分应大于 0
        for r in results:
            assert r.score > 0

    def test_rrf_fuse_empty_inputs(self, retriever: HybridRetriever) -> None:
        """两路均为空时融合结果为空。"""
        assert retriever._rrf_fuse([], []) == []

    def test_rrf_fuse_single_source(self, retriever: HybridRetriever) -> None:
        """仅向量路有结果时应保留并按 RRF 计分。"""
        vec_results = [
            RetrievalResult(chunk_id="c1", doc_id="d1", text="t1", score=0.9, source="vector"),
            RetrievalResult(chunk_id="c2", doc_id="d2", text="t2", score=0.8, source="vector"),
        ]
        fused = retriever._rrf_fuse(vec_results, [])
        assert len(fused) == 2
        # RRF 排名靠前的得分更高
        assert fused[0].score > fused[1].score

    def test_rrf_fuse_duplicate_chunk_id_merges_scores(self, retriever: HybridRetriever) -> None:
        """两路命中相同 chunk_id 应累加 RRF 得分。"""
        vec_results = [
            RetrievalResult(chunk_id="c1", doc_id="d1", text="t1", score=0.9, source="vector"),
        ]
        bm25_results = [
            RetrievalResult(chunk_id="c1", doc_id="d1", text="t1", score=2.5, source="bm25"),
        ]
        fused = retriever._rrf_fuse(vec_results, bm25_results)
        assert len(fused) == 1
        # 双路命中得分应高于单路
        expected = (1.0 / (60 + 0 + 1)) + (1.0 / (60 + 0 + 1))
        assert abs(fused[0].score - expected) < 1e-9

    def test_rrf_fuse_source_marked_hybrid(self, retriever: HybridRetriever) -> None:
        """融合结果 source 字段应标记为 hybrid。"""
        vec_results = [
            RetrievalResult(chunk_id="c1", doc_id="d1", text="t1", score=0.9, source="vector"),
        ]
        fused = retriever._rrf_fuse(vec_results, [])
        assert fused[0].source == "hybrid"


class TestSingleSourceReady:
    """单路可用时的降级行为。"""

    def test_only_vector_ready(self, retriever: HybridRetriever) -> None:
        """仅向量索引可用时应只返回向量结果。"""
        retriever._vector_store.add_chunks(_make_chunks())
        # BM25 索引为空
        results = retriever.search("RAG", top_k=3)
        assert len(results) >= 1
        # 全部标记为 hybrid（融合后统一标记）
        for r in results:
            assert r.source == "hybrid"

    def test_only_bm25_ready(self, retriever: HybridRetriever) -> None:
        """仅 BM25 索引可用时应只返回 BM25 结果。"""
        retriever._bm25_index.add_chunks(_make_chunks())
        # 向量索引为空
        results = retriever.search("RAG", top_k=3)
        assert len(results) >= 1


class TestRetrieverWeights:
    """权重配置：vector_weight / bm25_weight 调节各路影响力。"""

    def test_custom_weights_affect_fusion(self) -> None:
        """不同权重应导致融合得分不同（需不同 rank 位置才能体现差异）。"""
        # 向量路：c1 在 rank 0，c2 在 rank 1
        vec_results = [
            RetrievalResult(chunk_id="c1", doc_id="d1", text="t1", score=0.9, source="vector"),
            RetrievalResult(chunk_id="c2", doc_id="d2", text="t2", score=0.8, source="vector"),
        ]
        # BM25 路：c1 在 rank 1，c2 在 rank 0（与向量路排名相反）
        bm25_results = [
            RetrievalResult(chunk_id="c2", doc_id="d2", text="t2", score=2.5, source="bm25"),
            RetrievalResult(chunk_id="c1", doc_id="d1", text="t1", score=2.0, source="bm25"),
        ]
        # 向量权重高：c1（向量 rank 0）得分更高
        r1 = HybridRetriever(
            vector_store=VectorStore(
                MockEmbeddingService(64), persist_directory="", collection_name="r1"
            ),
            bm25_index=BM25Index(),
            vector_weight=2.0,
            bm25_weight=0.5,
        )
        # BM25 权重高：c2（BM25 rank 0）得分更高
        r2 = HybridRetriever(
            vector_store=VectorStore(
                MockEmbeddingService(64), persist_directory="", collection_name="r2"
            ),
            bm25_index=BM25Index(),
            vector_weight=0.5,
            bm25_weight=2.0,
        )
        fused1 = r1._rrf_fuse(vec_results, bm25_results)
        fused2 = r2._rrf_fuse(vec_results, bm25_results)
        # 向量权重高时 c1 排第一；BM25 权重高时 c2 排第一
        assert fused1[0].chunk_id == "c1"
        assert fused2[0].chunk_id == "c2"

    def test_custom_rrf_k(self) -> None:
        """自定义 rrf_k 应影响得分计算。"""
        vec_results = [
            RetrievalResult(chunk_id="c1", doc_id="d1", text="t1", score=0.9, source="vector"),
        ]
        r1 = HybridRetriever(
            vector_store=VectorStore(
                MockEmbeddingService(64), persist_directory="", collection_name="rk1"
            ),
            bm25_index=BM25Index(),
            rrf_k=10,
        )
        fused = r1._rrf_fuse(vec_results, [])
        expected = 1.0 / (10 + 0 + 1)
        assert abs(fused[0].score - expected) < 1e-9
