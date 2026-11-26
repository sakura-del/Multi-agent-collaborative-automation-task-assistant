"""TASK_DRAFT：任务创建草稿表（Issue #68）。

每用户至多一条草稿，支持跨设备恢复与服务端 `revision` 乐观锁防互盖。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import JSON, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TaskDraft(Base):
    """用户个人的任务创建草稿（不进入团队共享）。"""

    __tablename__ = "task_draft"
    __table_args__ = (UniqueConstraint("user_id", name="uq_task_draft_user"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, comment="草稿ID")
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="归属用户ID",
    )
    #: 白名单校验后的表单值（与前端 TaskCreateDraftValues 对齐）
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, comment="草稿表单载荷"
    )
    #: 服务端单调递增版本号（每次更新 +1，防多设备/多标签静默覆盖）
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, comment="乐观锁版本号"
    )

    # Base 已提供 created_at / updated_at
