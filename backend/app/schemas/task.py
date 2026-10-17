"""任务 HTTP 请求/响应 Schema（Plan 2 Task 5）。

与编排层契约（`app/orchestration/contracts.py`）解耦：
- 请求模型负责 HTTP 层严格校验（extra="forbid"），再映射为 transport-independent 契约；
- 响应模型直接复用契约模型（TaskSnapshot / TaskEvent），避免双份定义；
- 允许的未知字段一律拒绝，防止前端拼写错误被静默忽略。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.orchestration.contracts import (
    LiteratureReviewRequest,
    SubtaskSnapshot,
    TaskCommand,
    TaskControlRequest,
    TaskEvent,
    TaskSnapshot,
)

__all__ = [
    "PlanApproval",
    "SubtaskSnapshot",
    "TaskControl",
    "TaskCreate",
    "TaskCreateOut",
    "TaskEventPage",
    "TaskSnapshot",
    "TaskSummaryOut",
]


class TaskCreate(BaseModel):
    """创建文献综述任务（映射为 LiteratureReviewRequest）。"""

    model_config = ConfigDict(extra="forbid")

    topic: str = Field(min_length=5, max_length=200)
    keywords: list[str] = Field(min_length=1, max_length=10)
    date_from: date | None = None
    date_to: date | None = None
    max_documents: int = Field(default=20, ge=1, le=50)
    output_language: Literal["zh", "en"] = "zh"
    citation_style: Literal["gbt7714", "apa"] = "gbt7714"
    document_ids: list[UUID] = Field(default_factory=list, max_length=50)
    offline_dataset: bool = False

    def to_contract(self) -> LiteratureReviewRequest:
        """映射为编排层契约（HTTP 层不向编排层传递自身模型）。"""
        return LiteratureReviewRequest(**self.model_dump())


class TaskControl(BaseModel):
    """人工控制命令（映射为 TaskControlRequest）。"""

    model_config = ConfigDict(extra="forbid")

    command: TaskCommand
    node_id: str | None = None
    subtask_id: str | None = None
    input: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _validate_command_payload(self) -> TaskControl:
        if self.command is TaskCommand.RETRY_NODE and not (self.node_id or self.subtask_id):
            raise ValueError("retry_node 需要 node_id 或 subtask_id")
        if self.command is TaskCommand.SUPPLY_INPUT and not self.input:
            raise ValueError("supply_input 需要非空 input")
        return self

    def to_contract(self) -> TaskControlRequest:
        """映射为编排层契约；node_id 与 subtask_id 二选一。"""
        return TaskControlRequest(
            command=self.command,
            node_id=self.node_id or self.subtask_id,
            input=self.input,
        )


class PlanApproval(BaseModel):
    """规划确认请求：必须携带当前规划版本。"""

    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=1)


class TaskCreateOut(BaseModel):
    """创建任务响应。"""

    model_config = ConfigDict(extra="forbid")

    task_id: UUID
    status: str


class TaskSummaryOut(BaseModel):
    """任务列表条目。"""

    model_config = ConfigDict(extra="forbid")

    task_id: UUID
    status: str
    title: str
    plan_version: int = 0
    revision: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class TaskEventPage(BaseModel):
    """任务事件回放响应。"""

    model_config = ConfigDict(extra="forbid")

    events: list[TaskEvent]
    next_event_id: int
