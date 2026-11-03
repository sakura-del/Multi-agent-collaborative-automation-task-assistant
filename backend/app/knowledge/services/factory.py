"""知识库组件工厂：管理单例实例的生命周期。

应用启动时（lifespan）调用 init_kb() 显式初始化全部组件；
各模块通过 get_kb() / get_index_manager() 获取单例。

生产访问路径不允许隐式回退：未初始化时 get_kb() 抛出明确的初始化错误，
避免端点悄悄创建内存 Mock 库导致数据"看起来在、实际没持久化"。
测试显式调用 init_kb(force_mock_*=True, chroma_persist_dir="")。
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.knowledge.services.bm25_index import BM25Index
from app.knowledge.services.cache import RetrievalCache
from app.knowledge.services.chunker import DocumentChunker
from app.knowledge.services.document_parser import DocumentParser
from app.knowledge.services.document_storage import DocumentStorage
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
_storage: DocumentStorage | None = None


class KnowledgeBaseNotInitializedError(RuntimeError):
    """知识库未初始化：生产访问路径必须显式 init_kb()。"""

    def __init__(self) -> None:
        super().__init__(
            "知识库未初始化：请先在应用生命周期中调用 init_kb()（测试可用 force_mock 参数）"
        )


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
    global _bm25_index, _embedding, _reranker, _cache, _storage

    if _kb is not None:
        return _kb

    # 各组件按依赖顺序初始化
    _embedding = create_embedding_service(force_mock=force_mock_embedding)
    _reranker = create_reranker(force_mock=force_mock_reranker)

    # 服务端模式（compose 提供独立 chroma 服务）：RAG__CHROMA_HOST 非空时走 HTTP。
    # 此前该配置形同虚设——容器内始终用本地嵌入模式，与 compose 的 chroma 服务脱节，
    # 且 chromadb 1.x 的 Rust 绑定在全新容器里会崩（CI smoke 因此失败）。
    #
    # 显式传入 `chroma_persist_dir` 表示调用方要自行控制存储模式（测试传 "" 走内存），
    # 此时**不套用配置里的服务端地址与后端选择**，否则测试会去连不存在的 chroma 主机、
    # 或在配置了 pgvector 的环境里把离线单测拖去连外部数据库。
    from app.core.config import get_settings as _get_settings

    rag_cfg = _get_settings().rag
    chroma_host = ""
    chroma_port = 8000
    if chroma_persist_dir is None:
        chroma_host = (getattr(rag_cfg, "chroma_host", "") or "").strip()
        chroma_port = int(getattr(rag_cfg, "chroma_port", 8000))

    _vector_store = VectorStore(
        embedding_service=_embedding,
        # None = 由 `RAG__VECTOR_BACKEND` 决定后端（生产路径）；
        # 非 None = 强制 Chroma（测试与单机开发路径）。
        persist_directory=chroma_persist_dir,
        chroma_host=chroma_host,
        chroma_port=chroma_port,
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

    # 受控存储：上传根目录与大小上限来自全局配置
    from app.core.config import get_settings

    app_cfg = get_settings().app
    _storage = DocumentStorage(app_cfg.upload_root, max_file_bytes=app_cfg.max_upload_bytes)

    logger.info(
        "knowledge_base_initialized",
        embedding_type=type(_embedding).__name__,
        reranker_type=type(_reranker).__name__,
        vector_backend=_vector_store.backend_name,
        chroma_persist=bool(chroma_persist_dir),
    )
    return _kb


def get_kb() -> KnowledgeBase:
    """获取知识库单例。

    未初始化时抛出明确错误（生产访问路径不隐式回退 Mock）；
    测试应在 fixture 中显式调用 init_kb(force_mock_*=True)。
    """
    if _kb is None:
        raise KnowledgeBaseNotInitializedError()
    return _kb


def get_storage() -> DocumentStorage:
    """获取受控文档存储单例。未初始化时抛出明确错误。"""
    if _storage is None:
        raise KnowledgeBaseNotInitializedError()
    return _storage


def get_index_manager() -> IndexManager:
    """获取索引管理器单例。未初始化时抛出明确错误。"""
    if _index_manager is None:
        raise KnowledgeBaseNotInitializedError()
    return _index_manager


def get_vector_store() -> VectorStore:
    """获取向量存储单例（健康探针用）。

    探针要按当前后端真实探测，必须拿到同一个实例——否则会新建一个连接池，
    既不真实也会在每次探活时泄漏连接。
    """
    if _vector_store is None:
        raise KnowledgeBaseNotInitializedError()
    return _vector_store


def reset_kb() -> None:
    """重置知识库（测试用：清空单例与索引）。"""
    global _kb, _index_manager, _parser, _chunker, _vector_store
    global _bm25_index, _embedding, _reranker, _cache, _storage

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
    _storage = None
