"""开放端点全覆盖测试（Phase 2 · 开放 API Task 3 的真实缺陷回归）。

## 这个文件为什么存在

首版只把 `current_user` 参数换成了双通道依赖，**漏掉了间接依赖**：
`get_visible_workspace_ids` 内部还写着 `Depends(get_current_active_user)`（JWT only）。

结果：`POST /tasks`（无此依赖）用 Key 返回 201，而 `GET /tasks`（有此依赖）
返回 401 —— **同一个 Key，一半端点能用一半不能用**。服务器验收时才发现。

教训：改共用鉴权依赖时，不能只看「端点签名里那一行」，
必须**逐个端点用 Key 实测**。本文件就是那个「逐个实测」。

## 覆盖范围

开放 API 全部 11 个端点（10 个任务相关 + 批注写）。
每个都断言「用 Key 不是 401」——401 意味着鉴权链里有 JWT-only 依赖混进来了。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

import app.db.models  # noqa: F401
from app.api.v1.endpoints import annotations as annotations_endpoint
from app.api.v1.endpoints import tasks as tasks_endpoint
from app.core.exceptions import AppException
from app.db.base import Base
from app.db.models.task import Task
from app.db.models.user import User
from app.db.session import get_db
from app.security.api_key_service import ApiKeyService


@pytest.fixture
def open_app() -> Any:
    """最小 app：挂 tasks + annotations 路由，真实 Key 鉴权。"""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _create_all() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    asyncio.run(_create_all())

    async def _get_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    # 用假的 execution 避免真的跑编排。
    # ⚠️ 方法名必须与端点实际调用的**完全一致**（`grep 'execution\.' tasks.py` 核对），
    # 否则会在 401 之外的地方报 AttributeError，掩盖真正的鉴权断言。
    class _FakeExecution:
        async def list_tasks(self, user_id, *, skip=0, limit=20, workspace_ids=()):  # type: ignore[no-untyped-def]
            return []

        async def get_snapshot(self, user_id, task_id, *, workspace_ids=()):  # type: ignore[no-untyped-def]
            from app.core.exceptions import NotFoundException

            raise NotFoundException("任务不存在")

        async def events_after(self, user_id, task_id, event_id):  # type: ignore[no-untyped-def]
            return []

        async def resolve_artifact(self, user_id, task_id, artifact_id):  # type: ignore[no-untyped-def]
            from app.core.exceptions import NotFoundException

            raise NotFoundException("产出不存在")

        async def export_references(self, user_id, task_id, *, fmt):  # type: ignore[no-untyped-def]
            from app.core.exceptions import NotFoundException

            raise NotFoundException("任务不存在")

        async def submit(self, user_id, request):  # type: ignore[no-untyped-def]
            return uuid.uuid4()

        async def approve_plan(self, user_id, task_id, revision):  # type: ignore[no-untyped-def]
            from app.core.exceptions import NotFoundException

            raise NotFoundException("任务不存在")

        async def control(self, user_id, task_id, request):  # type: ignore[no-untyped-def]
            from app.core.exceptions import NotFoundException

            raise NotFoundException("任务不存在")

    application = FastAPI()
    application.include_router(tasks_endpoint.router, prefix="/api/v1")
    application.include_router(annotations_endpoint.router, prefix="/api/v1")

    @application.exception_handler(AppException)
    async def _handle(_: Any, exc: AppException) -> Any:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error_code": exc.error_code, "message": exc.message},
        )

    application.dependency_overrides[get_db] = _get_db
    application.dependency_overrides[tasks_endpoint.get_task_execution] = lambda: _FakeExecution()
    application.state.factory = factory
    application.state.client = TestClient(application)
    return application


async def _make_user(factory, name: str) -> User:
    async with factory() as session:
        user = User(username=name, email=f"{name}@example.com", password_hash="x", is_active=True)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


def _setup(open_app, name: str, scopes: list[str] | None = None) -> tuple[User, str]:
    factory = open_app.state.factory

    async def _run() -> tuple[User, str]:
        user = await _make_user(factory, name)
        service = ApiKeyService(factory)
        _, plaintext = await service.create(
            user.id,
            name="test",
            scopes=scopes or ["tasks:read", "tasks:write", "artifacts:read", "annotations:write"],
        )
        return user, plaintext

    return asyncio.run(_run())


def _make_task(open_app, user: User) -> uuid.UUID:
    factory = open_app.state.factory

    async def _run() -> uuid.UUID:
        async with factory() as session:
            task = Task(
                user_id=user.id,
                title="T",
                description="d",
                status="completed",
                final_output="正文",
            )
            session.add(task)
            await session.commit()
            await session.refresh(task)
            return task.id

    return asyncio.run(_run())


class TestAllOpenEndpointsAcceptKey:
    """**核心断言**：每个开放端点用 Key 都不能返回 401。"""

    def test_list_tasks(self, open_app) -> None:
        _, key = _setup(open_app, "e_list")

        resp = open_app.state.client.get("/api/v1/tasks", headers={"X-API-Key": key})

        assert resp.status_code != 401, f"用 Key 列任务被拒：{resp.text}"

    def test_get_task(self, open_app) -> None:
        user, key = _setup(open_app, "e_get")
        task_id = _make_task(open_app, user)

        resp = open_app.state.client.get(f"/api/v1/tasks/{task_id}", headers={"X-API-Key": key})

        assert resp.status_code != 401, f"用 Key 查任务被拒：{resp.text}"

    def test_create_task(self, open_app) -> None:
        _, key = _setup(open_app, "e_create")

        resp = open_app.state.client.post(
            "/api/v1/tasks",
            headers={"X-API-Key": key},
            json={"topic": "检索增强生成综述", "keywords": ["RAG"], "max_documents": 2},
        )

        assert resp.status_code != 401, f"用 Key 建任务被拒：{resp.text}"

    def test_approve_plan(self, open_app) -> None:
        user, key = _setup(open_app, "e_approve")
        task_id = _make_task(open_app, user)

        resp = open_app.state.client.post(
            f"/api/v1/tasks/{task_id}/plan/approve",
            headers={"X-API-Key": key},
            json={"revision": 1},
        )

        assert resp.status_code != 401, f"用 Key 确认规划被拒：{resp.text}"

    def test_control(self, open_app) -> None:
        user, key = _setup(open_app, "e_control")
        task_id = _make_task(open_app, user)

        resp = open_app.state.client.post(
            f"/api/v1/tasks/{task_id}/control",
            headers={"X-API-Key": key},
            json={"command": "cancel"},
        )

        assert resp.status_code != 401, f"用 Key 控制任务被拒：{resp.text}"

    def test_list_events(self, open_app) -> None:
        user, key = _setup(open_app, "e_events")
        task_id = _make_task(open_app, user)

        resp = open_app.state.client.get(
            f"/api/v1/tasks/{task_id}/events", headers={"X-API-Key": key}
        )

        assert resp.status_code != 401, f"用 Key 查事件被拒：{resp.text}"

    def test_download_artifact(self, open_app) -> None:
        user, key = _setup(open_app, "e_artifact")
        task_id = _make_task(open_app, user)

        resp = open_app.state.client.get(
            f"/api/v1/tasks/{task_id}/artifacts/a1", headers={"X-API-Key": key}
        )

        assert resp.status_code != 401, f"用 Key 下载产物被拒：{resp.text}"

    def test_download_bib(self, open_app) -> None:
        user, key = _setup(open_app, "e_bib")
        task_id = _make_task(open_app, user)

        resp = open_app.state.client.get(
            f"/api/v1/tasks/{task_id}/references.bib", headers={"X-API-Key": key}
        )

        assert resp.status_code != 401, f"用 Key 导出 bib 被拒：{resp.text}"

    def test_download_ris(self, open_app) -> None:
        user, key = _setup(open_app, "e_ris")
        task_id = _make_task(open_app, user)

        resp = open_app.state.client.get(
            f"/api/v1/tasks/{task_id}/references.ris", headers={"X-API-Key": key}
        )

        assert resp.status_code != 401, f"用 Key 导出 ris 被拒：{resp.text}"

    def test_list_annotations(self, open_app) -> None:
        user, key = _setup(open_app, "e_an_list")
        task_id = _make_task(open_app, user)

        resp = open_app.state.client.get(
            f"/api/v1/tasks/{task_id}/annotations", headers={"X-API-Key": key}
        )

        assert resp.status_code != 401, f"用 Key 列批注被拒：{resp.text}"

    def test_create_annotation(self, open_app) -> None:
        user, key = _setup(open_app, "e_an_create")
        task_id = _make_task(open_app, user)

        resp = open_app.state.client.post(
            f"/api/v1/tasks/{task_id}/annotations",
            headers={"X-API-Key": key},
            json={"anchor_type": "task", "content": "导师批注"},
        )

        assert resp.status_code != 401, f"用 Key 写批注被拒：{resp.text}"


class TestScopeOnAnnotations:
    def test_missing_annotations_scope_returns_403(self, open_app) -> None:
        """只给 tasks:read 的 Key 不能写批注（403，不是 401）。"""
        user, key = _setup(open_app, "e_an_scope", scopes=["tasks:read"])
        task_id = _make_task(open_app, user)

        resp = open_app.state.client.post(
            f"/api/v1/tasks/{task_id}/annotations",
            headers={"X-API-Key": key},
            json={"anchor_type": "task", "content": "x"},
        )

        assert resp.status_code == 403, resp.text


class TestJwtStillWorksOnOpenEndpoints:
    """改共用依赖后 JWT 必须照旧可用（回归保护）。"""

    def test_jwt_list_tasks(self, open_app) -> None:
        from app.core.security import create_access_token

        user = asyncio.run(_make_user(open_app.state.factory, "e_jwt_list"))
        token = create_access_token(str(user.id))

        resp = open_app.state.client.get(
            "/api/v1/tasks", headers={"Authorization": f"Bearer {token}"}
        )

        assert resp.status_code == 200, resp.text

    def test_jwt_workspace_ids_dependency_not_blocking(self, open_app) -> None:
        """**这条是本文件的核心**：`get_visible_workspace_ids` 曾用 JWT-only 依赖，
        导致 Key 调 `GET /tasks` 401 而 `POST /tasks` 201。
        JWT 路径本来就通，所以这里断言 JWT 仍然 200（不能被改坏）。
        """
        from app.core.security import create_access_token

        user = asyncio.run(_make_user(open_app.state.factory, "e_jwt_ws"))
        token = create_access_token(str(user.id))

        resp = open_app.state.client.get(
            "/api/v1/tasks", headers={"Authorization": f"Bearer {token}"}
        )

        assert resp.status_code == 200
        assert resp.json() == []
