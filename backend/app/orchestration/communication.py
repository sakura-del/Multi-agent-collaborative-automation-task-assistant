"""Agent 间通信层：基于 Redis Pub/Sub 的异步消息总线。

设计要点：
- 双实现：RedisMessageBus（生产）+ InMemoryMessageBus（测试），同一接口
- 发布/订阅解耦：发送方无需知道接收方实例，仅按 agent_type 定向
- 异步队列投递：订阅者通过 asyncio.Queue 接收，不阻塞事件循环（并行优化）
- 持久化可选：消息可同时写入 AgentInteraction 表，留痕供复盘

线程安全：Pub/Sub 连接与队列均为单 Agent 独占，无需加锁。
"""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from typing import Any

from app.core.logging import get_logger
from app.orchestration.messages import AgentMessage

logger = get_logger(__name__)


class MessageBus(ABC):
    """消息总线抽象接口，定义发布/订阅契约。"""

    @abstractmethod
    async def publish(self, message: AgentMessage) -> None:
        """发布消息到目标 Agent 的 channel。"""

    @abstractmethod
    async def subscribe(self, agent_type: str) -> asyncio.Queue[AgentMessage]:
        """订阅指定 Agent 的消息，返回消息队列。"""

    @abstractmethod
    async def close(self) -> None:
        """关闭总线，释放资源。"""


class InMemoryMessageBus(MessageBus):
    """内存消息总线，用于单元测试与无 Redis 环境。

    每个 agent_type 对应一个队列，发布即入队，订阅者异步消费。
    无网络依赖，测试快速且确定。
    """

    def __init__(self) -> None:
        # 每个 agent_type 一个队列；延迟创建避免预先分配（内存优化）
        self._queues: dict[str, asyncio.Queue[AgentMessage]] = {}

    async def publish(self, message: AgentMessage) -> None:
        queue = self._queues.setdefault(message.to_agent, asyncio.Queue(maxsize=100))
        await queue.put(message)

    async def subscribe(self, agent_type: str) -> asyncio.Queue[AgentMessage]:
        return self._queues.setdefault(agent_type, asyncio.Queue(maxsize=100))

    async def close(self) -> None:
        self._queues.clear()


class RedisMessageBus(MessageBus):
    """基于 Redis Pub/Sub 的消息总线，用于生产环境。

    - publish: 序列化为 JSON 发布到 agent:messages:{to_agent} channel
    - subscribe: 启动后台 task 监听 channel，通过队列投递给 Agent
    - 跨进程通信：多个 Agent 实例可分布在不同进程/机器
    """

    def __init__(self, redis_client: Any | None = None) -> None:
        # 延迟获取 client：避免在无 Redis 环境（如测试）实例化即报错
        self._client = redis_client
        self._pubsub: Any | None = None
        self._queues: dict[str, asyncio.Queue[AgentMessage]] = {}
        self._listeners: dict[str, asyncio.Task[None]] = {}

    def _get_client(self) -> Any:
        if self._client is None:
            from app.core.redis import get_redis

            self._client = get_redis()
        return self._client

    async def publish(self, message: AgentMessage) -> None:
        client = self._get_client()
        payload = message.model_dump_json()
        await client.publish(message.to_channel(), payload)
        logger.debug("bus_publish", **message.to_log_dict())

    async def subscribe(self, agent_type: str) -> asyncio.Queue[AgentMessage]:
        # 幂等：重复订阅同一 agent_type 返回已有队列
        if agent_type in self._queues:
            return self._queues[agent_type]

        queue: asyncio.Queue[AgentMessage] = asyncio.Queue(maxsize=100)
        self._queues[agent_type] = queue

        client = self._get_client()
        pubsub = client.pubsub()
        await pubsub.subscribe(f"agent:messages:{agent_type}")
        # 后台 task 持续读取消息并投递到队列（并行优化：不阻塞订阅者）
        self._listeners[agent_type] = asyncio.create_task(self._listen(agent_type, pubsub, queue))
        return queue

    async def _listen(
        self,
        agent_type: str,
        pubsub: Any,
        queue: asyncio.Queue[AgentMessage],
    ) -> None:
        """后台监听 Redis 消息并反序列化后入队。"""
        try:
            async for raw in pubsub.listen():
                # 跳过订阅确认消息
                if raw.get("type") != "message":
                    continue
                try:
                    data = json.loads(raw["data"])
                    msg = AgentMessage(**data)
                    await queue.put(msg)
                except Exception:
                    logger.error("bus_decode_failed", agent_type=agent_type)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.error("bus_listen_error", agent_type=agent_type)

    async def close(self) -> None:
        # 先取消监听 task，再关闭连接，避免资源泄漏
        for task in self._listeners.values():
            task.cancel()
        for task in self._listeners.values():
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._listeners.clear()
        self._queues.clear()
        if self._pubsub is not None:
            await self._pubsub.close()
            self._pubsub = None
