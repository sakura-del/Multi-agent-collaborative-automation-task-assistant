"""AGENT / AGENT_INTERACTION 表：Agent 能力画像与协作消息。

- AGENT：四大Agent（规划/执行/校验/复盘）的能力画像
- AGENT_INTERACTION：Agent 间消息传递记录（基于 Redis Pub/Sub 异步通信的持久化）
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Agent(Base):
    """Agent 能力画像表。"""

    __tablename__ = "agent"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, comment="Agent ID")

    # Agent 类型：planner / executor / validator / reviewer
    agent_type: Mapped[str] = mapped_column(
        String(16), unique=True, index=True, nullable=False, comment="Agent类型"
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="Agent显示名")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="Agent职责描述")

    # 能力画像：支持的task_type列表、最大并发数、平均处理时长等
    capabilities: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
        comment="能力标签：task_type/max_concurrency/avg_duration",
    )

    # 当前状态：idle / running / waiting / error
    status: Mapped[str] = mapped_column(
        String(16), default="idle", index=True, nullable=False, comment="Agent状态"
    )
    # 当前承担的子任务数（调度器负载均衡参考）
    current_load: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, comment="当前并发负载"
    )

    last_active_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="最后活跃时间"
    )


class AgentInteraction(Base):
    """Agent 间消息交互记录表。"""

    __tablename__ = "agent_interaction"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, comment="交互记录ID"
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("task.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="关联任务ID",
    )
    # 消息来源/目标 Agent（用 agent_type 字符串而非外键，避免历史消息引用被删Agent）
    from_agent: Mapped[str] = mapped_column(
        String(16), index=True, nullable=False, comment="发送方Agent类型"
    )
    to_agent: Mapped[str] = mapped_column(
        String(16), index=True, nullable=False, comment="接收方Agent类型"
    )

    # 消息类型：task_assign / result / query / status_sync / error
    message_type: Mapped[str] = mapped_column(String(16), nullable=False, comment="消息类型")
    # 消息内容（JSON 灵活结构）
    content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, comment="消息内容")
    # 元数据：trace_id / 优先级 / 重试标记等
    metadata_: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSON, nullable=True, comment="元数据"
    )

    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="消息发送时间"
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="消息送达时间"
    )
