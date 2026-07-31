"""执行 Agent 单元测试：LLM 推理、结果上报、工具注册。"""

import asyncio

import pytest

from app.llm.models import LLMResponse, MessageRole, Usage
from app.orchestration.agents.executor import ExecutorAgent
from app.orchestration.communication import InMemoryMessageBus
from app.orchestration.messages import MessageType
from tests.llm.mock_llm import MockLLM


@pytest.mark.asyncio
class TestExecutorAgent:
    """执行 Agent 测试。"""

    async def test_run_without_llm_degrades(self) -> None:
        """无 LLM 时降级输出占位结果。"""
        agent = ExecutorAgent()
        result = await agent.execute(
            {"node_id": "n1", "title": "测试任务", "description": "描述", "trace_id": "t1"}
        )
        assert result["node_id"] == "n1"
        assert result["status"] == "completed"
        assert "降级输出" in result["result"]

    async def test_agent_type_is_executor(self) -> None:
        assert ExecutorAgent().agent_type == "executor"

    async def test_run_with_llm_returns_content(self) -> None:
        """Mock LLM 返回推理结果。"""
        response = LLMResponse(
            content="这是执行结果",
            finish_reason="stop",
            usage=Usage(),
            role=MessageRole.ASSISTANT,
        )
        mock = MockLLM(responses=[response])
        agent = ExecutorAgent(llm=mock)

        result = await agent.execute(
            {"node_id": "n1", "title": "分析数据", "description": "分析销售数据", "trace_id": "t1"}
        )
        assert result["result"] == "这是执行结果"
        assert result["used_tools"] == []

    async def test_sends_result_to_validator(self) -> None:
        """执行结果通过消息总线通知校验 Agent。"""
        bus = InMemoryMessageBus()
        validator_q = await bus.subscribe("validator")

        response = LLMResponse(
            content="结果内容",
            finish_reason="stop",
            usage=Usage(),
            role=MessageRole.ASSISTANT,
        )
        mock = MockLLM(responses=[response])
        agent = ExecutorAgent(llm=mock, message_bus=bus)

        await agent.execute(
            {"node_id": "n1", "title": "任务", "description": "描述", "trace_id": "t1"}
        )

        msg = await asyncio.wait_for(validator_q.get(), timeout=1.0)
        assert msg.message_type is MessageType.RESULT
        assert msg.to_agent == "validator"
        assert msg.content["result"] == "结果内容"

    async def test_register_tool(self) -> None:
        """工具注册后存入 _available_tools。"""
        from app.llm.models import ToolSchema

        agent = ExecutorAgent()
        tool = ToolSchema(
            name="search",
            description="搜索工具",
            parameters={"type": "object", "properties": {}},
        )
        agent.register_tool(tool)
        assert len(agent._available_tools) == 1
        assert agent._available_tools[0].name == "search"

    async def test_infer_with_tool_calls(self) -> None:
        """LLM 返回工具调用时正确提取。"""
        from app.llm.models import ToolCall

        response = LLMResponse(
            content="调用了工具",
            tool_calls=[ToolCall(id="tc1", name="search", arguments='{"q":"test"}')],
            finish_reason="tool_calls",
            usage=Usage(),
            role=MessageRole.ASSISTANT,
        )
        mock = MockLLM(responses=[response])
        agent = ExecutorAgent(llm=mock)

        result = await agent.execute(
            {"node_id": "n1", "title": "搜索", "description": "搜索测试", "trace_id": "t1"}
        )
        assert result["used_tools"] == ["search"]
