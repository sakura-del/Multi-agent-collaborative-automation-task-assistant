"""重排模型：对混合检索 Top-K 结果进行精排，提升检索精度。

双模式设计（同嵌入服务）：
- 真实模式：基于 FlagEmbedding 加载 BGE-Reranker-large 交叉编码器
- Mock 模式：基于关键词重叠度评分，供 CI 与单元测试使用

重排原理：
交叉编码器将 (query, document) 拼接输入模型，直接输出相关性得分，
比双塔模型（向量检索）的余弦相似度更精确，但计算成本更高。
因此仅对 Top-K（通常 10-20 条）结果重排，平衡精度与延迟。

设计要点：
- 延迟加载：模型在首次 rerank 调用时才加载
- 批量推理：一次传入多对 (query, doc) 减少调用开销
- 得分归一化：重排后得分归一化到 [0, 1] 便于下游阈值过滤
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.knowledge.models.document import RetrievalResult

logger = get_logger(__name__)


class BaseReranker(ABC):
    """重排器抽象基类。"""

    @abstractmethod
    def rerank(
        self,
        query: str,
        results: list[RetrievalResult],
        top_k: int = 5,
    ) -> list[RetrievalResult]:
        """对检索结果重排，返回 top_k 条按相关性降序的结果。"""


class MockReranker(BaseReranker):
    """Mock 重排器：基于关键词重叠度评分。

    供 CI 与单元测试使用，无需下载模型。
    评分逻辑：query 与 doc 的分词重叠比例，归一化到 [0, 1]。
    """

    def rerank(
        self,
        query: str,
        results: list[RetrievalResult],
        top_k: int = 5,
    ) -> list[RetrievalResult]:
        from app.knowledge.services.bm25_index import tokenize

        query_tokens = set(tokenize(query))
        if not query_tokens or not results:
            return results[:top_k]

        scored: list[tuple[RetrievalResult, float]] = []
        for result in results:
            doc_tokens = set(tokenize(result.text))
            if not doc_tokens:
                scored.append((result, 0.0))
                continue
            # Jaccard 相似度：交集 / 并集
            overlap = len(query_tokens & doc_tokens)
            union = len(query_tokens | doc_tokens)
            score = overlap / union if union > 0 else 0.0
            scored.append((result, score))

        # 按重排得分降序
        scored.sort(key=lambda x: x[1], reverse=True)

        return [
            RetrievalResult(
                chunk_id=r.chunk_id,
                doc_id=r.doc_id,
                text=r.text,
                score=s,
                metadata={**r.metadata, "reranked": True},
                source=r.source,
            )
            for r, s in scored[:top_k]
        ]


class BGEReranker(BaseReranker):
    """BGE-Reranker 重排器：基于 FlagEmbedding 交叉编码器。

    模型在首次 rerank 调用时延迟加载。
    """

    def __init__(self, model_name: str | None = None) -> None:
        rag = get_settings().rag
        self._model_name = model_name or rag.reranker_model
        self._model: Any = None

    def _ensure_model(self) -> None:
        """延迟加载模型。"""
        if self._model is None:
            from FlagEmbedding import FlagReranker

            logger.info("loading_reranker_model", model=self._model_name)
            self._model = FlagReranker(self._model_name, use_fp16=True)

    def rerank(
        self,
        query: str,
        results: list[RetrievalResult],
        top_k: int = 5,
    ) -> list[RetrievalResult]:
        if not results:
            return []

        self._ensure_model()

        # 构造 (query, doc) 对
        pairs = [[query, r.text] for r in results]
        # 批量推理，返回相关性得分
        scores = self._model.compute_score(pairs, normalize=True)

        # 单条结果时 compute_score 返回标量，统一为列表
        if isinstance(scores, int | float):
            scores = [float(scores)]
        else:
            scores = [float(s) for s in scores]

        # 按得分降序
        scored = list(zip(results, scores, strict=False))
        scored.sort(key=lambda x: x[1], reverse=True)

        return [
            RetrievalResult(
                chunk_id=r.chunk_id,
                doc_id=r.doc_id,
                text=r.text,
                score=s,
                metadata={**r.metadata, "reranked": True},
                source=r.source,
            )
            for r, s in scored[:top_k]
        ]


def create_reranker(force_mock: bool | None = None) -> BaseReranker:
    """工厂函数：按环境自动选择真实或 Mock 重排器。"""
    if force_mock is None:
        force_mock = os.getenv("RAG_RERANKER_MOCK", "0") == "1"

    if force_mock:
        logger.info("using_mock_reranker")
        return MockReranker()

    try:
        return BGEReranker()
    except Exception as exc:
        logger.warning("reranker_model_unavailable_fallback_mock", error=str(exc))
        return MockReranker()
