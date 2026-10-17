"""规划 Agent 单元测试：TaskPlan 生成、DAG 构建、schema 校验。"""

import json

import pytest

from app.llm.models import LLMResponse, MessageRole, Usage
from app.orchestration.agents.planner import PlannerAgent, PlannerPlanInvalidError
from app.orchestration.communication import InMemoryMessageBus
from app.orchestration.dag_engine import DAGCycleError
from app.orchestration.messages import MessageType
from tests.llm.mock_llm import MockLLM


def _llm_response(payload: dict) -> LLMResponse:
    return LLMResponse(
        content=json.dumps(payload),
        finish_reason="stop",
        usage=Usage(),
        role=MessageRole.ASSISTANT,
    )


def _plan_payload(nodes: list[dict]) -> dict:
    return {
        "version": 1,
        "topic": "检索增强生成与证据核验",
        "research_questions": ["RAG 如何提升回答可靠性？"],
        "search_queries": ["RAG reliability"],
        "acceptance_criteria": ["引用可回溯"],
        "nodes": nodes,
    }


def _node(node_id: str, dependencies: list[str] | None = None) -> dict:
    return {
        "id": node_id,
        "type": "search" if not dependencies else "write",
        "title": f"节点{node_id}",
        "dependencies": dependencies or [],
    }


@pytest.mark.asyncio
class TestPlannerAgentBasic:
    """规划 Agent 基础功能测试。"""

    async def test_run_without_llm_degrades_gracefully(self) -> None:
        """无 LLM 时降级为确定性三节点计划。"""
        agent = PlannerAgent()
        result = await agent.execute({"requirement": "写一份报告", "trace_id": "t1"})
        assert "requirement_analysis" in result
        assert "plan" in result
        assert "subtasks" in result
        assert "dag" in result
        assert result["requirement_analysis"]["output_format"] == "markdown"
        assert len(result["plan"]["nodes"]) == 3
        assert result["dag"]["topological_order"] == ["n1", "n2", "n3"]

    async def test_agent_type_is_planner(self) -> None:
        assert PlannerAgent().agent_type == "planner"


@pytest.mark.asyncio
class TestPlannerAgentWithLLM:
    """有 LLM 的规划流程测试。"""

    async def test_generates_taskplan_and_dag(self) -> None:
        """Mock LLM 返回合法 TaskPlan，规划流程输出 plan 与 dag。"""
        mock = MockLLM(responses=[_llm_response(_plan_payload([_node("n1"), _node("n2", ["n1"])]))])
        agent = PlannerAgent(llm=mock)

        result = await agent.execute({"requirement": "生成市场分析报告", "trace_id": "t1"})

        assert result["plan"]["topic"] == "检索增强生成与证据核验"
        assert len(result["subtasks"]) == 2
        assert result["dag"]["topological_order"] == ["n1", "n2"]

    async def test_dag_edges_from_dependencies(self) -> None:
        """DAG 边由节点 dependencies 正确推导。"""
        mock = MockLLM(
            responses=[
                _llm_response(
                    _plan_payload(
                        [
                            _node("n1"),
                            _node("n2", ["n1"]),
                            _node("n3", ["n1", "n2"]),
                        ]
                    )
                )
            ]
        )
        agent = PlannerAgent(llm=mock)

        result = await agent.execute({"requirement": "测试规划", "trace_id": "t1"})
        edges = result["dag"]["edges"]
        assert len(edges) == 3
        assert {"from": "n1", "to": "n2"} in edges
        assert {"from": "n1", "to": "n3"} in edges
        assert {"from": "n2", "to": "n3"} in edges

    async def test_dag_cycle_detection_raises(self) -> None:
        """循环依赖应被检测并抛 DAGCycleError。"""
        payload = _plan_payload(
            [
                {"id": "n1", "type": "write", "title": "A", "dependencies": ["n2"]},
                {"id": "n2", "type": "write", "title": "B", "dependencies": ["n1"]},
            ]
        )
        mock = MockLLM(responses=[_llm_response(payload)])
        agent = PlannerAgent(llm=mock)

        with pytest.raises(DAGCycleError, match="循环依赖"):
            await agent.execute({"requirement": "测试循环", "trace_id": "t1"})

    async def test_sends_task_assign_to_executor(self) -> None:
        """无依赖的根节点通过消息总线通知执行 Agent。"""
        bus = InMemoryMessageBus()
        executor_q = await bus.subscribe("executor")

        mock = MockLLM(responses=[_llm_response(_plan_payload([_node("n1")]))])
        agent = PlannerAgent(llm=mock, message_bus=bus)

        await agent.execute({"requirement": "测试派发", "trace_id": "t1"})

        msg = await __import__("asyncio").wait_for(executor_q.get(), timeout=1.0)
        assert msg.message_type is MessageType.TASK_ASSIGN
        assert msg.to_agent == "executor"
        assert msg.content["node_id"] == "n1"

    async def test_invalid_json_raises_schema_error(self) -> None:
        """LLM 输出不是 JSON 时抛 PlannerPlanInvalidError 并保留原始输出。"""
        mock = MockLLM(
            responses=[
                LLMResponse(
                    content="这不是 JSON",
                    finish_reason="stop",
                    usage=Usage(),
                    role=MessageRole.ASSISTANT,
                )
            ]
        )
        agent = PlannerAgent(llm=mock)

        with pytest.raises(PlannerPlanInvalidError) as exc_info:
            await agent.execute({"requirement": "测试非法输出", "trace_id": "t1"})
        assert exc_info.value.raw_output == "这不是 JSON"

    async def test_schema_violation_raises_schema_error(self) -> None:
        """节点 type 非法时抛 PlannerPlanInvalidError。"""
        payload = _plan_payload([{"id": "n1", "type": "unknown_type", "title": "非法节点"}])
        mock = MockLLM(responses=[_llm_response(payload)])
        agent = PlannerAgent(llm=mock)

        with pytest.raises(PlannerPlanInvalidError, match="schema"):
            await agent.execute({"requirement": "测试 schema 违规", "trace_id": "t1"})

    async def test_safe_parse_json_handles_dirty_output(self) -> None:
        """LLM 输出含多余文本时仍能提取 JSON。"""
        result = PlannerAgent._safe_parse_json('一些文字 {"key": "value"} 更多文字')
        assert result == {"key": "value"}

    async def test_safe_parse_json_invalid_returns_empty(self) -> None:
        """完全无法解析时返回空字典。"""
        result = PlannerAgent._safe_parse_json("不是 JSON")
        assert result == {}
