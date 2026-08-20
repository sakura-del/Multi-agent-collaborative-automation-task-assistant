"""混合检索引擎：向量检索 + BM25 检索的 RRF 融合。

核心算法：Reciprocal Rank Fusion（RRF）
    score(d) = Σ 1 / (k + rank_i(d))
其中 k 为平滑常数（默认 60），rank_i(d) 为文档 d 在第 i 路检索中的排名。

RRF 优势：
- 无需两路得分归一化（向量余弦分与 BM25 分量纲不同）
- 对排名而非得分敏感，鲁棒性更强

设计要点：
- 双路检索：向量（语义匹配）+ BM25（关键词匹配）互补
- 可调权重：支持向量/BM25 路权重配比
- 去重合并：同一 chunk_id 在两路均命中时合并得分
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.knowledge.models.document import RetrievalResult
from app.knowledge.services.bm25_index import BM25Index
from app.knowledge.services.vector_store import VectorStore

logger = get_logger(__name__)

# RRF 平滑常数：标准值 60，来源于 RRF 原论文
_RRF_K = 60


class HybridRetriever:
    """混合检索器：向量 + BM25 双路检索 + RRF 融合排序。

    用法：
        retriever = HybridRetriever(vector_store=vs, bm25_index=bm25)
        results = retriever.search("查询文本", top_k=5)
    """

    def __init__(
        self,
        vector_store: VectorStore,
        bm25_index: BM25Index,
        *,
        vector_weight: float = 1.0,
        bm25_weight: float = 1.0,
        rrf_k: int = _RRF_K,
    ) -> None:
        self._vector_store = vector_store
        self._bm25_index = bm25_index
        self._vector_weight = vector_weight
        self._bm25_weight = bm25_weight
        self._rrf_k = rrf_k

    def search(
        self,
        query: str,
        top_k: int = 10,
        *,
        where: dict[str, Any] | None = None,
        vector_top_k: int | None = None,
        bm25_top_k: int | None = None,
    ) -> list[RetrievalResult]:
        """混合检索：并行执行向量与 BM25 检索，RRF 融合后返回 top_k。

        vector_top_k / bm25_top_k 控制各路召回数量，默认为 top_k * 2 以提高融合质量。
        where 为元数据过滤条件（如 {"user_id": "u1"}），两路检索均生效。
        """
        # 召回数量：各路取更多候选，融合后截断到 top_k
        v_k = vector_top_k or max(top_k * 2, top_k)
        b_k = bm25_top_k or max(top_k * 2, top_k)

        # 双路检索（均按 where 过滤，保证融合前候选已隔离）
        vector_results = (
            self._vector_store.search(query, top_k=v_k, where=where)
            if self._vector_store.is_ready
            else []
        )
        bm25_results = (
            self._bm25_index.search(query, top_k=b_k, where=where)
            if self._bm25_index.is_ready
            else []
        )

        # RRF 融合
        fused = self._rrf_fuse(vector_results, bm25_results)

        # 截断到 top_k
        results = fused[:top_k]
        logger.info(
            "hybrid_search_done",
            query_len=len(query),
            vector_hits=len(vector_results),
            bm25_hits=len(bm25_results),
            fused=len(results),
        )
        return results

    def _rrf_fuse(
        self,
        vector_results: list[RetrievalResult],
        bm25_results: list[RetrievalResult],
    ) -> list[RetrievalResult]:
        """RRF 融合两路检索结果。

        对每个 chunk_id，计算其在各路中的 RRF 得分加权和。
        """
        # chunk_id → 融合得分
        scores: dict[str, float] = {}
        # chunk_id → RetrievalResult（保留首次出现的完整信息）
        meta: dict[str, RetrievalResult] = {}

        # 向量路：按已有排名计算 RRF 分
        for rank, result in enumerate(vector_results):
            rrf_score = self._vector_weight / (self._rrf_k + rank + 1)
            scores[result.chunk_id] = scores.get(result.chunk_id, 0.0) + rrf_score
            if result.chunk_id not in meta:
                meta[result.chunk_id] = result

        # BM25 路
        for rank, result in enumerate(bm25_results):
            rrf_score = self._bm25_weight / (self._rrf_k + rank + 1)
            scores[result.chunk_id] = scores.get(result.chunk_id, 0.0) + rrf_score
            if result.chunk_id not in meta:
                meta[result.chunk_id] = result

        # 按融合得分降序排序
        sorted_ids = sorted(scores, key=lambda cid: scores[cid], reverse=True)

        # 构建结果（更新 score 为 RRF 融合分，source 标记为 hybrid）
        results: list[RetrievalResult] = []
        for chunk_id in sorted_ids:
            original = meta[chunk_id]
            results.append(
                RetrievalResult(
                    chunk_id=chunk_id,
                    doc_id=original.doc_id,
                    text=original.text,
                    score=scores[chunk_id],
                    metadata=original.metadata,
                    source="hybrid",
                )
            )
        return results
