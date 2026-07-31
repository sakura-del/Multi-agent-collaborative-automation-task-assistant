"""EXECUTION_LOG / VALIDATION_RESULT / FAILURE_CASE 表：执行追踪与质量保障。

- EXECUTION_LOG：Agent 执行子任务的全程日志
- VALIDATION_RESULT：校验Agent对子任务输出的校验报告
- FAILURE_CASE：失败场景自愈系统归档的失败案例
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ExecutionLog(Base):
    """Agent 执行日志表：全链路追踪。"""

    __tablename__ = "execution_log"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, comment="日志ID")
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
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
        comment="执行Agent ID",
    )

    # 日志级别：debug / info / warning / error
    level: Mapped[str] = mapped_column(
        String(8), default="info", index=True, nullable=False, comment="日志级别"
    )
    # 日志消息
    message: Mapped[str] = mapped_column(Text, nullable=False, comment="日志内容")
    # 结构化上下文（trace_id / 输入参数 / 中间结果等）
    context: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True, comment="结构化上下文"
    )

    # 耗时（毫秒）
    duration_ms: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="本次操作耗时(毫秒)"
    )

    logged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, nullable=False, comment="日志产生时间"
    )


class ValidationResult(Base):
    """校验结果表：校验Agent产出。"""

    __tablename__ = "validation_result"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, comment="校验结果ID"
    )
    subtask_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("subtask.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="被校验的子任务ID",
    )

    # 校验是否通过
    passed: Mapped[bool] = mapped_column(nullable=False, index=True, comment="是否通过校验")
    # 质量评分 0.0-1.0
    score: Mapped[float] = mapped_column(Float, nullable=False, comment="质量评分0-1")

    # 规则校验明细：[{rule:格式, passed:true, message:""}, ...]
    rule_results: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=list, nullable=False, comment="规则校验明细"
    )
    # LLM 自检报告：逻辑一致性 / 事实准确性
    llm_report: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True, comment="LLM自检报告"
    )
    # 完整校验报告文本
    report: Mapped[str | None] = mapped_column(Text, nullable=True, comment="校验报告全文")

    validated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="校验时间"
    )


class FailureCase(Base):
    """失败案例表：自愈系统归档，供复盘与策略优化。"""

    __tablename__ = "failure_case"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, comment="失败案例ID"
    )
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

    # 错误分类：tool_call / llm_output / file_io / system_resource
    error_category: Mapped[str] = mapped_column(
        String(16), index=True, nullable=False, comment="错误分类"
    )
    # 错误明细
    error_message: Mapped[str] = mapped_column(Text, nullable=False, comment="错误信息")
    # 堆栈/上下文
    stack_trace: Mapped[str | None] = mapped_column(Text, nullable=True, comment="堆栈追踪")

    # 自愈策略：switch_tool / adjust_params / fix_path / delay_retry
    remedy_strategy: Mapped[str | None] = mapped_column(
        String(16), nullable=True, comment="采用的修复策略"
    )
    # 自愈是否成功
    remedy_succeeded: Mapped[bool | None] = mapped_column(nullable=True, comment="修复是否成功")
    # 是否触发熔断（连续3次同类错误）
    triggered_circuit_breaker: Mapped[bool] = mapped_column(
        nullable=False, default=False, comment="是否触发熔断"
    )

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, nullable=False, comment="失败发生时间"
    )
