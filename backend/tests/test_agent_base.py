"""BaseAgent 抽象基类单元测试：模板方法、状态流转、异常兜底。"""

import pytest

from app.orchestration.base_agent import BaseAgent
from app.orchestration.communication import InMemoryMessageBus
from app.orchestration.messages import AgentMessage, MessageType
from app.orchestration.state import AgentStatus


class _TestAgentOK(BaseAgent):
    """测试用 Agent：正常执行。"""

    @property
    def agent_type(self) -> str:
        return "test"

    async def _run(self, input_data: dict) -> dict:
        return {"echo": input_data.get("value", "")}


class _TestAgentFail(BaseAgent):
    """测试用 Agent：执行抛异常。"""

    @property
    def agent_type(self) -> str:
        return "test_fail"

    async def _run(self, input_data: dict) -> dict:
        raise RuntimeError("模拟执行失败")


@pytest.mark.asyncio
class TestBaseAgentExecute:
    """execute() 模板方法测试。"""

    async def test_execute_success_returns_result(self) -> None:
        """成功执行返回 _run 结果。"""
        agent = _TestAgentOK()
        result = await agent.execute({"value": "hello", "trace_id": "t1"})
        assert result == {"echo": "hello"}

    async def test_execute_transitions_to_idle_after_success(self) -> None:
        """成功后状态回到 IDLE。"""
        agent = _TestAgentOK()
        await agent.execute({"trace_id": "t1"})
        assert agent.status is AgentStatus.IDLE

    async def test_execute_failure_raises_and_recovers(self) -> None:
        """失败抛异常后状态恢复到 IDLE。"""
        agent = _TestAgentFail()
        with pytest.raises(RuntimeError, match="模拟执行失败"):
            await agent.execute({"trace_id": "t1"})
        # 异常后恢复到 IDLE，允许重试
        assert agent.status is AgentStatus.IDLE

    async def test_execute_records_duration(self) -> None:
        """执行后记录耗时。"""
        agent = _TestAgentOK()
        await agent.execute({"trace_id": "t1"})
        assert agent._last_duration is not None
        assert agent._last_duration >= 0

    async def test_execute_increments_exec_count(self) -> None:
        """执行计数递增。"""
        agent = _TestAgentOK()
        await agent.execute({"trace_id": "t1"})
        await agent.execute({"trace_id": "t2"})
        assert agent._exec_count == 2


@pytest.mark.asyncio
class TestBaseAgentStatus:
    """get_status() 测试。"""

    async def test_get_status_snapshot(self) -> None:
        """状态快照包含完整信息。"""
        agent = _TestAgentOK(agent_id="agent-001")
        await agent.execute({"trace_id": "t1"})
        status = agent.get_status()
        assert status["agent_id"] == "agent-001"
        assert status["agent_type"] == "test"
        assert status["status"] == "idle"
        assert status["exec_count"] == 1
        assert status["last_duration_s"] is not None


@pytest.mark.asyncio
class TestBaseAgentMessage:
    """handle_message() 测试。"""

    async def test_handle_error_message_logged(self) -> None:
        """ERROR 消息被记录但不触发处理。"""
        agent = _TestAgentOK()
        msg = AgentMessage(
            from_agent="executor",
            to_agent="test",
            message_type=MessageType.ERROR,
            content={"error": "boom"},
        )
        result = await agent.handle_message(msg)
        assert result is None

    async def test_handle_message_delegates_to_subclass(self) -> None:
        """非 ERROR 消息交给子类 _handle_message。"""

        class _AgentWithHandler(_TestAgentOK):
            async def _handle_message(self, message: AgentMessage) -> dict:
                return {"handled": message.content}

        agent = _AgentWithHandler()
        msg = AgentMessage(
            from_agent="planner",
            to_agent="test",
            message_type=MessageType.TASK_ASSIGN,
            content={"task": "do_something"},
        )
        result = await agent.handle_message(msg)
        assert result == {"handled": {"task": "do_something"}}


@pytest.mark.asyncio
class TestBaseAgentCommunication:
    """消息总线集成测试。"""

    async def test_send_message_via_bus(self) -> None:
        """通过消息总线发送消息可被订阅者接收。"""
        bus = InMemoryMessageBus()
        agent = _TestAgentOK(message_bus=bus)
        reviewer_q = await bus.subscribe("reviewer")

        await agent._send_message(
            "reviewer",
            MessageType.RESULT,
            {"data": "test"},
            trace_id="t1",
        )
        msg = await __import__("asyncio").wait_for(reviewer_q.get(), timeout=1.0)
        assert msg.from_agent == "test"
        assert msg.content == {"data": "test"}

    async def test_send_message_without_bus_does_not_crash(self) -> None:
        """无消息总线时发送不崩溃（仅记日志）。"""
        agent = _TestAgentOK()  # 无 message_bus
        await agent._send_message("reviewer", MessageType.RESULT, {"data": "test"})  # 不应抛异常

    async def test_failure_broadcasts_error_to_reviewer(self) -> None:
        """执行失败时向 reviewer 广播 ERROR 消息。"""
        bus = InMemoryMessageBus()
        agent = _TestAgentFail(message_bus=bus)
        reviewer_q = await bus.subscribe("reviewer")

        with pytest.raises(RuntimeError):
            await agent.execute({"trace_id": "trace-xyz"})

        msg = await __import__("asyncio").wait_for(reviewer_q.get(), timeout=1.0)
        assert msg.message_type is MessageType.ERROR
        assert msg.to_agent == "reviewer"
        assert msg.content["agent_type"] == "test_fail"
        assert "模拟执行失败" in msg.content["error"]
