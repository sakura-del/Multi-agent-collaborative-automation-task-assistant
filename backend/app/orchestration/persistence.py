"""调度状态数据库观察者（Plan 2 Task 3）。

把调度器每次子任务状态转换与节点最终结果持久化为事件：
1. 回调内打开独立 AsyncSession（不共享调度器/请求的事务）；
2. 按 (task_id, node_id) 锁定 SUBTASK 行并更新状态、时间戳、重试与输出；
3. 通过 EventRepository.append() 分配任务内递增 event_seq 并写入 EXECUTION_LOG；
4. commit 成功后调用 EventPublisher.publish()。

失败策略：
- 数据库操作失败：回滚并记录日志，不向调度器抛异常（避免持久化问题中断执行）；
- 事件发布失败：数据库转换已提交，仅记录警告（Redis 抖动不影响事实来源）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.db.models.task import Subtask
from app.db.repositories.event import EventRepository
from app.db.session import AsyncSessionLocal
from app.orchestration.contracts import TaskEvent
from app.orchestration.events import EventPublisher, NullEventPublisher
from app.orchestration.scheduler import NodeResult
from app.orchestration.task_state import SubtaskStatus

logger = get_logger(__name__)

# 需要写入 completed_at 的终态集合
_TERMINAL_STATUSES = frozenset(
    {
        SubtaskStatus.COMPLETED,
        SubtaskStatus.FAILED,
        SubtaskStatus.TERMINATED,
        SubtaskStatus.CANCELLED,
    }
)


def _parse_task_id(task_id: str) -> UUID | None:
    """把调度器传入的字符串 task_id 解析为 UUID；非法时返回 None。"""
    try:
        return UUID(str(task_id))
    except (ValueError, AttributeError, TypeError):
        logger.warning("schedule_observer_invalid_task_id", task_id=task_id)
        return None


class DatabaseScheduleObserver:
    """把调度状态变更写入数据库，并在提交后发布事件。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] = AsyncSessionLocal,
        publisher: EventPublisher | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._publisher: EventPublisher = publisher or NullEventPublisher()

    async def _load_subtask(
        self, session: AsyncSession, task_id: UUID, node_id: str
    ) -> Subtask | None:
        """按 (task_id, node_id) 锁定子任务行。"""
        result = await session.execute(
            select(Subtask)
            .where(Subtask.task_id == task_id, Subtask.node_id == node_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def on_transition(
        self,
        task_id: str,
        node_id: str,
        old: SubtaskStatus,
        new: SubtaskStatus,
    ) -> None:
        """持久化一次子任务状态转换。"""
        parsed = _parse_task_id(task_id)
        if parsed is None:
            return

        event: TaskEvent | None = None
        try:
            async with self._session_factory() as session:
                subtask = await self._load_subtask(session, parsed, node_id)
                if subtask is None:
                    logger.warning(
                        "schedule_transition_subtask_missing",
                        task_id=task_id,
                        node_id=node_id,
                    )
                    return

                now = datetime.now(UTC)
                subtask.status = new.value
                if new is SubtaskStatus.RUNNING and subtask.started_at is None:
                    subtask.started_at = now
                if new in _TERMINAL_STATUSES and subtask.completed_at is None:
                    subtask.completed_at = now

                event = await EventRepository(session).append(
                    parsed,
                    event_type="node_status",
                    status=new.value,
                    subtask_id=subtask.id,
                    payload={"node_id": node_id, "from": old.value, "to": new.value},
                )
                await session.commit()
        except Exception:
            logger.exception(
                "schedule_transition_persist_failed",
                task_id=task_id,
                node_id=node_id,
            )
            return

        await self._publish(event)

    async def on_result(self, task_id: str, result: NodeResult) -> None:
        """持久化节点最终结果（重试次数 / 输出 / 校验）并追加结果事件。"""
        parsed = _parse_task_id(task_id)
        if parsed is None:
            return

        event: TaskEvent | None = None
        try:
            async with self._session_factory() as session:
                subtask = await self._load_subtask(session, parsed, result.node_id)
                if subtask is None:
                    logger.warning(
                        "schedule_result_subtask_missing",
                        task_id=task_id,
                        node_id=result.node_id,
                    )
                    return

                subtask.retry_count = max(0, result.attempts - 1)
                subtask.output_data = {
                    "output": result.output,
                    "validation": result.validation,
                    "error": result.error,
                }
                now = datetime.now(UTC)
                subtask.status = result.status.value
                if result.status is SubtaskStatus.RUNNING and subtask.started_at is None:
                    subtask.started_at = now
                if result.status in _TERMINAL_STATUSES and subtask.completed_at is None:
                    subtask.completed_at = now

                event = await EventRepository(session).append(
                    parsed,
                    event_type="node_result",
                    status=result.status.value,
                    subtask_id=subtask.id,
                    attempt=subtask.retry_count,
                    payload={
                        "node_id": result.node_id,
                        "attempts": result.attempts,
                        "error": result.error,
                    },
                )
                await session.commit()
        except Exception:
            logger.exception(
                "schedule_result_persist_failed",
                task_id=task_id,
                node_id=result.node_id,
            )
            return

        await self._publish(event)

    async def _publish(self, event: TaskEvent | None) -> None:
        """提交成功后发布事件；发布失败只记警告。"""
        if event is None:
            return
        try:
            await self._publisher.publish(event)
        except Exception:
            logger.warning(
                "schedule_event_publish_failed",
                event_id=event.event_id,
                event_type=event.event_type,
            )
