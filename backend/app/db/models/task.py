"""TASK / SUBTASK / TASK_DAG 表：任务编排核心。

- TASK：用户提交的顶层任务，按 user_id 隔离
- SUBTASK：规划Agent拆解后的子任务，关联TASK与执行Agent
- TASK_DAG：任务的DAG图结构（JSONB），与TASK 1:1
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Task(Base):
    """主任务表：用户提交的顶层任务。"""

    __tablename__ = "task"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, comment="任务ID")
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="提交者ID（多用户隔离）",
    )

    title: Mapped[str] = mapped_column(String(256), nullable=False, comment="任务标题")
    description: Mapped[str] = mapped_column(Text, nullable=False, comment="任务自然语言描述")

    # 任务状态：pending / planning / executing / validating / reviewing
    #          / completed / failed / paused / terminated
    status: Mapped[str] = mapped_column(
        String(16), default="pending", index=True, nullable=False, comment="任务状态"
    )
    # 优先级：0-9，数字越大越紧急
    priority: Mapped[int] = mapped_column(Integer, default=5, nullable=False, comment="优先级 0-9")

    # 任务输出格式要求（如 markdown / pdf / xlsx）
    output_format: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="输出格式")
    # 最终产出物（文件路径或文本）
    final_output: Mapped[str | None] = mapped_column(Text, nullable=True, comment="最终产出")

    # 时间统计
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="开始执行时间"
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="完成时间"
    )

    # 关系：一个任务对应一个 DAG 与多个子任务
    dag: Mapped[TaskDag | None] = relationship(
        back_populates="task", uselist=False, cascade="all, delete-orphan"
    )
    subtasks: Mapped[list[Subtask]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )


class Subtask(Base):
    """子任务表：DAG 节点。"""

    __tablename__ = "subtask"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, comment="子任务ID")
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("task.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="所属任务ID",
    )
    # 执行该子任务的Agent（规划/执行/校验/复盘）
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
        comment="执行Agent ID",
    )

    # 节点编号（DAG 中的唯一标识，如 n1/n2）
    node_id: Mapped[str] = mapped_column(String(32), nullable=False, comment="DAG 节点编号")
    title: Mapped[str] = mapped_column(String(256), nullable=False, comment="子任务标题")
    description: Mapped[str] = mapped_column(Text, nullable=False, comment="子任务描述")

    # 状态：pending / running / validating / completed / failed / retrying
    #       / paused / terminated / waiting_human
    status: Mapped[str] = mapped_column(
        String(16), default="pending", index=True, nullable=False, comment="子任务状态"
    )

    # 重试次数（指数退避 1s/3s/9s，最多3次）
    retry_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, comment="已重试次数"
    )
    max_retry: Mapped[int] = mapped_column(
        Integer, default=3, nullable=False, comment="最大重试次数"
    )

    # 输入/输出（JSON 灵活结构）
    input_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True, comment="子任务输入参数"
    )
    output_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True, comment="子任务执行结果"
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="开始时间"
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="完成时间"
    )

    # 关系
    task: Mapped[Task] = relationship(back_populates="subtasks")


class TaskDag(Base):
    """任务DAG图结构表：与 TASK 1:1。"""

    __tablename__ = "task_dag"

    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("task.id", ondelete="CASCADE"),
        primary_key=True,
        comment="任务ID（主键+外键）",
    )

    # DAG 完整结构：{nodes:[...], edges:[...], topological_order:[...]}
    graph: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, comment="DAG图结构（节点/边/拓扑序）"
    )

    # 关系
    task: Mapped[Task] = relationship(back_populates="dag")
