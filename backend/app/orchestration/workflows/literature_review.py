"""文献综述场景编排（Plan 3 Task 3）。

五个外部缝（供 TaskExecution / 测试使用）：

    plan(task) -> TaskPlan                    规划 + 文献契约校验（一次修复）
    prepare_node(task, node) -> dict          证据上下文与查询准备
    execute_node(task, node) -> ExecutionResult  search 走 arXiv；extract/write 走证据约束执行
    validate_node(task, result) -> ValidationReport
    review(task, results) -> ReviewReport       只基于已持久化结果组装

约束：
- 计划必须含五类节点、至少一个终端 review、每个 write 依赖 extract、3–10 个研究问题；
- 契约不满足只做一次修复请求，仍失败抛 `HumanActionRequiredError`（转 WAITING_HUMAN）；
- validate/review 节点不调用任意工具；
- 证据只来自 `LiteratureContextBuilder`（owner-scoped），引用标记必须落在证据范围内。
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from pydantic import ValidationError as PydanticValidationError

from app.core.exceptions import HumanActionRequiredError
from app.core.logging import get_logger
from app.knowledge.services.citation_validator import ContentRole
from app.knowledge.services.reference_verifier import candidates_from_artifacts
from app.orchestration.agents.planner import PlannerAgent, PlannerPlanInvalidError
from app.orchestration.base_agent import BaseAgent
from app.orchestration.contracts import (
    Evidence,
    ExecutionResult,
    PlanNode,
    ReviewReport,
    TaskPlan,
    ValidationReport,
)
from app.orchestration.dag_engine import DAGCycleError, TaskDAG
from app.orchestration.workflows.prompts import (
    render_executor_prompt,
    render_planner_prompt,
)
from app.tools.literature.models import (
    MAX_SEARCH_RESULTS,
    MIN_SEARCH_RESULTS,
    LiteratureSearch,
)

logger = get_logger(__name__)

#: 检索节点未指定条数时的默认值（与 `LiteratureSearch.max_results` 默认一致）
DEFAULT_SEARCH_RESULTS = 10


def clamp_max_results(raw: Any) -> int:
    """把 LLM 给的 `max_results` **夹到** `LiteratureSearch` 的合法区间。

    为什么需要夹紧而不是直接报错：检索节点参数由**规划模型自由生成**，
    `max_results=60` 这类值很常见（用户说"找 60 篇文献"时尤其自然）。
    直接把它传给 `LiteratureSearch` 会触发 `le=50` 校验错 → 执行器重试 4 次
    （换策略也改变不了这个数字）→ 节点转 `waiting_human`，
    用户在界面上只看到"任务卡住"（2026-10-21 浏览器 E2E 实测踩到）。

    语义：能解析成整数的夹到 `[1, 50]`；解析不了（None/字符串/布尔）
    退回默认 10 —— 宁可少取几条，也不要让整篇综述失败。
    """
    if isinstance(raw, bool):  # bool 是 int 子类，必须先排除
        return DEFAULT_SEARCH_RESULTS
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, float) and raw.is_integer():
        value = int(raw)
    elif isinstance(raw, str):
        try:
            value = int(raw.strip())
        except ValueError:
            return DEFAULT_SEARCH_RESULTS
    else:
        return DEFAULT_SEARCH_RESULTS
    return max(MIN_SEARCH_RESULTS, min(MAX_SEARCH_RESULTS, value))


#: 文献场景允许的节点类型（与 PlanNode.type 一致）
REQUIRED_NODE_TYPES = frozenset({"search", "extract", "write", "validate", "review"})
#: 需要证据上下文的节点类型
EVIDENCE_NODE_TYPES = frozenset({"extract", "write"})

#: 输出为机械汇总/检索结果的节点类型（Issue #6）：其 content 不是作者文本
AGGREGATE_NODE_TYPES = frozenset({"search", "validate", "review"})


def content_role_for_node(node_type: str) -> ContentRole:
    """节点类型 → 内容角色（Issue #6）。

    `extract` / `write` 由模型在有证据约束下创作正文，属 `authored`；
    `search`（检索结果列表）与 `validate` / `review`（依赖输出机械汇总）都不是作者文本，
    作者级文本质量检查（裸断言、重复段落比例）对它们没有意义——跨节点复述是固有形态，
    且重试无法修复（实测约 40% 运行因此转人工）。

    未知类型（含空串）一律按 **authored** 从严处理：宁可多查，不可在无法判定时静默放宽。
    """
    return "authored" if node_type not in AGGREGATE_NODE_TYPES else "aggregated"


#: 研究问题数量区间
MIN_RESEARCH_QUESTIONS = 3
MAX_RESEARCH_QUESTIONS = 10


class LiteraturePlanContractError(ValueError):
    """计划不满足文献综述契约（五类节点 / 终端 review / write 依赖 extract）。"""


class LiteratureReviewWorkflow:
    """文献综述场景编排：把规划、证据检索、执行、校验、复盘串成一条链路。"""

    def __init__(
        self,
        *,
        planner: Any | None = None,
        executor: Any | None = None,
        validator: Any | None = None,
        reviewer: Any | None = None,
        context_builder: Any | None = None,
        source: Any | None = None,
        reference_verifier: Any | None = None,
        top_k: int = 8,
    ) -> None:
        self._planner = planner
        self._executor = executor
        self._validator = validator
        self._reviewer = reviewer
        self._context_builder = context_builder
        self._source = source
        # 引用真实性核验器（Issue #3 / 计划 §2.6）：None 表示不核验，校验节点保持原行为
        self._reference_verifier = reference_verifier
        self._top_k = top_k
        self._plan: TaskPlan | None = None
        # 节点证据缓存：重生成时复用同一证据集（Plan 3 Task 4）
        self._prepared: dict[str, dict[str, Any]] = {}

    # ===== 计划管理 =====

    def use_plan(self, plan: TaskPlan) -> LiteratureReviewWorkflow:
        """绑定当前计划并返回工作流自身（便于链式构造）。

        重启恢复或执行期由 TaskExecution 注入已持久化计划。
        """
        self._plan = plan
        return self

    def _node_title(self, node_id: str) -> str:
        """取节点标题；计划未绑定时退回 node_id（校验器只用于日志/提示）。"""
        if self._plan is None:
            return node_id
        for node in self._plan.nodes:
            if node.id == node_id:
                return node.title
        return node_id

    def _node_type(self, node_id: str) -> str:
        """取节点类型：先查已备好的上下文，再查绑定计划；都查不到返回空串。

        （Issue #6 用：`validate`/`review` 节点在 `execute_node` 里不会走 `prepare_node`，
        故不能只依赖 `prepared_for`。）
        """
        prepared = self.prepared_for(node_id) or {}
        node_type = str(prepared.get("node_type") or "")
        if node_type:
            return node_type
        if self._plan is not None:
            for node in self._plan.nodes:
                if node.id == node_id:
                    return node.type
        return ""

    def require_node(self, node_id: str) -> PlanNode:
        """按 node_id 取计划节点；计划缺失时要求人工介入。"""
        if self._plan is None:
            raise HumanActionRequiredError("工作流尚未绑定计划")
        for node in self._plan.nodes:
            if node.id == node_id:
                return node
        raise HumanActionRequiredError(f"计划中不存在节点 {node_id}")

    # ===== 缝 1：规划 =====

    async def plan(self, task: Any, *, repair: dict[str, Any] | None = None) -> TaskPlan:
        """生成并校验文献计划；失败时只做一次修复请求，再失败要求人工介入。"""
        requirement = self._requirement(task)
        if not requirement:
            raise HumanActionRequiredError("任务缺少可规划的需求描述")

        try:
            plan = await self._request_plan(task, requirement, repair)
            self.validate_plan_contract(plan)
        except (
            PlannerPlanInvalidError,
            LiteraturePlanContractError,
            DAGCycleError,
            PydanticValidationError,
            ValueError,
        ) as first_error:
            logger.warning("literature_plan_repair", error=str(first_error))
            repair_payload = {
                "error": str(first_error),
                "previous_output": getattr(first_error, "raw_output", ""),
            }
            try:
                plan = await self._request_plan(task, requirement, repair_payload)
                self.validate_plan_contract(plan)
            except (
                PlannerPlanInvalidError,
                LiteraturePlanContractError,
                DAGCycleError,
                PydanticValidationError,
                ValueError,
            ) as second_error:
                raise HumanActionRequiredError(
                    f"规划修复后仍未满足文献契约: {second_error}"
                ) from second_error

        self._plan = plan
        logger.info(
            "literature_plan_ready",
            topic=plan.topic,
            node_count=len(plan.nodes),
        )
        return plan

    async def _request_plan(
        self,
        task: Any,
        requirement: str,
        repair: dict[str, Any] | None,
    ) -> TaskPlan:
        planner = self._planner
        if planner is None:
            raise HumanActionRequiredError("缺少规划 Agent")
        result = await planner.execute(
            {
                "requirement": requirement,
                "trace_id": self._trace_id(task),
                "plan_contract": "literature_review",
                "repair": repair,
            }
        )
        if not isinstance(result, dict) or not isinstance(result.get("plan"), dict):
            raise PlannerPlanInvalidError("规划输出缺少 plan", raw_output=str(result)[:2000])
        try:
            return TaskPlan.model_validate(result["plan"])
        except PydanticValidationError as exc:
            raise PlannerPlanInvalidError(
                f"规划未通过 TaskPlan schema 校验: {exc.errors()[:3]}",
                raw_output=str(result["plan"])[:2000],
            ) from exc

    @staticmethod
    def validate_plan_contract(plan: TaskPlan) -> None:
        """校验文献契约：研究问题数量、五类节点、终端 review、write 依赖 extract。"""
        question_count = len(plan.research_questions)
        if not MIN_RESEARCH_QUESTIONS <= question_count <= MAX_RESEARCH_QUESTIONS:
            raise LiteraturePlanContractError(
                f"research_questions 必须为 {MIN_RESEARCH_QUESTIONS}-"
                f"{MAX_RESEARCH_QUESTIONS} 条，当前 {question_count} 条"
            )

        dag = PlannerAgent.build_dag(plan)
        missing = REQUIRED_NODE_TYPES - {node.type for node in plan.nodes}
        if missing:
            raise LiteraturePlanContractError(f"缺少节点类型: {sorted(missing)}")

        review_nodes = [node for node in plan.nodes if node.type == "review"]
        if not any(not dag.get_successors(node.id) for node in review_nodes):
            raise LiteraturePlanContractError("缺少终端 review 节点（无后继）")

        for node in plan.nodes:
            if node.type != "write":
                continue
            predecessors = LiteratureReviewWorkflow._transitive_predecessors(dag, node.id)
            if not any(dag.get_node(pred).get("type") == "extract" for pred in predecessors):
                raise LiteraturePlanContractError(f"write 节点 {node.id} 未依赖 extract 节点")

    @staticmethod
    def _transitive_predecessors(dag: TaskDAG, node_id: str) -> set[str]:
        seen: set[str] = set()
        stack = list(dag.get_predecessors(node_id))
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            stack.extend(dag.get_predecessors(current))
        return seen

    # ===== 缝 2：节点准备 =====

    async def prepare_node(
        self,
        task: Any,
        node: PlanNode | dict[str, Any],
        *,
        dependency_outputs: dict[str, Any] | None = None,
        plan: TaskPlan | None = None,
    ) -> dict[str, Any]:
        """准备节点执行上下文：证据节点检索 owner-scoped 证据，其余只带依赖输出。"""
        plan_node = self._as_node(node)
        prepared: dict[str, Any] = {
            "node_id": plan_node.id,
            "node_type": plan_node.type,
            "title": plan_node.title,
            "description": f"{plan_node.type}: {plan_node.title}",
            "query": self._query_for(task, plan_node, plan),
            "evidence_context": "",
            "evidence": [],
            "insufficient_evidence": False,
            "warnings": [],
        }
        if dependency_outputs:
            prepared["dependency_outputs"] = dependency_outputs

        if plan_node.type in EVIDENCE_NODE_TYPES:
            context = await self._build_context(task, prepared["query"])
            prepared["evidence_context"] = context.text
            prepared["evidence"] = [item.model_dump(mode="json") for item in context.evidence]
            prepared["insufficient_evidence"] = context.insufficient
            if context.insufficient:
                prepared["warnings"].append("insufficient_evidence")
        self._prepared[plan_node.id] = prepared
        return prepared

    def prepared_for(self, node_id: str) -> dict[str, Any] | None:
        """返回节点上次准备的上下文（重生成时复用同一证据集）。"""
        return self._prepared.get(node_id)

    async def _build_context(self, task: Any, query: str) -> Any:
        if self._context_builder is None:
            raise HumanActionRequiredError("缺少证据上下文构建器")
        user_id = getattr(task, "user_id", None)
        if not isinstance(user_id, UUID):
            raise HumanActionRequiredError("任务缺少 user_id，无法检索证据")
        return await self._context_builder.build(user_id, query, top_k=self._top_k)

    # ===== 缝 3：节点执行 =====

    async def execute_node(
        self,
        task: Any,
        node: PlanNode | dict[str, Any],
        *,
        dependency_outputs: dict[str, Any] | None = None,
        plan: TaskPlan | None = None,
        validation_feedback: str = "",
        prepared: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        """执行单个节点：search 走文献源；extract/write 走证据约束执行；其余只汇总。

        传 `prepared`（或工作流内已有缓存）时复用同一证据集，满足定向重做要求。
        """
        plan_node = self._as_node(node)

        if plan_node.type == "search":
            return await self._execute_search(task, plan_node, plan)

        if plan_node.type in EVIDENCE_NODE_TYPES:
            prepared_node = (
                prepared
                or self.prepared_for(plan_node.id)
                or await self.prepare_node(
                    task, plan_node, dependency_outputs=dependency_outputs, plan=plan
                )
            )
            if self._executor is None:
                raise HumanActionRequiredError("缺少执行 Agent")
            evidence = [Evidence.model_validate(item) for item in prepared_node["evidence"]]
            result = await self._executor.execute_result(
                node_id=plan_node.id,
                title=plan_node.title,
                description=prepared_node["description"],
                evidence=evidence,
                evidence_text=prepared_node["evidence_context"],
                dependency_outputs=self._rebased_dependency_outputs(
                    dependency_outputs or {}, evidence
                ),
                validation_feedback=validation_feedback,
                trace_id=self._trace_id(task),
            )
            # Issue #7：标注节点类型，供复盘区分作者正文与机械汇总
            result.node_type = plan_node.type
            return result

        # validate / review：不调用任何工具，只汇总依赖输出
        content = self._summarize_dependencies(dependency_outputs or {})
        warnings = [] if content else ["missing_dependency_output"]
        return ExecutionResult(
            node_id=plan_node.id,
            content=content,
            warnings=warnings,
            duration_ms=0,
            node_type=plan_node.type,
        )

    async def _execute_search(
        self,
        task: Any,
        node: PlanNode,
        plan: TaskPlan | None,
    ) -> ExecutionResult:
        if self._source is None:
            raise HumanActionRequiredError("缺少文献检索源（arXiv 或离线清单）")
        query = self._query_for(task, node, plan)
        max_results = clamp_max_results(node.input.get("max_results"))
        records = await self._source.search(LiteratureSearch(query=query, max_results=max_results))
        content = "\n".join(
            f"- {record.title} ({record.source_id}) {record.source_url}" for record in records
        )
        return ExecutionResult(
            node_id=node.id,
            content=content,
            artifacts=[record.model_dump(mode="json") for record in records],
            duration_ms=0,
            node_type=node.type,
        )

    # ===== 缝 4/5：校验与复盘 =====

    async def validate_node(self, task: Any, result: ExecutionResult) -> ValidationReport:
        """委托校验 Agent；返回类型化 `ValidationReport`。

        引用真实性核验（Issue #3 / 计划 §2.6）：核验器存在且能从检索 artifacts 解析出候选时，
        先执行 `verify_many`，再把结论以 `reference_verifications` 随载荷交给校验 Agent。
        核验依赖外部元数据源，异常只降级为「无核验结论」，不得让校验节点失败。
        """
        if self._validator is None:
            raise HumanActionRequiredError("缺少校验 Agent")
        prepared = self.prepared_for(result.node_id) or {}
        request: dict[str, Any] = {
            "node_id": result.node_id,
            "title": self._node_title(result.node_id),
            "result": result.content,
            "evidence": [item.model_dump(mode="json") for item in result.evidence],
            "evidence_pool": prepared.get("evidence", []),
            "execution_result": result.model_dump(mode="json"),
            "task_id": str(getattr(task, "id", "")),
            "trace_id": self._trace_id(task),
            # Issue #6：search/validate/review 的 content 是机械汇总（依赖输出拼接），
            # 跨节点复述属固有形态，须与作者正文区分，否则会触发无法通过重试修复的失败
            "content_role": content_role_for_node(self._node_type(result.node_id)),
        }
        verifications = await self._verify_artifacts(result)
        if verifications:
            request["reference_verifications"] = [
                verification.model_dump(mode="json") for verification in verifications
            ]
        payload = await self._validator.execute(request)
        if isinstance(payload, ValidationReport):
            return payload
        known_fields = set(ValidationReport.model_fields)
        return ValidationReport.model_validate(
            {key: value for key, value in payload.items() if key in known_fields}
        )

    async def _verify_artifacts(self, result: ExecutionResult) -> list[Any]:
        """对检索节点 artifacts 做引用真实性核验；无核验器 / 无候选 / 异常时返回空列表。"""
        if self._reference_verifier is None or not result.artifacts:
            return []
        try:
            candidates = candidates_from_artifacts(result.artifacts)
            if not candidates:
                return []
            return list(await self._reference_verifier.verify_many(candidates))
        except Exception as exc:  # 外部核验失败不得让校验节点失败
            logger.warning("reference_verification_failed", node_id=result.node_id, error=str(exc))
            return []

    async def review(self, task: Any, results: list[ExecutionResult]) -> ReviewReport:
        """只基于已持久化结果组装复盘报告；不调用外部工具。"""
        if not results:
            raise HumanActionRequiredError("复盘缺少节点结果")
        if self._reviewer is None:
            raise HumanActionRequiredError("缺少复盘 Agent")
        payload = await self._reviewer.execute(
            {
                "task_id": str(getattr(task, "id", "")),
                "trace_id": self._trace_id(task),
                "results": [item.model_dump(mode="json") for item in results],
            }
        )
        if isinstance(payload, ReviewReport):
            return payload
        return ReviewReport.model_validate(payload)

    # ===== 辅助 =====

    @staticmethod
    def _requirement(task: Any) -> str:
        description = str(getattr(task, "description", "") or "").strip()
        title = str(getattr(task, "title", "") or "").strip()
        return description or title

    @staticmethod
    def _trace_id(task: Any) -> str:
        config = getattr(task, "config", None) or {}
        if isinstance(config, dict) and config.get("trace_id"):
            return str(config["trace_id"])
        return f"literature-{getattr(task, 'id', 'unknown')}"

    @staticmethod
    def _as_node(node: PlanNode | dict[str, Any]) -> PlanNode:
        if isinstance(node, PlanNode):
            return node
        return PlanNode.model_validate(node)

    def _query_for(
        self,
        task: Any,
        node: PlanNode,
        plan: TaskPlan | None,
    ) -> str:
        query = str(node.input.get("query", "")).strip()
        if query:
            return query
        current_plan = plan or self._plan
        if current_plan is not None and current_plan.search_queries:
            return current_plan.search_queries[0]
        return str(getattr(task, "title", "") or "")[:300] or "literature review"

    def _rebased_dependency_outputs(
        self,
        dependency_outputs: dict[str, Any],
        target_evidence: list[Evidence],
    ) -> dict[str, str]:
        """把依赖输出中的 `[E{n}]` 重定位到当前节点证据池（保持本地编号可解析）。"""
        index = {
            (str(item.doc_id), str(item.chunk_id)): position
            for position, item in enumerate(target_evidence, start=1)
        }
        rebased: dict[str, str] = {}
        for dep_id, output in dependency_outputs.items():
            if isinstance(output, dict):
                text = str(output.get("result", "") or output.get("content", ""))
            else:
                text = str(output)
            rebased[dep_id] = self._rebase_markers(text, dep_id, index)
        return rebased

    def _summarize_dependencies(
        self,
        dependency_outputs: dict[str, Any],
        target_evidence: list[Evidence] | None = None,
    ) -> str:
        """汇总依赖输出；把各依赖节点的局部 `[E{n}]` 重定位到当前节点的证据池。

        证据按 (doc_id, chunk_id) 归一：能在当前节点证据池中找到的保留并改为本地编号，
        找不到的标记直接去掉——否则汇总文本会出现无法解析的引用（Plan 3 引用可回溯要求）。
        """
        target = target_evidence or []
        index = {
            (str(item.doc_id), str(item.chunk_id)): position
            for position, item in enumerate(target, start=1)
        }
        lines = []
        for dep_id, output in dependency_outputs.items():
            if isinstance(output, dict):
                text = str(output.get("result", "") or output.get("content", ""))
            else:
                text = str(output)
            lines.append(f"[{dep_id}] {self._rebase_markers(text, dep_id, index)}".strip())
        return "\n".join(lines)

    def _rebase_markers(
        self,
        text: str,
        source_node_id: str,
        index: dict[tuple[str, str], int],
    ) -> str:
        """按来源节点的证据清单重写 `[E{n}]`。"""
        prepared = self.prepared_for(source_node_id) or {}
        source = [Evidence.model_validate(item) for item in prepared.get("evidence", [])]

        def replace(match: re.Match[str]) -> str:
            number = int(match.group(1))
            if number < 1 or number > len(source):
                return ""
            evidence = source[number - 1]
            local = index.get((str(evidence.doc_id), str(evidence.chunk_id)))
            return f"[E{local}]" if local else ""

        rebased = re.sub(r"\[E(\d+)\]", replace, text)
        return re.sub(r"[ \t]{2,}", " ", rebased).strip()


class LiteratureNodeExecutor(BaseAgent):
    """把 `LiteratureReviewWorkflow.execute_node` 适配成调度器可用的 executor。"""

    def __init__(self, workflow: LiteratureReviewWorkflow, task: Any) -> None:
        super().__init__()
        self._workflow = workflow
        self._task = task

    @property
    def agent_type(self) -> str:
        return "executor"

    async def _run(self, input_data: dict[str, Any]) -> dict[str, Any]:
        node = self._workflow.require_node(str(input_data.get("node_id", "")))
        # 重生成：复用首次准备好的证据集，并把校验反馈传给执行器
        prepared = self._workflow.prepared_for(node.id)
        execution = await self._workflow.execute_node(
            self._task,
            node,
            dependency_outputs=input_data.get("dependency_outputs"),
            validation_feedback=str(input_data.get("validation_feedback", "")),
            prepared=prepared,
        )
        prepared_after = self._workflow.prepared_for(node.id) or {}
        return {
            "node_id": node.id,
            "result": execution.content,
            "status": "completed",
            "used_tools": [],
            "warnings": execution.warnings,
            "execution_result": execution.model_dump(mode="json"),
            "evidence_pool": prepared_after.get("evidence", []),
        }


class LiteratureNodeValidator(BaseAgent):
    """把 `LiteratureReviewWorkflow.validate_node` 适配成调度器可用的 validator。"""

    def __init__(self, workflow: LiteratureReviewWorkflow, task: Any) -> None:
        super().__init__()
        self._workflow = workflow
        self._task = task

    @property
    def agent_type(self) -> str:
        return "validator"

    async def _run(self, input_data: dict[str, Any]) -> dict[str, Any]:
        payload = input_data.get("execution_result")
        if isinstance(payload, dict):
            try:
                result = ExecutionResult.model_validate(payload)
            except PydanticValidationError:
                result = None
        else:
            result = None
        if result is None:
            result = ExecutionResult(
                node_id=str(input_data.get("node_id", "")),
                content=str(input_data.get("result", "")),
                duration_ms=0,
            )
        report = await self._workflow.validate_node(self._task, result)
        return report.model_dump(mode="json")


__all__ = [
    "EVIDENCE_NODE_TYPES",
    "REQUIRED_NODE_TYPES",
    "LiteratureNodeExecutor",
    "LiteratureNodeValidator",
    "LiteraturePlanContractError",
    "LiteratureReviewWorkflow",
    "render_executor_prompt",
    "render_planner_prompt",
]
