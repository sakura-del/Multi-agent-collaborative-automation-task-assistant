"""套餐功能门禁测试（Phase 2 · 计费套餐 Task 4）。

## 本文件锁定三条门禁

| 门禁 | 套餐字段 | 触发点 |
| --- | --- | --- |
| **团队工作区** | `allow_team` | 建工作区 / 接受邀请 |
| **工作区数量** | `max_workspaces` | 建工作区 |
| **席位上限** | `max_seats_per_workspace` | 建工作区时确定 `seat_limit` |
| **开放 API** | `allow_api_access` | 用 Key 调开放端点 |

## 为什么 API 访问要绑套餐

免费用户用脚本刷任务的成本风险远高于网页（脚本可以无限快）。
这是行业惯例（Rayyan / Covidence 的 API 都是付费档）。

## 为什么席位上限由套餐决定而不是用户随意填

席位 = 钱。若用户可以自填 200 席位，机构版就卖不出去。
`workspace.seat_limit` 的**上限**必须由套餐约束。
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
from app.api.v1.endpoints import tasks as tasks_endpoint
from app.api.v1.endpoints import workspaces as workspaces_endpoint
from app.billing.services.plan_service import PlanService
from app.core.exceptions import AppException
from app.core.security import create_access_token
from app.db.base import Base
from app.db.models.billing import SEED_PLANS, Plan
from app.db.models.user import User
from app.db.session import get_db


@pytest.fixture
def env() -> Any:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _setup() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            for spec in SEED_PLANS:
                session.add(Plan(**spec, is_active=True))
            await session.commit()

    asyncio.run(_setup())

    async def _get_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    application = FastAPI()
    application.include_router(workspaces_endpoint.router, prefix="/api/v1")
    application.include_router(tasks_endpoint.router, prefix="/api/v1")

    @application.exception_handler(AppException)
    async def _handle(_: Any, exc: AppException) -> Any:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error_code": exc.error_code, "message": exc.message},
        )

    application.dependency_overrides[get_db] = _get_db

    # 任务端点需要 execution；本文件只关心**门禁**，用空实现即可
    class _FakeExecution:
        async def list_tasks(self, user_id, *, skip=0, limit=20, workspace_ids=()):  # type: ignore[no-untyped-def]
            return []

    application.dependency_overrides[tasks_endpoint.get_task_execution] = lambda: _FakeExecution()
    application.state.factory = factory
    application.state.plan_service = PlanService(factory)
    application.state.client = TestClient(application)
    return application


async def _make_user(factory, name: str) -> User:
    async with factory() as session:
        user = User(username=name, email=f"{name}@example.com", password_hash="x", is_active=True)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


def _user(env, name: str) -> User:
    return asyncio.run(_make_user(env.state.factory, name))


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


def _subscribe(env, user: User, plan_id: str) -> None:
    asyncio.run(PlanService(env.state.factory).subscribe(user.id, plan_id, note="测试"))


def _workspace_payload(name: str, seat_limit: int = 3) -> dict[str, Any]:
    # `slug` 是必填（团队工作区交付时引入的 slug 唯一性校验）
    slug = "".join(ch for ch in name if ch.isascii() and ch.isalnum()) or "ws"
    return {"name": name, "slug": f"{slug}-{uuid.uuid4().hex[:8]}", "seat_limit": seat_limit}


class TestTeamGate:
    def test_free_cannot_create_workspace(self, env) -> None:
        """免费档不能建工作区（**403 + PLAN_FEATURE_UNAVAILABLE**）。"""
        user = _user(env, "tg1")

        resp = env.state.client.post(
            "/api/v1/workspaces", headers=_headers(user), json=_workspace_payload("免费团队")
        )

        assert resp.status_code == 403, resp.text
        assert resp.json()["error_code"] == "PLAN_FEATURE_UNAVAILABLE"

    def test_student_can_create_workspace(self, env) -> None:
        user = _user(env, "tg2")
        _subscribe(env, user, "student")

        resp = env.state.client.post(
            "/api/v1/workspaces", headers=_headers(user), json=_workspace_payload("学生团队")
        )

        assert resp.status_code == 201, resp.text


class TestWorkspaceCountLimit:
    def test_student_limited_to_one_workspace(self, env) -> None:
        """学生档 `max_workspaces=1`：第 2 个必须被拒。"""
        user = _user(env, "wc1")
        _subscribe(env, user, "student")
        client = env.state.client
        first = client.post(
            "/api/v1/workspaces", headers=_headers(user), json=_workspace_payload("第一个")
        )
        assert first.status_code == 201, first.text

        second = client.post(
            "/api/v1/workspaces", headers=_headers(user), json=_workspace_payload("第二个")
        )

        assert second.status_code == 403, second.text
        assert second.json()["error_code"] == "PLAN_WORKSPACE_LIMIT"

    def test_pro_allows_more(self, env) -> None:
        user = _user(env, "wc2")
        _subscribe(env, user, "pro")
        client = env.state.client
        for index in range(2):
            resp = client.post(
                "/api/v1/workspaces",
                headers=_headers(user),
                json=_workspace_payload(f"专业团队{index}"),
            )
            assert resp.status_code == 201, resp.text


class TestSeatLimitCap:
    def test_seat_limit_capped_by_plan(self, env) -> None:
        """学生档最多 3 席：请求 10 席必须被拒（**不能静默截断**）。"""
        user = _user(env, "sc1")
        _subscribe(env, user, "student")

        resp = env.state.client.post(
            "/api/v1/workspaces",
            headers=_headers(user),
            json=_workspace_payload("超席团队", seat_limit=10),
        )

        assert resp.status_code == 403, resp.text
        body = resp.json()
        assert body["error_code"] == "PLAN_SEAT_LIMIT"

    def test_seat_limit_within_cap_allowed(self, env) -> None:
        user = _user(env, "sc2")
        _subscribe(env, user, "student")

        resp = env.state.client.post(
            "/api/v1/workspaces",
            headers=_headers(user),
            json=_workspace_payload("合规团队", seat_limit=3),
        )

        assert resp.status_code == 201, resp.text


class TestApiAccessGate:
    def test_free_key_cannot_use_open_api(self, env) -> None:
        """免费档用 Key 调开放端点 → **403**（不是 401：Key 本身有效）。"""
        from app.security.api_key_service import ApiKeyService

        user = _user(env, "ag1")

        async def _key() -> str:
            service = ApiKeyService(env.state.factory)
            _, plaintext = await service.create(
                user.id, name="免费Key", scopes=["tasks:read", "tasks:write"]
            )
            return plaintext

        plaintext = asyncio.run(_key())

        resp = env.state.client.get("/api/v1/tasks", headers={"X-API-Key": plaintext})

        assert resp.status_code == 403, resp.text
        assert resp.json()["error_code"] == "PLAN_FEATURE_UNAVAILABLE"

    def test_pro_key_can_use_open_api(self, env) -> None:
        from app.security.api_key_service import ApiKeyService

        user = _user(env, "ag2")
        _subscribe(env, user, "pro")

        async def _key() -> str:
            service = ApiKeyService(env.state.factory)
            _, plaintext = await service.create(
                user.id, name="专业Key", scopes=["tasks:read", "tasks:write"]
            )
            return plaintext

        plaintext = asyncio.run(_key())

        resp = env.state.client.get("/api/v1/tasks", headers={"X-API-Key": plaintext})

        assert resp.status_code == 200, resp.text

    def test_jwt_not_blocked_by_api_gate(self, env) -> None:
        """**关键**：网页（JWT）不受 `allow_api_access` 限制。

        否则免费用户连网页都用不了 —— 而网页正是免费档的唯一入口。
        """
        user = _user(env, "ag3")

        resp = env.state.client.get("/api/v1/tasks", headers=_headers(user))

        assert resp.status_code == 200, resp.text
