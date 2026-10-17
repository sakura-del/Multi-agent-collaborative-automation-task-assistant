"""规划 Agent：需求解析 → 结构化规划（TaskPlan）→ DAG 生成。

职责：
1. 需求解析：从自然语言提取意图、约束、输出要求（确定性，不依赖 LLM）
2. 任务规划：LLM 输出符合 `TaskPlan` schema 的结构化计划
3. DAG 生成：由 TaskPlan.nodes 构建 TaskDAG 并校验依赖/循环
4. 通知执行 Agent：把无依赖的根节点通过消息总线派发

输出结构：
    {"requirement_analysis": {...}, "plan": TaskPlan JSON,
     "subtasks": [PlanNode JSON], "dag": TaskDAG JSON}

schema 违规以 `PlannerPlanInvalidError` 抛出（保留原始输出），由 TaskExecution
执行一次 schema 修复调用；修复仍失败才进入 WAITING_HUMAN。
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from app.core.logging import get_logger
from app.llm.models import Message, MessageRole
from app.orchestration.base_agent import BaseAgent
from app.orchestration.contracts import PlanNode, TaskPlan
from app.orchestration.dag_engine import TaskDAG
from app.orchestration.messages import MessageType

logger = get_logger(__name__)

_PLAN_PROMPT = """你是文献综述任务规划专家。请把用户需求拆解为可执行的结构化计划。

任务需求：
{requirement}

要求：
- 每个节点可独立执行，标注节点间依赖（dependencies 列出前置节点 id）
- 节点 id 用小写字母开头（如 n1, n2）
- search / extract / write / validate / review 五类节点按需组合
- 只输出 JSON，不要任何解释文字

