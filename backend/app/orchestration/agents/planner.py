"""规划 Agent：需求解析 → 任务拆解 → DAG 生成。

职责：
1. 需求解析：从自然语言提取意图、约束、输出要求
2. 任务拆解：LLM 驱动递归分解为可执行子任务
3. DAG 生成：构建节点+边+拓扑序，输出依赖关系

输出结构对齐数据库 Subtask / TaskDag 表，供调度器消费。
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from app.core.logging import get_logger
from app.llm.models import Message, MessageRole
from app.orchestration.base_agent import BaseAgent
from app.orchestration.messages import MessageType

logger = get_logger(__name__)

# 需求解析 Prompt：提取结构化意图，约束 LLM 输出 JSON
_PARSE_PROMPT = """你是一个需求分析专家。请解析以下任务需求，提取意图、约束与输出要求。

任务需求：
{requirement}

请以 JSON 格式返回（仅输出 JSON，不要其他文字）：
{{
  "intent": "任务核心意图（一句话）",
  "constraints": ["约束1", "约束2"],
  "output_format": "期望输出格式（如 markdown/pdf/xlsx）",
  "success_criteria": "完成标准描述"
}}
"""

# 任务拆解 Prompt：将需求分解为有序子任务
_DECOMPOSE_PROMPT = """你是一个任务规划专家。请将以下需求拆解为可执行的子任务列表。

需求意图：{intent}
约束条件：{constraints}
输出格式：{output_format}

拆解要求：
- 每个子任务应可独立执行
- 标注子任务间的依赖关系（depends_on 列出前置节点编号）
- 节点编号用 n1, n2, n3 ...

请以 JSON 格式返回（仅输出 JSON）：
{{
  "nodes": [
    {{"node_id": "n1", "title": "子任务标题", "description": "详细描述", "depends_on": []}},
    {{"node_id": "n2", "title": "...", "description": "...", "depends_on": ["n1"]}}
  ]
}}
"""


class PlannerAgent(BaseAgent):
    """规划 Agent：将用户需求转化为可执行的子任务 DAG。"""

    @property
    def agent_type(self) -> str:
        return "planner"

    async def _run(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """执行规划流程：解析 → 拆解 → DAG 生成 → 通知执行 Agent。

        input_data 需包含 requirement 字段（用户自然语言需求）。
        返回 {requirement_analysis, subtasks, dag}。
        """
        requirement = input_data["requirement"]
        trace_id = input_data.get("trace_id", "")

        # 步骤1：需求解析
        analysis = await self._parse_requirement(requirement)
        logger.info("planner_parsed", trace_id=trace_id, intent=analysis.get("intent"))

        # 步骤2：任务拆解
        decomposition = await self._decompose(analysis)
        nodes = decomposition.get("nodes", [])
        logger.info("planner_decomposed", trace_id=trace_id, node_count=len(nodes))

        # 步骤3：构建 DAG（校验依赖 + 拓扑排序）
        dag = self._build_dag(nodes)

        # 步骤4：通知执行 Agent 接收首批无依赖子任务
        for node in nodes:
            if not node.get("depends_on"):
                await self._send_message(
                    "executor",
                    MessageType.TASK_ASSIGN,
                    {
                        "node_id": node["node_id"],
                        "title": node["title"],
                        "description": node["description"],
                    },
                    trace_id=trace_id,
                )

        return {
            "requirement_analysis": analysis,
            "subtasks": nodes,
            "dag": dag,
        }

    async def _parse_requirement(self, requirement: str) -> dict[str, Any]:
        """LLM 驱动的需求解析，返回结构化意图。"""
        if self.llm is None:
            # 无 LLM 时的降级：返回最小可用结构，便于测试
            return {
                "intent": requirement[:100],
                "constraints": [],
                "output_format": "markdown",
                "success_criteria": "完成用户需求",
            }

        response = await self.llm.chat(
            [
                Message(role=MessageRole.SYSTEM, content="你是需求分析专家，只输出 JSON。"),
                Message(
                    role=MessageRole.USER,
                    content=_PARSE_PROMPT.format(requirement=requirement),
                ),
            ],
            temperature=0.2,  # 低温度保证结构化输出稳定
        )
        return self._safe_parse_json(response.content or "{}")

    async def _decompose(self, analysis: dict[str, Any]) -> dict[str, Any]:
        """LLM 驱动的任务拆解，返回子任务节点列表。"""
        if self.llm is None:
            return {"nodes": []}

        response = await self.llm.chat(
            [
                Message(role=MessageRole.SYSTEM, content="你是任务规划专家，只输出 JSON。"),
                Message(
                    role=MessageRole.USER,
                    content=_DECOMPOSE_PROMPT.format(
                        intent=analysis.get("intent", ""),
                        constraints=analysis.get("constraints", []),
                        output_format=analysis.get("output_format", "markdown"),
                    ),
                ),
            ],
            temperature=0.3,
        )
        return self._safe_parse_json(response.content or '{"nodes": []}')

    def _build_dag(self, nodes: list[dict[str, Any]]) -> dict[str, Any]:
        """从节点列表构建 DAG 结构（含拓扑排序）。

        边由 depends_on 推导：前置依赖 → 当前节点。
        拓扑序用 Kahn 算法计算，检测循环依赖。
        """
        node_ids = {n["node_id"] for n in nodes}
        edges: list[dict[str, str]] = []
        # 邻接表 + 入度表
        in_degree: dict[str, int] = {n["node_id"]: 0 for n in nodes}
        adjacency: dict[str, list[str]] = {n["node_id"]: [] for n in nodes}

        for node in nodes:
            for dep in node.get("depends_on", []):
                if dep in node_ids:
                    edges.append({"from": dep, "to": node["node_id"]})
                    adjacency[dep].append(node["node_id"])
                    in_degree[node["node_id"]] += 1

        # Kahn 拓扑排序
        queue = [nid for nid, deg in in_degree.items() if deg == 0]
        topo_order: list[str] = []
        while queue:
            nid = queue.pop(0)
            topo_order.append(nid)
            for child in adjacency[nid]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        # 拓扑序不完整说明存在循环依赖
        if len(topo_order) != len(nodes):
            cyclic = set(in_degree) - set(topo_order)
            raise ValueError(f"DAG 存在循环依赖，涉及节点: {cyclic}")

        return {
            "nodes": nodes,
            "edges": edges,
            "topological_order": topo_order,
            "task_id": str(uuid.uuid4()),
        }

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
