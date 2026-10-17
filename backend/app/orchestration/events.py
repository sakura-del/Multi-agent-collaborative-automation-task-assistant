"""任务事件发布协议与默认空实现（Plan 2 Task 3 创建，Task 6 在此扩展）。

DatabaseScheduleObserver 只依赖 EventPublisher.publish() 协议；生产环境的
Redis 发布 / WebSocket 订阅在 Plan 2 Task 6 中追加，不改变本协议。
"""

from __future__ import annotations

from typing import Protocol

from app.orchestration.contracts import TaskEvent


class EventPublisher(Protocol):
    """事件发布协议：事务提交后发布 TaskEvent。"""

    async def publish(self, event: TaskEvent) -> None: ...


class NullEventPublisher:
    """空发布器：用于无实时推送需求的测试与离线场景。"""

    async def publish(self, event: TaskEvent) -> None:
        return None
