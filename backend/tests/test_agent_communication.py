"""Agent 通信层单元测试：InMemoryMessageBus 发布订阅。"""

import asyncio

import pytest

from app.orchestration.communication import InMemoryMessageBus
from app.orchestration.messages import AgentMessage, MessageType


@pytest.mark.asyncio
class TestInMemoryMessageBus:
    """内存消息总线测试。"""

    async def test_publish_subscribe_delivers_message(self) -> None:
        """发布消息后订阅者可收到。"""
        bus = InMemoryMessageBus()
        queue = await bus.subscribe("executor")

        msg = AgentMessage(
            from_agent="planner",
            to_agent="executor",
            message_type=MessageType.TASK_ASSIGN,
            content={"node_id": "n1"},
        )
        await bus.publish(msg)

        received = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert received.from_agent == "planner"
        assert received.content == {"node_id": "n1"}

    async def test_subscribe_idempotent(self) -> None:
        """重复订阅同一 agent_type 返回同一队列。"""
        bus = InMemoryMessageBus()
        q1 = await bus.subscribe("validator")
        q2 = await bus.subscribe("validator")
        assert q1 is q2

    async def test_multiple_agents_isolated(self) -> None:
        """不同 Agent 的消息队列相互隔离。"""
        bus = InMemoryMessageBus()
        executor_q = await bus.subscribe("executor")
        validator_q = await bus.subscribe("validator")

        # 发给 executor 的消息不进入 validator 队列
        await bus.publish(
            AgentMessage(
                from_agent="planner",
                to_agent="executor",
                message_type=MessageType.TASK_ASSIGN,
                content={},
            )
        )
        await bus.publish(
            AgentMessage(
                from_agent="executor",
                to_agent="validator",
                message_type=MessageType.RESULT,
                content={"result": "ok"},
            )
        )

        exec_msg = await asyncio.wait_for(executor_q.get(), timeout=1.0)
        val_msg = await asyncio.wait_for(validator_q.get(), timeout=1.0)
        assert exec_msg.to_agent == "executor"
        assert val_msg.to_agent == "validator"
        assert val_msg.content == {"result": "ok"}

    async def test_close_clears_queues(self) -> None:
        """关闭后队列清空。"""
        bus = InMemoryMessageBus()
        await bus.subscribe("executor")
        await bus.close()
        # 关闭后重新订阅应得到新队列
        new_q = await bus.subscribe("executor")
        assert new_q.empty()
