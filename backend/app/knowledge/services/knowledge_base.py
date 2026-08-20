"""知识库服务门面：统一检索入口，串联混合检索→重排→缓存。

对上层（Agent / API）提供极简接口：
    kb = KnowledgeBase(...)
    results = kb.query("查询文本", top_k=5)

内部自动完成：
1. 缓存查询 → 命中则直接返回
2. 混合检索（向量 + BM25 RRF 融合）
3. 重排精排（BGE-Reranker 或 Mock）
4. 写入缓存

设计要点：
- 门面模式：隐藏内部组件交互复杂度，上层只需一个接口
- 依赖注入：所有组件通过构造函数注入，便于测试用 Mock
- 可观测性：记录每步耗时与命中信息，便于性能调优
"""

from __future__ import annotations

import time
from typing import Any

from app.core.logging import get_logger
from app.knowledge.models.document import RetrievalResult
from app.knowledge.services.cache import RetrievalCache
from app.knowledge.services.hybrid_retriever import HybridRetriever
from app.knowledge.services.reranker import BaseReranker

logger = get_logger(__name__)


class KnowledgeBase:
    """知识库服务门面：统一检索入口。

    用法：
        kb = KnowledgeBase(retriever=retriever, reranker=reranker, cache=cache)
        results = kb.query("什么是 RAG？", top_k=5)
    """

    def __init__(
        self,
        retriever: HybridRetriever,
        reranker: BaseReranker | None = None,
        cache: RetrievalCache | None = None,
        *,
        rerank_top_k: int | None = None,
    ) -> None:
        self._retriever = retriever
        self._reranker = reranker
        self._cache = cache
        # 重排后保留的结果数，默认与检索 top_k 一致
        self._rerank_top_k = rerank_top_k
        # 缓存命中统计
        self._cache_hits = 0
        self._cache_misses = 0

    def query(
        self,
        query: str,
        top_k: int = 5,
        *,
        where: dict[str, Any],
        namespace: str,
        skip_cache: bool = False,
        skip_rerank: bool = False,
    ) -> list[RetrievalResult]:
        """知识库检索：缓存 → 混合检索 → 重排。

        参数：
            top_k: 最终返回结果数
            where: 元数据过滤条件（如 {"user_id": "u1"}），多用户隔离的强制入口
            namespace: 缓存隔离命名空间（通常为 user_id），同 query 不同用户不串缓存
            skip_cache: 跳过缓存读写（用于检索测试）
            skip_rerank: 跳过重排（用于对比评测）
        """
        start = time.perf_counter()

        # 1. 查缓存（按 namespace 隔离）
        if not skip_cache and self._cache is not None:
            cached = self._cache.get(namespace, query, top_k=top_k)
            if cached is not None:
                self._cache_hits += 1
                logger.info(
                    "kb_query_cache_hit", namespace=namespace, query_len=len(query), top_k=top_k
                )
                return cached
            self._cache_misses += 1

        # 2. 混合检索（召回更多候选，供重排筛选；按 where 过滤）
        recall_k = top_k * 3 if self._reranker and not skip_rerank else top_k
        results = self._retriever.search(query, top_k=recall_k, where=where)

        # 3. 重排精排
        if self._reranker and not skip_rerank and results:
            rerank_k = self._rerank_top_k or top_k
            results = self._reranker.rerank(query, results, top_k=rerank_k)
        else:
            results = results[:top_k]

        # 4. 写缓存（按 namespace 隔离）
        if not skip_cache and self._cache is not None:
            self._cache.set(namespace, query, results, top_k=top_k)

        elapsed = time.perf_counter() - start
        logger.info(
            "kb_query_done",
            namespace=namespace,
            query_len=len(query),
            top_k=top_k,
            results=len(results),
            elapsed_ms=round(elapsed * 1000, 2),
        )
        return results

    @property
    def cache_hit_rate(self) -> float:
        """缓存命中率。"""
        total = self._cache_hits + self._cache_misses
        if total == 0:
            return 0.0
        return self._cache_hits / total

    def invalidate_cache(self, namespace: str | None = None) -> None:
        """清空检索缓存。

        传入 namespace 时只失效该用户（文档更新后调用）；
        不传时清空全部用户（重建索引等全局场景）。
        """
        if self._cache is not None:
            self._cache.clear(namespace)
            logger.info("kb_cache_invalidated", namespace=namespace)
