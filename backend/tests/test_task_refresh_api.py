"""刷新与排期端点的 HTTP 契约（Issue #27）。

只测「路由 → TaskExecution → 出参」这一层：状态限制、冷却、归属校验都在
`TaskExecution` 里，这里验证它们被正确映射成 4xx/5xx，且出参形状稳定。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.db.models  # noqa: F401
from app.api.v1.endpoints import tasks as tasks_endpoint
from app.core.exceptions import AppException
from app.core.security import create_access_token
from app.db.base import Base
from app.db.models.task import Task
from app.db.models.user import User
from app.db.session import get_db
from app.orchestration.contracts import STATUS_GROUPS  # noqa: F401  确保枚举接线
from app.orchestration.services.task_execution import TaskExecution


class _DummyContainer:
    """只实现 TaskExecution 用到的两件事：session_factory 与 track。"""

    def __init__(self, factory) -> None:
        self.session_factory = factory
        self.tracked: list[Any] = []

    def track(self, task_id, coroutine, *, after_current: bool = False) -> None:
        self.tracked.append(task_id)
        coroutine.close()  # 后台协程由真实运行时执行；测试里不跑，显式关闭


@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


async def _user(factory, name: str = "owner") -> User:
    async with factory() as session:
        user = User(
            username=name,
            email=f"{name}@example.com",
            password_hash="x",
            role="user",
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _task(factory, user: User, *, status: str = "completed") -> Task:
    async with factory() as session:
        task = Task(
            user_id=user.id,
            title="图神经网络综述",
            description="d",
            task_type="literature_review",
            status=status,
            config={"topic": "图神经网络"},
            plan={"search_queries": ["GNN survey"]},
            completed_at=datetime.now(UTC) - timedelta(days=30),
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        return task


def _client(factory, container: _DummyContainer) -> TestClient:
    application = FastAPI()
    application.include_router(tasks_endpoint.router, prefix="/api/v1")
    application.state.runtime = TaskExecution(container)  # type: ignore[arg-type]

    async def _get_db():
        async with factory() as session:
            yield session

    application.dependency_overrides[get_db] = _get_db

    @application.exception_handler(AppException)
    async def _handle(_: Any, exc: AppException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"message": exc.message})

    return TestClient(application)


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


class _Ctx(SimpleNamespace):
    """把测试要用的东西捆一起，避免元组解包顺序出错。"""


@pytest.fixture
def app_client(factory):
    container = _DummyContainer(factory)
    user = asyncio.run(_user(factory))
    task = asyncio.run(_task(factory, user))
    return _Ctx(
        client=_client(factory, container),
        container=container,
        user=user,
        task=task,
        factory=factory,
    )


def test_routine_roundtrip(app_client) -> None:
    client, user, task = app_client.client, app_client.user, app_client.task
    base = f"/api/v1/tasks/{task.id}/routine"

    assert client.get(base, headers=_auth(user)).json() is None

    created = client.put(
        base, headers=_auth(user), json={"enabled": True, "cadence": "monthly", "notify": "email"}
    )
    assert created.status_code == 200
    body = created.json()
    assert body["cadence"] == "monthly"
    assert body["notify"] == "email"
    assert body["enabled"] is True
    assert datetime.fromisoformat(body["next_run_at"]) > datetime.now(UTC)

    assert client.get(base, headers=_auth(user)).json()["cadence"] == "monthly"
    assert client.delete(base, headers=_auth(user)).status_code == 204
    assert client.get(base, headers=_auth(user)).json() is None


def test_routine_requires_completed_task(app_client) -> None:
    client, user, task = app_client.client, app_client.user, app_client.task
    asyncio.run(_set_status(app_client, "running"))
    response = client.put(
        f"/api/v1/tasks/{task.id}/routine",
        headers=_auth(user),
        json={"enabled": True, "cadence": "weekly", "notify": "none"},
    )
    assert response.status_code == 422
    assert "已完成" in response.json()["message"]


def test_refresh_starts_background_run(app_client) -> None:
    client, container, user, task = (
        app_client.client,
        app_client.container,
        app_client.user,
        app_client.task,
    )
    response = client.post(f"/api/v1/tasks/{task.id}/refresh", headers=_auth(user))
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "running"
    assert body["trigger"] == "manual"
    assert container.tracked == [task.id]

    listed = client.get(f"/api/v1/tasks/{task.id}/refreshes", headers=_auth(user)).json()
    assert len(listed) == 1
    assert listed[0]["id"] == body["id"]


def test_refresh_rejects_unfinished_task(app_client) -> None:
    client, container, user, task = (
        app_client.client,
        app_client.container,
        app_client.user,
        app_client.task,
    )
    asyncio.run(_set_status(app_client, "running"))
    response = client.post(f"/api/v1/tasks/{task.id}/refresh", headers=_auth(user))
    assert response.status_code == 422
    assert "已完成" in response.json()["message"]
    assert container.tracked == []


def test_refresh_is_owner_scoped(app_client) -> None:
    stranger = asyncio.run(_user(app_client.factory, "stranger"))
    response = app_client.client.post(
        f"/api/v1/tasks/{app_client.task.id}/refresh", headers=_auth(stranger)
    )
    assert response.status_code == 404


def test_stale_running_refresh_does_not_block_forever(app_client) -> None:
    """进程重启留下的 running 记录不能永久挡住后续刷新。"""

    async def scenario():
        from app.db.models.routine import TaskRefresh

        async with app_client.factory() as session:
            session.add(
                TaskRefresh(
                    task_id=app_client.task.id,
                    user_id=app_client.user.id,
                    trigger="manual",
                    status="running",
                    window_from=datetime.now(UTC) - timedelta(days=30),
                    window_to=datetime.now(UTC),
                    queries=[],
                    started_at=datetime.now(UTC) - timedelta(hours=2),
                )
            )
            await session.commit()

    asyncio.run(scenario())

    response = app_client.client.post(
        f"/api/v1/tasks/{app_client.task.id}/refresh", headers=_auth(app_client.user)
    )
    assert response.status_code == 202

    listed = app_client.client.get(
        f"/api/v1/tasks/{app_client.task.id}/refreshes", headers=_auth(app_client.user)
    ).json()
    statuses = sorted(item["status"] for item in listed)
    assert statuses == ["failed", "running"]


def test_invalid_routine_disables_on_next_tick(app_client) -> None:
    """任务不再是 completed 时，排期应自动停用而不是每周期空跑。"""

    async def scenario():
        from app.db.models.routine import TaskRoutine

        async with app_client.factory() as session:
            session.add(
                TaskRoutine(
                    task_id=app_client.task.id,
                    user_id=app_client.user.id,
                    enabled=True,
                    cadence="weekly",
                    notify="email",
                    next_run_at=datetime.now(UTC) - timedelta(days=1),
                )
            )
            await session.commit()

    asyncio.run(scenario())
    asyncio.run(_set_status(app_client, "failed"))

    execution = app_client.client.app.state.runtime
    asyncio.run(execution.start_scheduled_refresh(app_client.task.id, app_client.user.id))

    async def check():
        from app.db.models.routine import TaskRoutine

        async with app_client.factory() as session:
            row = (
                await session.execute(
                    select(TaskRoutine).where(TaskRoutine.task_id == app_client.task.id)
                )
            ).scalar_one()
            return row.enabled

    assert asyncio.run(check()) is False


async def _set_status(app_client, status: str) -> None:
    task = app_client.task
    async with app_client.factory() as session:
        row = await session.get(Task, task.id)
        row.status = status
        await session.commit()
