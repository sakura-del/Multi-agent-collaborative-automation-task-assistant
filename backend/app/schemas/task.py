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
    "WebSocketTicketOut",
    "WebSocketTicketRequest",
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
    # Issue #4：样式在渲染期生效；ID 集合由 `app.tools.csl.styles.StyleRegistry` 管理
    # （预置：gbt7714-2025-numeric（默认，别名 "gbt7714"）/ gbt7714-2015-numeric / apa / ieee / nature）。
    citation_style: str = Field(default="gbt7714", min_length=1, max_length=64)
    document_ids: list[UUID] = Field(default_factory=list, max_length=50)
    offline_dataset: bool = False
    verify_references: bool | None = None
    """是否做外部引用核验（Issue #3）：None=沿用全局配置；True=强制核验（含离线任务）；False=跳过。"""

    # ===== 实验画像开关（2026-10-22 实验矩阵）=====
    # 默认全开 = 与改造前行为一致。`to_contract()` 用 `model_dump()` 全量透传，
    # 所以这里加同名字段即可，无需改映射代码。
    retrieval_mode: Literal["none", "vector", "hybrid_rerank"] = "hybrid_rerank"
    """检索模式：none=不检索 / vector=向量 / hybrid_rerank=混合+重排（默认）。"""

    validation_enabled: bool = True
    """是否执行 validate 节点（关闭后仍规划、只跳过执行）。"""

    review_enabled: bool = True
    """是否执行 review 节点（关闭后用 write 输出作最终正文）。"""

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


class WebSocketTicketRequest(BaseModel):
    """申请 WebSocket 连接票据（要求任务归属当前用户）。"""

    model_config = ConfigDict(extra="forbid")

    task_id: UUID


class WebSocketTicketOut(BaseModel):
    """一次性连接票据；expires_in 为剩余有效秒数。"""

    model_config = ConfigDict(extra="forbid")

    ticket: str
    expires_in: int
