"""知识库组件工厂：管理单例实例的生命周期。

应用启动时调用 init_kb() 初始化全部组件；
各模块通过 get_kb() / get_index_manager() 获取单例。

使用 Mock 模式时无需下载模型，适合 CI 与开发环境。
"""

from __future__ import annotations

import os
from typing import Any

from app.core.logging import get_logger
from app.knowledge.services.bm25_index import BM25Index
from app.knowledge.services.cache import RetrievalCache
from app.knowledge.services.chunker import DocumentChunker
from app.knowledge.services.document_parser import DocumentParser
from app.knowledge.services.embedding import (
    BaseEmbeddingService,
    create_embedding_service,
)
from app.knowledge.services.hybrid_retriever import HybridRetriever
from app.knowledge.services.index_manager import IndexManager
from app.knowledge.services.knowledge_base import KnowledgeBase
from app.knowledge.services.reranker import BaseReranker, create_reranker
from app.knowledge.services.vector_store import VectorStore

logger = get_logger(__name__)

# 全局单例
_kb: KnowledgeBase | None = None
_index_manager: IndexManager | None = None
_parser: DocumentParser | None = None
_chunker: DocumentChunker | None = None
_vector_store: VectorStore | None = None
_bm25_index: BM25Index | None = None
_embedding: BaseEmbeddingService | None = None
_reranker: BaseReranker | None = None
_cache: RetrievalCache | None = None


def init_kb(
    *,
    redis_client: Any | None = None,
    force_mock_embedding: bool | None = None,
    force_mock_reranker: bool | None = None,
    chroma_persist_dir: str | None = None,
) -> KnowledgeBase:
    """初始化知识库全部组件，返回 KnowledgeBase 单例。

    参数：
        redis_client: Redis 客户端（缓存层），None 时使用内存回退
        force_mock_embedding: 强制使用 Mock 嵌入（测试用）
        force_mock_reranker: 强制使用 Mock 重排（测试用）
        chroma_persist_dir: Chroma 持久化路径，None 时读环境变量
    """
    global _kb, _index_manager, _parser, _chunker, _vector_store
    global _bm25_index, _embedding, _reranker, _cache

    if _kb is not None:
        return _kb

    # 各组件按依赖顺序初始化
    _embedding = create_embedding_service(force_mock=force_mock_embedding)
    _reranker = create_reranker(force_mock=force_mock_reranker)

    # 显式传空字符串表示使用内存模式（测试用）；None 时读环境变量
    if chroma_persist_dir is None:
        persist_dir = os.getenv("RAG_CHROMA_PATH", ".chroma_db")
    else:
        persist_dir = chroma_persist_dir
    _vector_store = VectorStore(
        embedding_service=_embedding,
        persist_directory=persist_dir,
    )
    _bm25_index = BM25Index()
    _parser = DocumentParser()
    _chunker = DocumentChunker()

    _index_manager = IndexManager(
        parser=_parser,
        chunker=_chunker,
        vector_store=_vector_store,
        bm25_index=_bm25_index,
    )

    retriever = HybridRetriever(
        vector_store=_vector_store,
        bm25_index=_bm25_index,
    )

    _cache = RetrievalCache(redis_client=redis_client)

    _kb = KnowledgeBase(
        retriever=retriever,
        reranker=_reranker,
        cache=_cache,
    )

    logger.info(
        "knowledge_base_initialized",
        embedding_type=type(_embedding).__name__,
        reranker_type=type(_reranker).__name__,
        chroma_persist=bool(persist_dir),
    )
    return _kb


def get_kb() -> KnowledgeBase:
    """获取知识库单例。未初始化时自动用 Mock 模式初始化。"""
    if _kb is None:
        init_kb(
            force_mock_embedding=True,
            force_mock_reranker=True,
            chroma_persist_dir="",  # 内存模式
        )
    return _kb


def get_index_manager() -> IndexManager:
    """获取索引管理器单例。"""
    if _index_manager is None:
        get_kb()  # 触发初始化
    assert _index_manager is not None
    return _index_manager


def reset_kb() -> None:
    """重置知识库（测试用：清空单例与索引）。"""
    global _kb, _index_manager, _parser, _chunker, _vector_store
    global _bm25_index, _embedding, _reranker, _cache

    if _vector_store is not None:
        _vector_store.clear()
    if _bm25_index is not None:
        _bm25_index.clear()

    _kb = None
    _index_manager = None
    _parser = None
    _chunker = None
    _vector_store = None
    _bm25_index = None
    _embedding = None
    _reranker = None
    _cache = None
