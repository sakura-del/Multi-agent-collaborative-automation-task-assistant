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
    node_type: str = ""
    """节点类型（search/extract/write/validate/review），Issue #7 起由工作流填写。

    复盘用它区分**作者正文**（extract/write）与**机械汇总**（search/validate/review）：
    后者内容是其他节点输出的拼接，若并入正文会造成大面积重复。
    历史持久化负载没有该键，缺省空串表示「无法判定」，复盘保持既有行为。
    """

    content_role: str = ""
    """内容角色（authored/aggregated，Issue #7）：显式标注时优先于 `node_type` 推断。

    缺省空串表示未标注，由复盘按 `node_type` 判定。
    """


class ValidationCheck(BaseModel):
    """单条校验项。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    passed: bool
    message: str
    advisory: bool = False
    """true 表示「仅供参考」：结论写入报告供审计，但不参与 passed / score 判定。

    Issue #3 引用真实性核验默认走 advisory（外部源不可用时避免误伤任务），
    历史持久化负载没有该键，缺省 false 保持既有语义。
    """


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
    # Issue #4：引用样式在**渲染期**生效（不再只是提示词）。取值由
    # `app.tools.csl.styles.StyleRegistry` 提供（含 "gbt7714" 别名 → "gbt7714-2025-numeric"）；
    # 这里放宽为字符串以便新增样式不必改契约，未知样式在渲染时给出明确错误。
    citation_style: str = Field(default="gbt7714", min_length=1, max_length=64)
    document_ids: list[UUID] = Field(default_factory=list, max_length=50)
    offline_dataset: bool = False
    reference_library: bool = False
    """是否用**用户自己的引用库**作为文献源（2026-10-26 引用库计划 §3.3）。

    ``True`` 时检索节点从 `reference_library_item` 取文献，**完全不外呼** ——
    天然可复现，且引用的都是用户自己挑过的文献。

    与 ``offline_dataset`` 的区别：离线清单是**产品内置的演示语料**（固定 10 篇），
    引用库是**用户导入的**（BibTeX/RIS）。两者同时为真时以引用库为准
    （用户显式选择优先），并在日志里记 warning。
    """
    verify_references: bool | None = None
    """本任务是否做外部引用核验（Issue #3）。

    - ``None``（默认）：沿用全局配置——离线清单任务零外呼（R6），在线任务核验；
    - ``True``：本任务强制核验，**即使离线**（核验结论有 7 天缓存兜底），
      用于让「引用可核验」在离线演示中可见；
    - ``False``：本任务强制跳过核验。
    """

    # ===== 实验画像开关（2026-10-22 实验矩阵）=====
    #
    # ## 为什么放在请求契约而不是全局配置
    #
    # 120 次实验要跑四种画像。若用全局配置，每次切换都要重启进程，
    # 且并发跑不同画像会互相污染。放契约里，每个 run 的 `config.json`
    # 天然记录了开关取值 —— 可复现、可并发、可追溯。
    #
    # ## 为什么默认全开
    #
    # 网页用户不传这些字段时必须得到**完整能力**（检索 + 校验 + 复盘），
    # 与改造前一致。任一默认关闭都会让既有用户静默降级。

    retrieval_mode: Literal["none", "vector", "hybrid_rerank"] = "hybrid_rerank"
    """检索模式（实验消融维度）。

    - ``none``：不检索，仅用冻结清单（对应 `single_agent_no_rag` / `multi_agent_no_rag`）；
    - ``vector``：向量检索（`multi_agent_vector_rag`）；
    - ``hybrid_rerank``：向量 + 关键词混合 + 重排（`multi_agent_hybrid_rerank`，默认）。
    """

    validation_enabled: bool = True
    """是否执行 `validate` 节点（引用校验与完整性检查）。

    关闭后 `validate` 节点仍会被**规划**（`REQUIRED_NODE_TYPES` 契约要求），
    但执行期直接返回通过、不调用 LLM。这样 DAG 结构一致，只改变执行行为 ——
    实验对比才干净。
    """

    review_enabled: bool = True
    """是否执行 `review` 节点（复盘与最终正文生成）。

    关闭后 `review` 节点用上游 `write` 节点的输出作为最终正文，不调用 LLM。
    同上：仍被规划，只跳过执行。
    """


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
    #: 综述正文（Markdown）。**必须随快照下发** —— 开放 API / MCP 直接消费它：
    #: `app/mcp/tools.py::get_task_output` 读 `snapshot["final_output"]`，
    #: `get_task_status` 用 `bool(final_output)` 判断「有没有产出」。
    #:
    #: 曾经漏掉过：执行层会**写入** `task.final_output`，但契约里没有这个字段
    #: （且 `extra="forbid"`），序列化时被静默丢弃 → MCP 永远返回 `markdown: null`、
    #: 永远提示「尚无产出」。既有测试用手写 dict 替身伪造了这个字段，所以全绿。
    #:
    #: `None`（而非空串）表示「还没产出」，调用方靠它区分状态。
    final_output: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
