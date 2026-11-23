"""定时重跑调度器（Issue #27）。

## 为什么不用 APScheduler / Celery

部署是**单容器单 uvicorn worker**（`backend/entrypoint.sh`），任务本身就是
`asyncio.create_task` 起的进程内协程。为一个「每分钟扫一张小表」的需求引一套
调度框架，会多出 broker、时区、序列化三处不确定性，而收益只是省 60 行代码。

## 怎么保证不重复跑

认领用一条原子 UPDATE：`WHERE enabled AND next_run_at <= now`，认领的同时把
`next_run_at` 推到下一个周期。两个进程（未来横向扩容）同时扫，只有一个能更新成功；
`SELECT ... FOR UPDATE SKIP LOCKED` 在 PostgreSQL 上进一步避免互相等待。

## 停机期间错过的排期不补跑

`next_run_at` 从**当前时刻**往后推，而不是从「本该跑的时刻」推。否则一次两天
停机会在重启瞬间连跑两三次检索 + 两次模型调用，用户看到的是一串重复邮件。
错过就错过，下一期继续。
"""

from __future__ import annotations

import asyncio
import calendar
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.core.logging import get_logger

logger = get_logger(__name__)

#: 认领后启动一次刷新的回调签名：(task_id, user_id) -> None
RefreshStarter = Callable[[uuid.UUID, uuid.UUID], Awaitable[None]]


def _now() -> datetime:
    return datetime.now(UTC)


def advance(cadence: str, base: datetime) -> datetime:
    """按频率推算下一次运行时刻（UTC）。

    月度要处理「31 号」这种不存在的日期：`calendar.monthrange` 夹到当月最后一天，
    否则 3 月 31 日 + 1 个月会抛 `ValueError`，排期就此永久停摆。
    """
    if cadence == "monthly":
        year = base.year + (1 if base.month == 12 else 0)
        month = 1 if base.month == 12 else base.month + 1
        day = min(base.day, calendar.monthrange(year, month)[1])
        return base.replace(year=year, month=month, day=day)
    return base + timedelta(days=7)


class RoutineScheduler:
    """扫表 → 认领 → 启动刷新。"""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Any],
        starter: RefreshStarter,
        config: Any,
        now: Callable[[], datetime] = _now,
    ) -> None:
        self._session_factory = session_factory
        self._starter = starter
        self._config = config
        self._now = now
        self._loop_task: asyncio.Task[None] | None = None

    async def tick(self) -> list[uuid.UUID]:
        """扫描一次；返回本次认领并启动的任务 id。"""
        from app.db.models.routine import TaskRoutine

        moment = self._now()
        claimed: list[tuple[uuid.UUID, uuid.UUID, str]] = []
        async with self._session_factory() as session:
            stmt = (
                select(TaskRoutine)
                .where(TaskRoutine.enabled.is_(True), TaskRoutine.next_run_at <= moment)
                .order_by(TaskRoutine.next_run_at)
                .limit(self._config.batch_size)
                .with_for_update(skip_locked=True)
            )
            routines = list((await session.execute(stmt)).scalars())
            for routine in routines:
                routine.last_run_at = moment
                routine.next_run_at = advance(routine.cadence, moment)
                claimed.append((routine.task_id, routine.user_id, routine.cadence))
            if claimed:
                await session.commit()

        started: list[uuid.UUID] = []
        for task_id, user_id, cadence in claimed:
            try:
                await self._starter(task_id, user_id)
                started.append(task_id)
            except Exception as exc:
                # 单条失败不能让整轮扫描中断，否则后面的排期永远不会被认领
                logger.warning(
                    "routine_start_failed",
                    task_id=str(task_id),
                    cadence=cadence,
                    error=str(exc),
                )
        if started:
            logger.info("routine_started", count=len(started))
        return started

    async def run_forever(self) -> None:
        """常驻协程：每 `tick_seconds` 扫一次，异常只记日志不退出。"""
        logger.info("routine_scheduler_started", tick_seconds=self._config.tick_seconds)
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # 数据库抖动等：下一轮继续
                logger.warning("routine_tick_failed", error=str(exc))
            await asyncio.sleep(self._config.tick_seconds)

    def start(self) -> asyncio.Task[None]:
        """把常驻协程挂到当前事件循环（lifespan 调用）。"""
        self._loop_task = asyncio.create_task(self.run_forever(), name="routine-scheduler")
        return self._loop_task

    async def stop(self) -> None:
        if self._loop_task is None:
            return
        self._loop_task.cancel()
        try:
            await self._loop_task
        except (asyncio.CancelledError, Exception):
            pass
        self._loop_task = None
