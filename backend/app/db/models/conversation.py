"""CONVERSATION / MESSAGE：会话式 Agent 界面内核（Issue #8 P0）。

## 为什么消息是**树**而不是列表

「从任意节点继续对话」「编辑重发」「分支对比」都要求同一父节点下有多条子消息。
扁平列表 + `parent_id` 是唯一能同时表达这三件事的最小结构：

```
u1 ── a1 ── u2 ── a2
        └── u2' ── a2'     ← 编辑/分支（与 u2 并列）
```

前端按 `parent_id` 建树，`leaf_id` 指向当前查看的分支末端（存在会话上，
`NULL` = 走最新叶子）。删除父消息时子消息级联删除（不做孤儿）。

## 内容存储

P0 用 `content` 纯文本（Markdown 原文）。工具调用块、附件引用等结构化内容
到 P1 再升级为 `blocks` JSONB——先不做，避免为一个推测的需求复杂化表结构。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Conversation(Base):
    """一个多轮会话（归属用户；标题可自动生成）。"""

    __tablename__ = "conversation"
    __table_args__ = (Index("ix_conversation_user_updated", "user_id", "updated_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, comment="会话ID")
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="归属用户ID",
    )
    title: Mapped[str] = mapped_column(
        String(120), nullable=False, default="新会话", comment="标题（首条用户消息截断）"
    )
    archived: Mapped[bool] = mapped_column(
        nullable=False, default=False, comment="归档（不删除，列表默认隐藏）"
    )
    message_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="消息数（含分支）"
    )
    # ⚠️ 必须显式 DateTime(timezone=True)：`Mapped[datetime | None]` 的默认推导是
    # 无时区 TIMESTAMP，写入 aware datetime 时 asyncpg 直接报
    # 「can't subtract offset-naive and offset-aware datetimes」（生产实测 2026-11-19）。
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="最后一条消息时间（列表排序用）"
    )
    #: 当前查看的分支末端；NULL = 取最新叶子（前端切换分支时回写）
    leaf_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("message.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
        comment="当前分支末端消息ID",
    )

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
        foreign_keys="Message.conversation_id",
    )

    # Base 已提供 created_at / updated_at


class Message(Base):
    """会话内一条消息（树节点）。"""

    __tablename__ = "message"
    __table_args__ = (
        Index("ix_message_conversation_created", "conversation_id", "created_at"),
        Index("ix_message_parent", "parent_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, comment="消息ID")
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversation.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属会话ID",
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("message.id", ondelete="CASCADE"),
        nullable=True,
        comment="父消息ID（树结构；NULL = 会话根）",
    )
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="角色：user / assistant / system"
    )
    content: Mapped[str] = mapped_column(
        Text, nullable=False, default="", comment="Markdown 原文（流式期间逐步追加）"
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="completed",
        comment="completed / pending / streaming / interrupted / failed",
    )
    model: Mapped[str | None] = mapped_column(String(160), nullable=True, comment="生成模型")
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="失败原因")

    conversation: Mapped[Conversation] = relationship(
        back_populates="messages", foreign_keys=[conversation_id]
    )

    # Base 已提供 created_at / updated_at
