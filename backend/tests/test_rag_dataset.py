"""RAG 检索质量回归测试：基于标准测试集验证召回率与排序质量。

使用 30 条 query-doc 标准测试集，验证：
1. Recall@5 ≥ 0.6（验收标准：Top-5 中至少 3 条相关）
2. MRR ≥ 0.5（首个相关文档应排在靠前位置）
3. 纯向量检索 vs 混合检索对比（混合应更优）

集成到 CI：每次改动检索组件后自动运行，确保检索质量不退化。
"""

from __future__ import annotations

import uuid

import pytest

from app.knowledge.models.document import ChunkStrategy
from app.knowledge.services.bm25_index import BM25Index
from app.knowledge.services.chunker import DocumentChunker
from app.knowledge.services.document_parser import DocumentParser
from app.knowledge.services.embedding import MockEmbeddingService
from app.knowledge.services.hybrid_retriever import HybridRetriever
from app.knowledge.services.vector_store import VectorStore
from tests.rag_dataset import (
    compute_mrr,
    compute_recall_at_k,
    load_standard_dataset,
)


@pytest.fixture(scope="module")
def indexed_retriever() -> HybridRetriever:
    """构造已索引标准测试集的混合检索器（模块级共享，避免重复索引）。"""
    documents, _ = load_standard_dataset()
    parser = DocumentParser()
    chunker = DocumentChunker(chunk_size=80, chunk_overlap=16)
    vector_store = VectorStore(
        embedding_service=MockEmbeddingService(dimension=64),
        persist_directory="",
        collection_name=f"rag_dataset_{uuid.uuid4().hex[:8]}",
    )
    bm25_index = BM25Index()

    # 索引全部标准文档
    for doc in documents:
        parsed = parser.parse_text(doc["text"], doc_id=doc["doc_id"])
        chunks = chunker.chunk(parsed, strategy=ChunkStrategy.SEMANTIC)
        vector_store.add_chunks(chunks)
        bm25_index.add_chunks(chunks)

    return HybridRetriever(vector_store=vector_store, bm25_index=bm25_index)


class TestDatasetIntegrity:
    """标准测试集完整性校验。"""

    def test_documents_count_in_range(self) -> None:
        """文档数应在 10-50 之间。"""
        docs, _ = load_standard_dataset()
        assert 10 <= len(docs) <= 50

    def test_queries_count_in_range(self) -> None:
        """查询数应在 20-50 之间。"""
        _, queries = load_standard_dataset()
        assert 20 <= len(queries) <= 50

    def test_each_query_has_expected_docs(self) -> None:
        """每条 query 应标注至少 1 个相关文档。"""
        _, queries = load_standard_dataset()
        for q in queries:
            assert len(q["expected_doc_ids"]) >= 1
            assert q["query"].strip()

    def test_documents_have_unique_ids(self) -> None:
        """文档 ID 应唯一。"""
        docs, _ = load_standard_dataset()
        doc_ids = [d["doc_id"] for d in docs]
        assert len(doc_ids) == len(set(doc_ids))

    def test_expected_doc_ids_exist_in_documents(self) -> None:
        """query 标注的 expected_doc_ids 应在文档集中存在。"""
        docs, queries = load_standard_dataset()
        all_doc_ids = {d["doc_id"] for d in docs}
        for q in queries:
            for did in q["expected_doc_ids"]:
                assert did in all_doc_ids, f"doc_id {did} 不在文档集中"


