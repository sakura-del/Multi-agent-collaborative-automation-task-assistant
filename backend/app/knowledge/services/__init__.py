"""知识库服务层：RAG 全流水线组件。

组件关系：
    DocumentParser → DocumentChunker → IndexManager
                                         ├→ VectorStore (向量检索)
                                         └→ BM25Index   (关键词检索)
                          HybridRetriever ← (向量 + BM25 RRF 融合)
                                 ↓
                          Reranker (BGE-Reranker 精排)
                                 ↓
                          RetrievalCache (Redis 缓存)
                                 ↓
                          KnowledgeBase (门面入口)
"""

from app.knowledge.services.bm25_index import BM25Index, tokenize
from app.knowledge.services.cache import RetrievalCache
from app.knowledge.services.chunker import DocumentChunker
from app.knowledge.services.document_parser import DocumentParser
from app.knowledge.services.embedding import (
    BaseEmbeddingService,
    MockEmbeddingService,
    SentenceTransformerEmbeddingService,
    create_embedding_service,
)
from app.knowledge.services.hybrid_retriever import HybridRetriever
from app.knowledge.services.index_manager import IndexManager
from app.knowledge.services.knowledge_base import KnowledgeBase
from app.knowledge.services.reranker import (
    BaseReranker,
    BGEReranker,
    MockReranker,
    create_reranker,
)
from app.knowledge.services.siliconflow_embedding import SiliconFlowEmbeddingService
from app.knowledge.services.vector_backends.base import (
    VectorDimensionMismatchError,
    VectorStoreConfigError,
    VectorStoreError,
    VectorStoreUnavailableError,
)
from app.knowledge.services.vector_store import VectorStore

__all__ = [
    "BGEReranker",
    "BM25Index",
    "BaseEmbeddingService",
    "BaseReranker",
    "DocumentChunker",
    "DocumentParser",
    "HybridRetriever",
    "IndexManager",
    "KnowledgeBase",
    "MockEmbeddingService",
    "MockReranker",
    "RetrievalCache",
    "SentenceTransformerEmbeddingService",
    "SiliconFlowEmbeddingService",
    "VectorDimensionMismatchError",
    "VectorStore",
    "VectorStoreConfigError",
    "VectorStoreError",
    "VectorStoreUnavailableError",
    "create_embedding_service",
    "create_reranker",
    "tokenize",
]
