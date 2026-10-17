"""任务事件发布/订阅与 WebSocket 连接票据（Plan 2 Task 3 创建，Task 6 扩展）。

- `EventPublisher`：publish（事务提交后发布）+ subscribe（按 task_id 订阅实时事件）
- `InMemoryEventPublisher`：进程内 fan-out，供测试与无 Redis 环境使用
- `RedisEventPublisher`：Redis Pub/Sub 生产实现
- `WebSocketTicketService`：60 秒单次连接票据（Redis SET NX EX + GETDEL；内存实现用于测试）

安全约定：票据不写入日志；WebSocket 只接受一次性票据，不传递长期 access token。
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from collections.abc import AsyncIterator
from typing import Any, Protocol
from uuid import UUID

from pydantic import ValidationError

from app.core.logging import get_logger
from app.orchestration.contracts import TaskEvent

logger = get_logger(__name__)

# WebSocket 连接票据有效期（秒）
TICKET_TTL_SECONDS = 60

# Redis Pub/Sub 频道前缀
EVENT_CHANNEL_PREFIX = "task-events"


class EventPublisher(Protocol):
    """事件发布协议：事务提交后发布 TaskEvent，并支持按任务订阅。"""

    async def publish(self, event: TaskEvent) -> None: ...

    def subscribe(self, task_id: UUID) -> AsyncIterator[TaskEvent]: ...


class NullEventPublisher:
    """空发布器：不发布、不订阅（离线场景）。"""

    async def publish(self, event: TaskEvent) -> None:
        return None

    async def subscribe(self, task_id: UUID) -> AsyncIterator[TaskEvent]:
        """空订阅：立即结束的异步生成器。"""
        if False:  # pragma: no cover - 仅用于生成器语法
            yield

    @staticmethod
    def channel_for(task_id: UUID) -> str:
        return f"{EVENT_CHANNEL_PREFIX}:{task_id}"


class InMemoryEventPublisher:
    """进程内事件总线：按 task_id 向所有订阅者广播。"""

    def __init__(self) -> None:
        self._subscribers: dict[UUID, set[asyncio.Queue[TaskEvent]]] = {}

    async def publish(self, event: TaskEvent) -> None:
        for queue in list(self._subscribers.get(event.task_id, ())):
            queue.put_nowait(event)

    async def subscribe(self, task_id: UUID) -> AsyncIterator[TaskEvent]:
        queue: asyncio.Queue[TaskEvent] = asyncio.Queue()
        subscribers = self._subscribers.setdefault(task_id, set())
        subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(task_id, None)


class RedisEventPublisher:
    """Redis Pub/Sub 事件发布器（生产实现）。"""

    def __init__(self, client: Any, *, channel_prefix: str = EVENT_CHANNEL_PREFIX) -> None:
        self._client = client
        self._prefix = channel_prefix

    def channel_for(self, task_id: UUID) -> str:
        return f"{self._prefix}:{task_id}"

    async def publish(self, event: TaskEvent) -> None:
        await self._client.publish(self.channel_for(event.task_id), event.model_dump_json())

    async def subscribe(self, task_id: UUID) -> AsyncIterator[TaskEvent]:
        channel = self.channel_for(task_id)
        pubsub = self._client.pubsub()
        await pubsub.subscribe(channel)
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    yield TaskEvent.model_validate_json(message["data"])
                except ValidationError:
                    logger.warning("event_decode_failed", task_id=str(task_id))
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()


class WebSocketTicketService(Protocol):
    """WebSocket 单次连接票据协议。"""

    async def issue(
        self, user_id: UUID, task_id: UUID, *, ttl_seconds: int = TICKET_TTL_SECONDS
    ) -> str: ...

    async def consume(self, ticket: str, task_id: UUID) -> UUID | None: ...


class InMemoryTicketService:
    """内存票据服务：测试与无 Redis 环境使用。"""

    def __init__(self, *, ttl_seconds: int = TICKET_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._tickets: dict[str, tuple[UUID, UUID, float]] = {}

    async def issue(self, user_id: UUID, task_id: UUID, *, ttl_seconds: int | None = None) -> str:
        token = secrets.token_urlsafe(24)
        ttl = ttl_seconds if ttl_seconds is not None else self._ttl
        self._tickets[token] = (user_id, task_id, time.monotonic() + ttl)
        return token

    async def consume(self, ticket: str, task_id: UUID) -> UUID | None:
        entry = self._tickets.pop(ticket, None)
        if entry is None:
            return None
        user_id, bound_task_id, expires_at = entry
        if time.monotonic() > expires_at or bound_task_id != task_id:
            return None
        return user_id


class RedisTicketService:
    """Redis 票据服务：SET key value EX ttl NX + GETDEL 原子消费。"""

    def __init__(
        self,
        client: Any,
        *,
        ttl_seconds: int = TICKET_TTL_SECONDS,
        key_prefix: str = "ws-ticket",
    ) -> None:
        self._client = client
        self._ttl = ttl_seconds
        self._prefix = key_prefix

    def _key(self, ticket: str) -> str:
        return f"{self._prefix}:{ticket}"

    async def issue(self, user_id: UUID, task_id: UUID, *, ttl_seconds: int | None = None) -> str:
        token = secrets.token_urlsafe(24)
        ttl = ttl_seconds if ttl_seconds is not None else self._ttl
        payload = json.dumps({"user_id": str(user_id), "task_id": str(task_id)})
        created = await self._client.set(self._key(token), payload, ex=ttl, nx=True)
        if not created:
            raise RuntimeError("票据生成冲突，请重试")
        return token

    async def consume(self, ticket: str, task_id: UUID) -> UUID | None:
        raw = await self._client.getdel(self._key(ticket))
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if payload.get("task_id") != str(task_id):
            return None
        try:
            return UUID(payload["user_id"])
        except (KeyError, ValueError):
            return None