输出 JSON schema：
{{
  "version": 1,
  "topic": "研究主题（5-200 字）",
  "research_questions": ["研究问题（1-10 条）"],
  "search_queries": ["检索词（1-20 条）"],
  "acceptance_criteria": ["验收标准（1-20 条）"],
  "nodes": [
    {{
      "id": "n1",
      "type": "search | extract | write | validate | review",
      "title": "节点标题",
      "dependencies": [],
      "input": {{}},
      "required_evidence": 1
    }}
  ]
}}
{repair_section}"""

_REPAIR_SECTION = """
上一次输出未通过 schema 校验，请修正后重新输出完整 JSON。
错误信息：{error}
上一次输出（截断）：{previous_output}
"""


class PlannerPlanInvalidError(ValueError):
    """规划输出不是合法 JSON 或不符合 TaskPlan schema。"""

    def __init__(self, message: str, *, raw_output: str = "") -> None:
        super().__init__(message)
        self.raw_output = raw_output


class PlannerAgent(BaseAgent):
    """规划 Agent：将用户需求转化为 TaskPlan 与可执行子任务 DAG。"""

    @property
    def agent_type(self) -> str:
        return "planner"

    async def _run(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """执行规划流程：解析 → 生成 TaskPlan → 构建 DAG → 派发根节点。"""
        requirement = str(input_data.get("requirement", "")).strip()
        if not requirement:
            raise ValueError("规划输入缺少 requirement")
        trace_id = input_data.get("trace_id", "")

        analysis = self._analyze_requirement(requirement)
        if input_data.get("plan_contract") == "literature_review":
            plan = await self._generate_literature_plan(requirement, input_data.get("repair"))
        else:
            plan = await self._generate_plan(requirement, input_data.get("repair"))
        logger.info(
            "planner_planned",
            trace_id=trace_id,
            topic=plan.topic,
            node_count=len(plan.nodes),
        )

        dag = self.build_dag(plan)

        # 无依赖的根节点通知执行 Agent（阶段4 调度器接管前保留该行为）
        for node in plan.nodes:
            if not node.dependencies:
                await self._send_message(
                    "executor",
                    MessageType.TASK_ASSIGN,
                    {
                        "node_id": node.id,
                        "title": node.title,
                        "description": f"{node.type}: {node.title}",
                    },
                    trace_id=trace_id,
                )

        return {
            "requirement_analysis": analysis,
            "plan": plan.model_dump(mode="json"),
            "subtasks": [node.model_dump(mode="json") for node in plan.nodes],
            "dag": dag.to_dict(),
        }

    @staticmethod
    def _analyze_requirement(requirement: str) -> dict[str, Any]:
        """确定性需求解析（不调用 LLM，避免规划链路两次模型开销）。"""
        return {
            "intent": requirement[:100],
            "constraints": [],
            "output_format": "markdown",
            "success_criteria": "完成用户需求",
        }

    async def _generate_plan(
        self,
        requirement: str,
        repair: dict[str, Any] | None,
    ) -> TaskPlan:
        """调用 LLM 生成 TaskPlan；schema 违规抛 PlannerPlanInvalidError。"""
        if self.llm is None:
            return self._degraded_plan(requirement)

        prompt = _PLAN_PROMPT.format(
            requirement=requirement,
            repair_section=self._repair_section(repair),
        )
        response = await self.llm.chat(
            [
                Message(role=MessageRole.SYSTEM, content="你是任务规划专家，只输出 JSON。"),
                Message(role=MessageRole.USER, content=prompt),
            ],
            temperature=0.2,
        )
        content = response.content or ""
        payload = self._safe_parse_json(content)
        if not payload:
            raise PlannerPlanInvalidError("规划输出不是合法 JSON", raw_output=content)
        try:
            return TaskPlan.model_validate(payload)
        except ValidationError as exc:
            raise PlannerPlanInvalidError(
                f"规划未通过 TaskPlan schema 校验: {exc.errors()[:3]}",
                raw_output=content,
            ) from exc

    async def _generate_literature_plan(
        self,
        requirement: str,
        repair: dict[str, Any] | None,
    ) -> TaskPlan:
        """文献场景规划：受约束 Prompt（五类节点 + write 依赖 extract + 无环）。"""
        if self.llm is None:
            return self._degraded_literature_plan(requirement)

        # 方法内导入：workflows 包 __init__ 反向依赖 planner，避免导入环
        from app.orchestration.workflows.prompts import render_planner_prompt

        prompt = render_planner_prompt(
            requirement,
            repair_section=self._repair_section(repair),
        )
        response = await self.llm.chat(
            [
                Message(
                    role=MessageRole.SYSTEM,
                    content="你是文献综述规划专家，只输出满足约束的 JSON。",
                ),
                Message(role=MessageRole.USER, content=prompt),
            ],
            temperature=0.2,
        )
        content = response.content or ""
        payload = self._safe_parse_json(content)
        if not payload:
            raise PlannerPlanInvalidError("规划输出不是合法 JSON", raw_output=content)
        try:
            return TaskPlan.model_validate(payload)
        except ValidationError as exc:
            raise PlannerPlanInvalidError(
                f"规划未通过 TaskPlan schema 校验: {exc.errors()[:3]}",
                raw_output=content,
            ) from exc

    @staticmethod
    def _degraded_literature_plan(requirement: str) -> TaskPlan:
        """无 LLM 的确定性文献计划：search → extract → write → validate → review。"""
        topic = requirement if len(requirement) >= 5 else f"{requirement} 文献综述"
        query = requirement[:200]
        return TaskPlan(
            version=1,
            topic=topic[:200],
            research_questions=[
                f"{query} 的核心方法有哪些？",
                f"{query} 的评估指标是什么？",
                f"{query} 的证据可追溯性如何保证？",
            ],
            search_queries=[query],
            acceptance_criteria=["生成带可回溯引用的综述", "全部引用标记可解析到证据"],
            nodes=[
                PlanNode(
                    id="n1",
                    type="search",
                    title="检索文献",
                    input={"query": query},
                ),
                PlanNode(id="n2", type="extract", title="提取证据", dependencies=["n1"]),
                PlanNode(id="n3", type="write", title="撰写综述", dependencies=["n2"]),
                PlanNode(id="n4", type="validate", title="校验引用", dependencies=["n3"]),
                PlanNode(id="n5", type="review", title="汇总复盘", dependencies=["n4"]),
            ],
        )

    @staticmethod
    def _repair_section(repair: dict[str, Any] | None) -> str:
        if not repair:
            return ""
        return _REPAIR_SECTION.format(
            error=str(repair.get("error", ""))[:500],
            previous_output=str(repair.get("previous_output", ""))[:2000],
        )

    @staticmethod
    def _degraded_plan(requirement: str) -> TaskPlan:
        """无 LLM 时的确定性降级计划：检索 → 提取 → 撰写。"""
        topic = requirement if len(requirement) >= 5 else f"{requirement} 文献综述"
        return TaskPlan(
            version=1,
            topic=topic[:200],
            research_questions=[requirement[:200]],
            search_queries=[requirement[:200]],
            acceptance_criteria=["生成带可回溯引用的综述"],
            nodes=[
                PlanNode(
                    id="n1",
                    type="search",
                    title="检索文献",
                    input={"query": requirement[:200]},
                ),
                PlanNode(
                    id="n2",
                    type="extract",
                    title="提取证据",
                    dependencies=["n1"],
                ),
                PlanNode(
                    id="n3",
                    type="write",
                    title="撰写综述",
                    dependencies=["n2"],
                ),
            ],
        )

    @staticmethod
    def build_dag(plan: TaskPlan) -> TaskDAG:
        """由 TaskPlan 构建 TaskDAG；循环依赖由 add_edge 抛 DAGCycleError。"""
        dag = TaskDAG()
        for node in plan.nodes:
            dag.add_node(
                node.id,
                title=node.title,
                description=f"{node.type}: {node.title}",
                type=node.type,
            )
        for node in plan.nodes:
            for dependency in node.dependencies:
                dag.add_edge(dependency, node.id)
        return dag

    @staticmethod
    def _safe_parse_json(text: str) -> dict[str, Any]:
        """容错 JSON 解析：LLM 输出可能含多余文本，提取首个 JSON 块。"""
        text = text.strip()
        # 尝试直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # 回退：提取 {} 包裹的内容
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
        logger.warning("planner_json_parse_failed", text_preview=text[:200])
        return {}
