"""任务列表必须走**单条**汇总 SQL（Supabase 迁移后的性能回归）。

## 背景（2026-11-04 真机实测）

迁移到 Supabase 后 `/tasks` 一次加载耗时 **34 秒**（前端 15s 超时），
而 SQL 在服务端总共只花 44ms —— 慢的全是**网络往返**（每条 SQL ~380ms）。
旧实现对每个任务跑 2 条额外 SQL（subtasks + 时间戳回查），20 个任务
≈ 90 条 SQL。本地库每次 2ms 时这个 N+1 完全隐形。

列表页只需要 6 个字段，必须一条 SQL 拿完；
详情接口（`get_snapshot`）保留原实现不受影响。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models.task import Task
from app.orchestration.contracts import TaskListSummary
from app.orchestration.services.task_execution import TaskExecution


class _NoopContainer:
    """list_tasks 不触碰容器；给个最小替身即可。"""

    session_factory = None


@pytest.fixture
async def factory() -> Any:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    counters = {"select": 0}

    def _count(conn: Any, cursor: Any, statement: str, *args: Any, **kwargs: Any) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            counters["select"] += 1

    event.listen(engine.sync_engine, "before_cursor_execute", _count)
    factory_obj = async_sessionmaker(engine, expire_on_commit=False)
    factory_obj._counters = counters  # type: ignore[attr-defined]
    yield factory_obj
    await engine.dispose()


def _task(user_id: uuid.UUID, i: int) -> Task:
    # created_at 显式给值：同事务批量插入的时间戳相同（AGENTS #14），
    # 而排序键是 (created_at desc, id desc)，UUID 的次序是随机的，不能拿来断言
    return Task(
        user_id=user_id,
        title=f"综述任务 {i}",
        description=f"描述 {i}",
        status="completed",
        plan_version=3,
        created_at=datetime(2026, 11, 1, i % 24, tzinfo=UTC),
        updated_at=datetime(2026, 11, 1, i % 24, tzinfo=UTC),
    )


class TestListTasksSummary:
    async def test_single_select_for_whole_page(self, factory: Any) -> None:
        """整页列表必须只有 **1 条** SELECT（旧实现是 1 + 2×N）。"""
        uid = uuid.uuid4()
        async with factory() as s:
            s.add_all([_task(uid, i) for i in range(5)])
            s.add(_task(uuid.uuid4(), 99))  # 别人的任务：标题相同也会被 owner 过滤掉
            await s.commit()

        svc = TaskExecution(_NoopContainer(), session_factory=factory)
        rows = await svc.list_tasks(uid, workspace_ids=())

        assert len(rows) == 5
        assert factory._counters["select"] == 1  # type: ignore[attr-defined]

    async def test_fields_and_visibility(self, factory: Any) -> None:
        uid = uuid.uuid4()
        async with factory() as s:
            s.add_all([_task(uid, 1), _task(uid, 2)])
            await s.commit()

        svc = TaskExecution(_NoopContainer(), session_factory=factory)
        rows = await svc.list_tasks(uid, workspace_ids=())

        assert all(isinstance(r, TaskListSummary) for r in rows)
        # 倒序（AGENTS #14：排序带 id）
        assert [r.title for r in rows] == ["综述任务 2", "综述任务 1"]
        first = rows[0]
        assert first.task_id and first.status == "completed"
        assert first.plan_version == 3 and first.revision == 3
        assert first.created_at is not None and first.updated_at is not None

    async def test_pagination_and_owner_isolation(self, factory: Any) -> None:
        uid = uuid.uuid4()
        other = uuid.uuid4()
        async with factory() as s:
            s.add_all([_task(uid, i) for i in range(6)])
            s.add_all([_task(other, 100 + i) for i in range(3)])
            await s.commit()

        svc = TaskExecution(_NoopContainer(), session_factory=factory)
        page = await svc.list_tasks(uid, workspace_ids=(), skip=1, limit=2)

        # 6 条按时间倒序是 5,4,3,2,1,0（每小时一条），跳过 1 取 2
        assert [r.title for r in page] == ["综述任务 4", "综述任务 3"]
