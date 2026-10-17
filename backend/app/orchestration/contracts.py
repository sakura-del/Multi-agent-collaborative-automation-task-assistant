"""编排运行时共享契约（Plan 2 Task 1）。

这些模型是 HTTP 层、编排层与持久化层之间的唯一定义：
- 任务状态与命令枚举
- 结构化规划（TaskPlan / PlanNode）
- 节点执行结果、校验报告、复盘报告
- 证据引用与任务事件

约束：所有模型 `extra="forbid"`，避免上游静默丢弃未知字段。
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class TaskStatus(StrEnum):
    """任务生命周期状态，对齐 TASK.status。"""

    CREATED = "created"
    PLANNING = "planning"
    WAITING_APPROVAL = "waiting_approval"
    RUNNING = "running"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    PAUSED = "paused"
    RETRYING = "retrying"
    WAITING_HUMAN = "waiting_human"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskCommand(StrEnum):
    """人工控制命令。"""

    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"
    RETRY_NODE = "retry_node"
    SUPPLY_INPUT = "supply_input"


class Evidence(BaseModel):
    """一条可回溯证据：绑定文档/分块/来源 URL 与原文片段。"""

    model_config = ConfigDict(extra="forbid")

    doc_id: UUID | None = None
    chunk_id: UUID | None = None
    source_url: HttpUrl | None = None
    excerpt: str = Field(min_length=1, max_length=2000)


class EvidenceContext(BaseModel):
    """owner-scoped 证据上下文：稳定标记文本 + 结构化证据列表（Plan 3 Task 2）。"""

    model_config = ConfigDict(extra="forbid")

    text: str
    evidence: list[Evidence]
    insufficient: bool
    query: str


class PlanNode(BaseModel):
    """规划出的 DAG 节点。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    type: Literal["search", "extract", "write", "validate", "review"]
    title: str = Field(min_length=1, max_length=256)
    dependencies: list[str] = Field(default_factory=list)
    input: dict[str, Any] = Field(default_factory=dict)
    required_evidence: int = Field(default=1, ge=0, le=50)


class TaskPlan(BaseModel):
    """结构化任务规划：用户确认后按此生成 DAG。"""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    topic: str = Field(min_length=5, max_length=200)
    research_questions: list[str] = Field(min_length=1, max_length=10)
    search_queries: list[str] = Field(min_length=1, max_length=20)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=20)
    nodes: list[PlanNode] = Field(min_length=1, max_length=50)


class ExecutionResult(BaseModel):
    """单个 DAG 节点的执行结果。"""

    model_config = ConfigDict(extra="forbid")

    node_id: str
    content: str
    evidence: list[Evidence] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    duration_ms: int = Field(ge=0)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)


class ValidationCheck(BaseModel):
    """单条校验项。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    passed: bool
    message: str


class ValidationReport(BaseModel):
    """校验 Agent 输出：是否通过、评分、重试建议。"""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    score: float = Field(ge=0, le=100)
    checks: list[ValidationCheck]
    retryable: bool
    feedback: str


class ReviewReport(BaseModel):
    """复盘 Agent 输出：最终报告、参考文献、评分与统计。"""

    model_config = ConfigDict(extra="forbid")

    final_markdown: str
    bibliography: list[dict[str, Any]]
    scores: dict[str, float]
    issues: list[str]
    stats: dict[str, Any]


class TaskEvent(BaseModel):
    """对客户端可见的任务事件（按 event_id 递增回放）。"""

    model_config = ConfigDict(extra="forbid")

    event_id: int
    task_id: UUID
    subtask_id: UUID | None = None
    event_type: str
    status: str
    occurred_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


# ===== 任务调用契约（HTTP 层映射，编排层不依赖 app.schemas）=====


class LiteratureReviewRequest(BaseModel):
    """文献综述任务提交请求（HTTP TaskCreate 映射到此）。"""

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


class TaskControlRequest(BaseModel):
    """人工控制命令（HTTP TaskControl 映射到此）。"""

    model_config = ConfigDict(extra="forbid")

    command: TaskCommand
    node_id: str | None = None
    input: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _validate_command_payload(self) -> TaskControlRequest:
        if self.command is TaskCommand.RETRY_NODE and not self.node_id:
            raise ValueError("retry_node 需要 node_id")
        if self.command is TaskCommand.SUPPLY_INPUT and not self.input:
            raise ValueError("supply_input 需要非空 input")
        return self


class SubtaskSnapshot(BaseModel):
    """任务快照中的子任务条目。"""

    model_config = ConfigDict(extra="forbid")

    node_id: str
    title: str
    status: str
    retry_count: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    output: dict[str, Any] | None = None
    error: str | None = None


class TaskSnapshot(BaseModel):
    """任务状态快照：供查询、断线重连与前端轮询使用。"""

    model_config = ConfigDict(extra="forbid")

    task_id: UUID
    status: str
    title: str
    plan_version: int = 0
    revision: int = 0
    plan: dict[str, Any] | None = None
    subtasks: list[SubtaskSnapshot] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    review_report: dict[str, Any] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
