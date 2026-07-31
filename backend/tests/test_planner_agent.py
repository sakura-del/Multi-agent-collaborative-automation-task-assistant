"""规划 Agent 单元测试：需求解析、任务拆解、DAG 生成。"""

import json

import pytest

from app.llm.models import LLMResponse, MessageRole, Usage
from app.orchestration.agents.planner import PlannerAgent
from app.orchestration.communication import InMemoryMessageBus
from app.orchestration.messages import MessageType
from tests.llm.mock_llm import MockLLM


@pytest.mark.asyncio
class TestPlannerAgentBasic:
    """规划 Agent 基础功能测试。"""

    async def test_run_without_llm_degrades_gracefully(self) -> None:
        """无 LLM 时降级返回最小可用结构。"""
        agent = PlannerAgent()
        result = await agent.execute({"requirement": "写一份报告", "trace_id": "t1"})
        assert "requirement_analysis" in result
        assert "subtasks" in result
        assert "dag" in result
        assert result["requirement_analysis"]["output_format"] == "markdown"

    async def test_agent_type_is_planner(self) -> None:
        assert PlannerAgent().agent_type == "planner"


@pytest.mark.asyncio
class TestPlannerAgentWithLLM:
    """有 LLM 的规划流程测试。"""

    async def test_parse_and_decompose_with_mock_llm(self) -> None:
        """Mock LLM 返回结构化 JSON，规划流程正确解析。"""
        parse_response = LLMResponse(
            content=json.dumps(
                {
                    "intent": "生成市场分析报告",
                    "constraints": ["使用中文"],
                    "output_format": "markdown",
                    "success_criteria": "包含数据图表",
                }
            ),
            finish_reason="stop",
            usage=Usage(),
            role=MessageRole.ASSISTANT,
        )
        decompose_response = LLMResponse(
            content=json.dumps(
                {
                    "nodes": [
                        {
                            "node_id": "n1",
                            "title": "收集数据",
                            "description": "收集市场数据",
                            "depends_on": [],
                        },
                        {
                            "node_id": "n2",
                            "title": "撰写报告",
                            "description": "基于数据撰写报告",
                            "depends_on": ["n1"],
                        },
                    ]
                }
            ),
            finish_reason="stop",
            usage=Usage(),
            role=MessageRole.ASSISTANT,
        )
        mock = MockLLM(responses=[parse_response, decompose_response])
        agent = PlannerAgent(llm=mock)

        result = await agent.execute({"requirement": "生成市场分析报告", "trace_id": "t1"})

        assert result["requirement_analysis"]["intent"] == "生成市场分析报告"
        assert len(result["subtasks"]) == 2
        assert result["dag"]["topological_order"] == ["n1", "n2"]

    async def test_dag_edges_from_depends_on(self) -> None:
        """DAG 边由 depends_on 正确推导。"""
        decompose_response = LLMResponse(
            content=json.dumps(
                {
                    "nodes": [
                        {"node_id": "n1", "title": "A", "description": "a", "depends_on": []},
                        {"node_id": "n2", "title": "B", "description": "b", "depends_on": ["n1"]},
                        {
                            "node_id": "n3",
                            "title": "C",
                            "description": "c",
                            "depends_on": ["n1", "n2"],
                        },
                    ]
                }
            ),
            finish_reason="stop",
            usage=Usage(),
            role=MessageRole.ASSISTANT,
        )
        # 规划流程会先调需求解析，再调拆解
        parse_response = LLMResponse(
            content='{"intent":"test"}',
            finish_reason="stop",
            usage=Usage(),
            role=MessageRole.ASSISTANT,
        )
        mock = MockLLM(responses=[parse_response, decompose_response])
        agent = PlannerAgent(llm=mock)

        result = await agent.execute({"requirement": "test", "trace_id": "t1"})
        edges = result["dag"]["edges"]
        # n1→n2, n1→n3, n2→n3 共 3 条边
        assert len(edges) == 3
        assert {"from": "n1", "to": "n2"} in edges
        assert {"from": "n1", "to": "n3"} in edges
        assert {"from": "n2", "to": "n3"} in edges

    async def test_dag_cycle_detection_raises(self) -> None:
        """循环依赖应被检测并抛异常。"""
        decompose_response = LLMResponse(
            content=json.dumps(
                {
                    "nodes": [
                        {"node_id": "n1", "title": "A", "description": "a", "depends_on": ["n2"]},
                        {"node_id": "n2", "title": "B", "description": "b", "depends_on": ["n1"]},
                    ]
                }
            ),
            finish_reason="stop",
            usage=Usage(),
            role=MessageRole.ASSISTANT,
        )
        parse_response = LLMResponse(
            content='{"intent":"test"}',
            finish_reason="stop",
            usage=Usage(),
            role=MessageRole.ASSISTANT,
        )
        mock = MockLLM(responses=[parse_response, decompose_response])
        agent = PlannerAgent(llm=mock)

        with pytest.raises(ValueError, match="循环依赖"):
            await agent.execute({"requirement": "test", "trace_id": "t1"})

    async def test_sends_task_assign_to_executor(self) -> None:
        """无依赖的首批子任务通过消息总线通知执行 Agent。"""
        bus = InMemoryMessageBus()
        executor_q = await bus.subscribe("executor")

        parse_response = LLMResponse(
            content='{"intent":"test"}',
            finish_reason="stop",
            usage=Usage(),
            role=MessageRole.ASSISTANT,
        )
        decompose_response = LLMResponse(
            content=json.dumps(
                {
                    "nodes": [
                        {
                            "node_id": "n1",
                            "title": "首任务",
                            "description": "无依赖",
                            "depends_on": [],
                        },
                    ]
                }
            ),
            finish_reason="stop",
            usage=Usage(),
            role=MessageRole.ASSISTANT,
        )
        mock = MockLLM(responses=[parse_response, decompose_response])
        agent = PlannerAgent(llm=mock, message_bus=bus)

        await agent.execute({"requirement": "test", "trace_id": "t1"})

        msg = await __import__("asyncio").wait_for(executor_q.get(), timeout=1.0)
        assert msg.message_type is MessageType.TASK_ASSIGN
        assert msg.to_agent == "executor"
        assert msg.content["node_id"] == "n1"

    async def test_safe_parse_json_handles_dirty_output(self) -> None:
        """LLM 输出含多余文本时仍能提取 JSON。"""
        # _safe_parse_json 是静态方法，直接测试
        result = PlannerAgent._safe_parse_json('一些文字 {"key": "value"} 更多文字')
        assert result == {"key": "value"}

    async def test_safe_parse_json_invalid_returns_empty(self) -> None:
        """完全无法解析时返回空字典。"""
        result = PlannerAgent._safe_parse_json("不是 JSON")
        assert result == {}
