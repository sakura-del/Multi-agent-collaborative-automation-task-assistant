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
    SnowballDirection,
    SubtaskSnapshot,
    TaskCommand,
    TaskControlRequest,
    TaskEvent,
    TaskSnapshot,
    WorkflowPreset,
)

__all__ = [
    "MatrixFillGapOut",
    "MatrixFillGapRequest",
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
    keywords: list[str] = Field(default_factory=list, max_length=10)
    workflow_preset: WorkflowPreset = "literature_review"
    draft_text: str | None = Field(default=None, max_length=30000)
    seed_dois: list[str] = Field(default_factory=list, max_length=5)
    snowball_direction: SnowballDirection = "both"
    date_from: date | None = None
    date_to: date | None = None
    max_documents: int = Field(default=15, ge=1, le=50)
    output_language: Literal["zh", "en"] = "zh"
    # Issue #4：样式在渲染期生效；ID 集合由 `app.tools.csl.styles.StyleRegistry` 管理
    # （预置：gbt7714-2025-numeric（默认，别名 "gbt7714"）/ gbt7714-2015-numeric / apa / ieee / nature）。
    citation_style: str = Field(default="gbt7714", min_length=1, max_length=64)
    document_ids: list[UUID] = Field(default_factory=list, max_length=50)
    offline_dataset: bool = False
    reference_library: bool = False
    """是否用用户自己的引用库作为文献源（2026-10-26 引用库计划 §3.3）。

    `True` 时检索节点从引用库取文献、完全不外呼。引用库为空时任务会失败并给出
    可读原因（不是静默回退到外部检索 —— 那会让用户以为在用自己导入的文献）。
    """
    literature_sources: list[str] | None = Field(default=None, max_length=6)
    """指定本次任务使用的文献检索源（Issue #67）。

    `None` / 空列表 = 平台默认（中文任务且配置了万方 Key 时为「万方 + OpenAlex」，
    否则「arXiv + OpenAlex」）。取值见 `app.tools.literature.source_registry.SOURCE_ALIASES`
    （如 "wanfang" / "openalex" / "pubmed" / "semantic_scholar"）。未知源名不会让任务
    直接失败：多源构建失败时回退单 arXiv 源（可跑优先）。
    """
    verify_references: bool | None = None
    """是否做外部引用核验（Issue #3）：None=沿用全局配置；True=强制核验（含离线任务）；False=跳过。"""

    llm_credential_id: UUID | None = Field(
        default=None,
        description="用户自带的模型凭据ID（Issue #15 BYOK）；不传=使用平台模型",
    )
    """只影响本任务用哪把 key，不进入编排层合约（`to_contract` 会剔除）。"""

    is_demo: bool = Field(
        default=False,
        description="是否为新用户一键示例任务（仅首次使用且离线演示语料免扣免费额度）",
    )

    # ===== 实验画像开关（2026-10-22 实验矩阵）=====
    # 默认全开 = 与改造前行为一致。`to_contract()` 用 `model_dump()` 全量透传，
    # 所以这里加同名字段即可，无需改映射代码。
    retrieval_mode: Literal["none", "vector", "hybrid_rerank"] = "hybrid_rerank"
    """检索模式：none=不检索 / vector=向量 / hybrid_rerank=混合+重排（默认）。"""

    validation_enabled: bool = True
    """是否执行 validate 节点（关闭后仍规划、只跳过执行）。"""

    review_enabled: bool = True
    """是否执行 review 节点（关闭后用 write 输出作最终正文）。"""

    @model_validator(mode="after")
    def _validate_preset_inputs(self) -> TaskCreate:
        if self.is_demo:
            if not self.offline_dataset:
                raise ValueError("示例演示任务必须使用离线演示语料（offline_dataset=true）")
            if self.max_documents > 10:
                raise ValueError("示例演示任务文献数量不得超过 10 篇")
            if self.verify_references is True:
                raise ValueError("示例演示任务不开启外部引用核验")
        if self.workflow_preset == "verify_citations":
            has_draft = bool((self.draft_text or "").strip())
            if not has_draft and not self.document_ids:
                raise ValueError("验证引用模式需提供待核验草稿（draft_text）或至少一篇知识库文档")
            if not self.keywords:
                self.keywords = ["引用核验"]
        elif self.workflow_preset == "citation_snowballing":
            self.seed_dois = [item.strip() for item in self.seed_dois if item.strip()][:5]
            if not self.keywords and self.seed_dois:
                self.keywords = list(self.seed_dois[:5])
            if len(self.keywords) < 1:
                raise ValueError("keywords 或 seed_dois 至少需要提供 1 项")
        elif len(self.keywords) < 1:
            raise ValueError("keywords 至少需要 1 个关键词")
        return self

    def to_contract(self) -> LiteratureReviewRequest:
        """映射为编排层契约（HTTP 层不向编排层传递自身模型）。"""
        data = self.model_dump()
        # BYOK 凭据与示例免扣标是「任务归属/计费」层面的信息，编排层合约不认它们；
        # 留着会触发 `extra="forbid"`，或诱使业务层误以为模型参数在请求体里。
        data.pop("llm_credential_id", None)
        data.pop("is_demo", None)
        return LiteratureReviewRequest(**data)


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


class TaskCountsOut(BaseModel):
    """列表页分组计数（服务端全量，不是当前页统计）。

    `all` 不等于三者之和：失败/取消计入 all，但不属于任何操作分组。
    """

    model_config = ConfigDict(extra="forbid")

    all: int
    needs_me: int
    active: int
    done: int


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


class MatrixFillGapRequest(BaseModel):
    """研究空白矩阵一键补检索请求（Issue #32 Phase 3）。"""

    model_config = ConfigDict(extra="forbid")

    question_index: int = Field(ge=0)
    query: str | None = Field(default=None, max_length=300)


class MatrixFillGapOut(BaseModel):
    """研究空白矩阵补检索结果。"""

    model_config = ConfigDict(extra="forbid")

    task_id: UUID
    question_index: int
    query: str
    added_count: int
    duplicate_count: int
    summary_markdown: str
    evidence_matrix: dict[str, Any] | None = None
    bibliography: list[dict[str, Any]] = Field(default_factory=list)
