"""HUMAN_FEEDBACK 表：人工反馈介入机制。

阶段8任务2：弹窗反馈 / 文本修正 / 强制重跑 + 反馈存入RAG知识库。
本表为反馈的持久化存储，与 RAG 知识库联动。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class HumanFeedback(Base):
    """人工反馈记录表。"""

    __tablename__ = "human_feedback"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, comment="反馈ID")
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("task.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="关联任务ID",
    )
    subtask_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("subtask.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
        comment="关联子任务ID",
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="反馈用户ID",
    )

    # 反馈触发节点：planning_done / execution_done / validation_failed / manual
    trigger_point: Mapped[str] = mapped_column(String(16), nullable=False, comment="反馈触发节点")

    # 1-5 星评分
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="评分1-5")
    # 文本批注
    comment: Mapped[str | None] = mapped_column(Text, nullable=True, comment="文本批注")
    # 用户修改后的内容（版本化记录）
    modified_content: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="用户修正后的内容"
    )
    # 操作类型：rate / edit / force_rerun / skip / terminate
    action: Mapped[str] = mapped_column(String(16), nullable=False, comment="反馈操作类型")

    # 附加元数据（如 force_rerun 时的新参数）
    metadata_: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSON, nullable=True, comment="附加元数据"
    )

    # 是否已同步至 RAG 知识库（供后续任务检索）
    synced_to_rag: Mapped[bool] = mapped_column(
        nullable=False, default=False, comment="是否已同步至RAG"
    )

    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="反馈提交时间"
    )
