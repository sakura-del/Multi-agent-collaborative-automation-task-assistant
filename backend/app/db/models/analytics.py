"""推广漏斗埋点与站内用户反馈 ORM 模型（Issue #61 P2-10 & P2-11）。"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base

_JSON_TYPE = JSON().with_variant(JSONB(), "postgresql")


class AnalyticsEvent(Base):
    """轻量产品漏斗事件（仅存白名单事件名与匿名 visitor_id / 可选 user_id）。"""

    __tablename__ = "analytics_event"
    __table_args__ = (
        Index("ix_analytics_event_name_created", "event_name", "created_at"),
        Index("ix_analytics_event_visitor_created", "visitor_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    event_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="白名单事件名")
    visitor_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="匿名访客标识")
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="关联用户（未登录为 NULL）",
    )
    path: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="页面路由")
    meta: Mapped[dict[str, Any] | None] = mapped_column(
        _JSON_TYPE, nullable=True, comment="附加元数据（不含敏感文本）"
    )


class UserFeedback(Base):
    """站内用户反馈记录。"""

    __tablename__ = "user_feedback"
    __table_args__ = (Index("ix_user_feedback_created", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="提交用户ID",
    )
    category: Mapped[str] = mapped_column(
        String(32), nullable=False, default="suggestion", comment="bug/suggestion/question/other"
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="反馈正文")
    page_path: Mapped[str | None] = mapped_column(String(255), nullable=True, comment="来源页面")
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="open", comment="open/reviewed/resolved"
    )
