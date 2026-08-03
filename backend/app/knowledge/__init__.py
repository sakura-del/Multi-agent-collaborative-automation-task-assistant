"""RAG 知识库层：多格式文档解析、智能分块、混合检索、重排、缓存。

阶段五交付：
- 文档解析器（PDF/Word/Markdown/TXT）
- 智能分块（递归字符/语义/滑动窗口）
- 向量嵌入服务（BGE-large-zh + Mock 双模式）
- Chroma 向量数据库
- BM25 倒排索引（jieba 中文分词）
- 混合检索引擎（向量+BM25 RRF 融合）
- BGE-Reranker 重排
- 增量索引更新
- Redis 检索缓存
- 知识库管理 API
"""

from app.knowledge.models.document import (
    ChunkStrategy,
    DocumentType,
    IndexStats,
    ParsedDocument,
    RetrievalResult,
    TextChunk,
)
from app.knowledge.services.factory import (
    get_index_manager,
    get_kb,
    init_kb,
    reset_kb,
)
from app.knowledge.services.knowledge_base import KnowledgeBase

__all__ = [
    "ChunkStrategy",
    "DocumentType",
    "IndexStats",
    "KnowledgeBase",
    "ParsedDocument",
    "RetrievalResult",
    "TextChunk",
    "get_index_manager",
    "get_kb",
    "init_kb",
    "reset_kb",
]
