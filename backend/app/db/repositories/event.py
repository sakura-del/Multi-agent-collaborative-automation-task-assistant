"""任务事件 Repository：任务内递增事件序号与 owner-scoped 回放（Plan 2 Task 2）。

设计要点：
- append() 先锁父任务行，再取 max(event_seq)+1，保证并发下序号唯一且连续；
- 只 flush 不 commit，由调用方（DatabaseScheduleObserver / TaskExecution）控制事务；
- 事件对外契约是 TaskEvent；status 存入 ExecutionLog.context 的保留键 "status"，
  payload 其余键原样回放，避免新增数据库列。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select

from app.db.models.log import ExecutionLog
from app.db.models.task import Task
from app.db.repositories.base import BaseRepository
from app.orchestration.contracts import TaskEvent

# 单次回放事件数上限，避免一次拉取过多
MAX_EVENT_LIMIT = 500

# ExecutionLog.context 中保存事件状态（TaskEvent.status）的保留键
_STATUS_CONTEXT_KEY = "status"


class EventRepository(BaseRepository[ExecutionLog]):
    """ExecutionLog 的任务事件写入与回放。"""

    model = ExecutionLog

    async def append(
        self,
        task_id: UUID,
        *,
        event_type: str,
        status: str,
        subtask_id: UUID | None = None,
        attempt: int = 0,
        payload: dict[str, Any] | None = None,
    ) -> TaskEvent:
        """追加一条任务事件并返回对外契约 TaskEvent。

        先对父任务行加锁（PostgreSQL 下 FOR UPDATE），再分配任务内递增序号；
        调用方负责 commit。事件与状态转换应在同一事务内提交。
        """
        locked = await self.db.execute(select(Task.id).where(Task.id == task_id).with_for_update())
        if locked.scalar_one_or_none() is None:
            raise LookupError(f"任务不存在: {task_id}")

        max_seq = await self.db.scalar(
            select(func.max(ExecutionLog.event_seq)).where(ExecutionLog.task_id == task_id)
        )
        event_seq = int(max_seq or 0) + 1
        occurred_at = datetime.now(UTC)
        body = dict(payload or {})
        context = {**body, _STATUS_CONTEXT_KEY: status}

        log = ExecutionLog(
            task_id=task_id,
            subtask_id=subtask_id,
            level="info",
            message=event_type,
            context=context,
            event_type=event_type,
            event_seq=event_seq,
            attempt=attempt,
            logged_at=occurred_at,
        )
        self.db.add(log)
        await self.db.flush()

        return TaskEvent(
            event_id=event_seq,
            task_id=task_id,
            subtask_id=subtask_id,
            event_type=event_type,
            status=status,
            occurred_at=occurred_at,
            payload=body,
        )

    async def events_after(
        self,
        user_id: UUID,
        task_id: UUID,
        after: int,
        limit: int = MAX_EVENT_LIMIT,
    ) -> list[ExecutionLog]:
        """按 event_seq 升序回放某个任务在 after 之后的事件（owner-scoped）。"""
        statement = (
            select(ExecutionLog)
            .join(Task, Task.id == ExecutionLog.task_id)
            .where(
                Task.user_id == user_id,
                Task.id == task_id,
                ExecutionLog.event_seq > after,
            )
            .order_by(ExecutionLog.event_seq)
            .limit(max(1, min(limit, MAX_EVENT_LIMIT)))
        )
        result = await self.db.execute(statement)
        return list(result.scalars().all())

    @staticmethod
    def to_event(log: ExecutionLog) -> TaskEvent:
        """把持久化日志转换为对外 TaskEvent（payload 去掉保留的 status 键）。"""
        context = dict(log.context or {})
        status = str(context.pop(_STATUS_CONTEXT_KEY, ""))
        return TaskEvent(
            event_id=log.event_seq,
            task_id=log.task_id,
            subtask_id=log.subtask_id,
            event_type=log.event_type,
            status=status,
            occurred_at=log.logged_at,
            payload=context,
        )
