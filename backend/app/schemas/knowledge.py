"""知识库 HTTP 请求/响应模型。

与 ORM（KnowledgeDoc/DocChunk）解耦：
- Out 模型用 UUID 与白名单字段，避免泄漏 file_path 等内部细节
- from_attributes=True 允许直接从 ORM 对象构造
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeDocumentOut(BaseModel):
    """文档响应体。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    doc_type: str
    index_status: str
    chunk_count: int
    created_at: datetime


class KnowledgeDocumentList(BaseModel):
    """文档列表响应体。"""

    documents: list[KnowledgeDocumentOut]


class KnowledgeStatsOut(BaseModel):
    """索引统计响应体。"""

    total_docs: int
    total_chunks: int
    vector_index_ready: bool
    bm25_index_ready: bool
    last_update: str | None = None


class KnowledgeSearchIn(BaseModel):
    """检索请求体。"""

    query: str = Field(..., min_length=1, description="查询文本")
    top_k: int = Field(5, ge=1, le=50, description="返回结果数")
    skip_cache: bool = False
    skip_rerank: bool = False


class KnowledgeSearchItem(BaseModel):
    """单条检索结果。"""

    chunk_id: str
    doc_id: str
    text: str
    score: float
    source: str


class KnowledgeSearchOut(BaseModel):
    """检索响应体。"""

    query: str
    total: int
    results: list[KnowledgeSearchItem]
