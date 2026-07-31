"""KNOWLEDGE_DOC / DOC_CHUNK / VECTOR_INDEX 表：RAG 知识库元数据。

阶段5 RAG 核心模块的元数据持久化：
- KNOWLEDGE_DOC：上传文档元信息
- DOC_CHUNK：分块后的 chunk 文本与位置
- VECTOR_INDEX：向量索引条目（与 Chroma 中的 embedding 一一对应）

注：实际向量数据存于 Chroma，本表只存"应用层元数据"以便管理。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class KnowledgeDoc(Base):
    """知识文档表：RAG 库中的文档元信息。"""

    __tablename__ = "knowledge_doc"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, comment="文档ID")
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="上传者ID（多用户隔离）",
    )

    title: Mapped[str] = mapped_column(String(256), nullable=False, comment="文档标题")
    # 文档来源：upload / web_crawl / arxiv / scholar / feedback_archive
    source: Mapped[str] = mapped_column(
        String(16), default="upload", nullable=False, comment="文档来源"
    )
    # 文档类型：pdf / word / markdown / txt
    doc_type: Mapped[str] = mapped_column(String(16), nullable=False, comment="文档类型")

    # 原始文件存储路径或URL
    file_path: Mapped[str | None] = mapped_column(
        String(512), nullable=True, comment="文件存储路径"
    )
    # 文档摘要
    summary: Mapped[str | None] = mapped_column(Text, nullable=True, comment="文档摘要")
    # 元数据：作者/URL/标签/创建时间等
    meta_info: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSON, nullable=True, comment="文档元数据"
    )

    # 索引状态：pending / indexing / ready / failed
    index_status: Mapped[str] = mapped_column(
        String(16), default="pending", index=True, nullable=False, comment="索引状态"
    )
    # 分块总数（索引完成后填充）
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="分块数量")

    # 是否对其他用户可见（共享知识库）
    is_shared: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, comment="是否共享"
    )

    # 关系
    chunks: Mapped[list[DocChunk]] = relationship(
        back_populates="doc", cascade="all, delete-orphan"
    )


class DocChunk(Base):
    """文档分块表：分块文本与定位。"""

    __tablename__ = "doc_chunk"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, comment="分块ID")
    doc_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_doc.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="所属文档ID",
    )

    # 块序号（从0开始）
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, comment="块序号")
    # 块文本内容
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="块文本内容")
    # 块字符数
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, comment="字符数")

    # 块在原文中的定位：{page:1, paragraph:3, offset:1024}
    position: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True, comment="原文定位")
    # 块级元数据：标题层级/表格标记等
    chunk_meta: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True, comment="块级元数据"
    )

    # 关系
    doc: Mapped[KnowledgeDoc] = relationship(back_populates="chunks")
    # 1:1 向量索引条目
    vector_index: Mapped[VectorIndex | None] = relationship(
        back_populates="chunk", uselist=False, cascade="all, delete-orphan"
    )


class VectorIndex(Base):
    """向量索引条目表：与 Chroma collection 中的向量一一对应。"""

    __tablename__ = "vector_index"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, comment="索引条目ID"
    )
    chunk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("doc_chunk.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
        comment="关联分块ID（1:1）",
    )
    doc_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_doc.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="关联文档ID（冗余便于按文档批量删除）",
    )

    # Chroma collection 名称（按文档类型/场景分区）
    collection_name: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="Chroma collection名"
    )
    # Chroma 中的向量 ID（与 chunk_id 通常一致，但保留独立字段以适配 Chroma 自定义ID策略）
    chroma_id: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, comment="Chroma中的向量ID"
    )

    # 使用的嵌入模型
    embedding_model: Mapped[str] = mapped_column(String(64), nullable=False, comment="嵌入模型名")
    # 向量维度
    vector_dim: Mapped[int] = mapped_column(Integer, nullable=False, comment="向量维度")

    # 是否同时建立 BM25 倒排索引
    has_bm25_index: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, comment="是否建立BM25索引"
    )

    indexed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="索引建立时间"
    )

    # 关系
    chunk: Mapped[DocChunk] = relationship(back_populates="vector_index")
