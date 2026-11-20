"""审计日志模型（Issue #25 沉淀项 3）。

## 字段设计要点

- `actor_name` 冗余用户名的**快照**：用户注销后审计仍可读（不外键级联删除）；
- `actor_id` 外键 `ON DELETE SET NULL`：不因删用户丢掉审计行；
- `metadata_json` 只放操作摘要（改了哪些字段、套餐 id），**绝不放 key/密码**；
- 不设 updated_at 语义：审计行写入即终态，只增不改。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base

_JSON_TYPE = JSON().with_variant(JSONB(), "postgresql")


class AuditLog(Base):
    """关键操作审计行（只增不改）。"""

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_action_created", "action", "created_at"),
        Index("ix_audit_log_actor_created", "actor_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
        comment="操作者（NULL=系统动作）",
    )
    actor_name: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="操作者用户名快照（删号后仍可读）"
    )
    action: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="动作键，如 credential.create"
    )
    target_type: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="对象类型：credential/subscription/user…"
    )
    target_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="对象 ID（字符串，兼容非 UUID 主键）"
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        _JSON_TYPE, nullable=False, default=dict, comment="操作摘要（不含敏感值）"
    )
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="客户端 IP")