class TestRetrievalQuality:
    """检索质量回归：Recall@K 与 MRR。"""

    def test_recall_at_5_meets_threshold(self, indexed_retriever: HybridRetriever) -> None:
        """Recall@5 应 ≥ 0.6（验收标准：Top-5 中至少 3 条相关）。"""
        _, queries = load_standard_dataset()
        total_recall = 0.0
        for q in queries:
            results = indexed_retriever.search(q["query"], top_k=5)
            retrieved_doc_ids = [r.doc_id for r in results]
            total_recall += compute_recall_at_k(retrieved_doc_ids, q["expected_doc_ids"], k=5)
        avg_recall = total_recall / len(queries)
        assert avg_recall >= 0.6, f"Recall@5 平均值 {avg_recall:.3f} 低于阈值 0.6"

    def test_recall_at_3_positive(self, indexed_retriever: HybridRetriever) -> None:
        """Recall@3 应 ≥ 0.4。"""
        _, queries = load_standard_dataset()
        total_recall = 0.0
        for q in queries:
            results = indexed_retriever.search(q["query"], top_k=3)
            retrieved_doc_ids = [r.doc_id for r in results]
            total_recall += compute_recall_at_k(retrieved_doc_ids, q["expected_doc_ids"], k=3)
        avg_recall = total_recall / len(queries)
        assert avg_recall >= 0.4

    def test_mrr_meets_threshold(self, indexed_retriever: HybridRetriever) -> None:
        """MRR 应 ≥ 0.5（首个相关文档应排在靠前位置）。"""
        _, queries = load_standard_dataset()
        total_mrr = 0.0
        for q in queries:
            results = indexed_retriever.search(q["query"], top_k=5)
            retrieved_doc_ids = [r.doc_id for r in results]
            total_mrr += compute_mrr(retrieved_doc_ids, q["expected_doc_ids"])
        avg_mrr = total_mrr / len(queries)
        assert avg_mrr >= 0.5, f"MRR 平均值 {avg_mrr:.3f} 低于阈值 0.5"


class TestHybridVsVectorOnly:
    """混合检索 vs 纯向量检索对比：混合应不劣于纯向量。"""

    def test_hybrid_recall_not_worse_than_vector(self, indexed_retriever: HybridRetriever) -> None:
        """混合检索 Recall@5 应不低于纯向量检索。"""
        _, queries = load_standard_dataset()

        hybrid_recall = 0.0
        vector_only_recall = 0.0
        for q in queries:
            # 混合检索
            hybrid_results = indexed_retriever.search(q["query"], top_k=5)
            hybrid_doc_ids = [r.doc_id for r in hybrid_results]
            hybrid_recall += compute_recall_at_k(hybrid_doc_ids, q["expected_doc_ids"], k=5)

            # 纯向量检索（绕过 BM25）
            vector_results = indexed_retriever._vector_store.search(q["query"], top_k=5)
            vector_doc_ids = [r.doc_id for r in vector_results]
            vector_only_recall += compute_recall_at_k(vector_doc_ids, q["expected_doc_ids"], k=5)

        hybrid_avg = hybrid_recall / len(queries)
        vector_avg = vector_only_recall / len(queries)
        # 混合检索应不劣于纯向量
        assert hybrid_avg >= vector_avg - 0.05  # 允许 5% 波动


class TestMetricsFunctions:
    """评估指标函数测试。"""

    def test_recall_at_k_full_hit(self) -> None:
        """全部相关文档在前 K 条返回时 Recall@K = 1.0。"""
        assert compute_recall_at_k(["d1", "d2", "d3"], ["d1", "d2"], k=5) == 1.0

    def test_recall_at_k_partial_hit(self) -> None:
        """部分相关文档在前 K 条时返回部分召回率。"""
        # d1 在前 5，d4 不在
        assert compute_recall_at_k(["d1", "d2", "d3"], ["d1", "d4"], k=5) == 0.5

    def test_recall_at_k_no_hit(self) -> None:
        """无相关文档命中时返回 0。"""
        assert compute_recall_at_k(["d1", "d2"], ["d3", "d4"], k=5) == 0.0

    def test_recall_at_k_empty_relevant(self) -> None:
        """无相关文档标注时返回 0。"""
        assert compute_recall_at_k(["d1"], [], k=5) == 0.0

    def test_mrr_first_position(self) -> None:
        """首个相关文档在第 1 位时 MRR = 1.0。"""
        assert compute_mrr(["d1", "d2"], ["d1"]) == 1.0

    def test_mrr_second_position(self) -> None:
        """首个相关文档在第 2 位时 MRR = 0.5。"""
        assert compute_mrr(["d1", "d2"], ["d2"]) == 0.5

    def test_mrr_no_hit(self) -> None:
        """无相关文档命中时 MRR = 0。"""
        assert compute_mrr(["d1", "d2"], ["d3"]) == 0.0
