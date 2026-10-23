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
    # 列表页展示所需的最小元数据（不泄漏 file_path 等内部细节）
    source: str = ""
    original_name: str | None = None
    failure_reason: str | None = None


class KnowledgeDocumentList(BaseModel):
    """文档列表响应体。"""

    documents: list[KnowledgeDocumentOut]


class KnowledgeStatsOut(BaseModel):
    """索引统计响应体。

    ## 两个 ready 标志的真实含义（2026-10-23 修复）

    原先 `vector_index_ready` / `bm25_index_ready` 在端点里被**硬编码为 True**，
    无论真实索引状况如何都报「就绪」。本会话已踩过同类的坑（SQLite 说 28344
    条向量、Chroma 实际只有 4 条），所以这两个标志必须来自可验证的事实：

    - `vector_index_ready`：该用户的向量索引行数 **等于** 分块数且大于 0；
    - `bm25_index_ready`：进程内 BM25 索引**确实**已就绪（无法确认时为 False，
      因为「确认不了」不该被显示成「就绪」）。
    """

    total_docs: int
    total_chunks: int
    vector_index_ready: bool
    bm25_index_ready: bool
    last_update: str | None = None
    #: 索引失败的文档数（需要重新上传）。
    index_failed_count: int = 0
    #: 正在索引的文档数（只需等待，无需操作）。
    indexing_count: int = 0


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
