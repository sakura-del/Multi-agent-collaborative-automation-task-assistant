"""后台管理控制台（Issue #12）。

## 锁定的口径

1. KPI 一次返回，数字同快照（不是 6 个接口的拼接）；
2. 窗口按 `created_at` 切（「这周失败率」= 这周提交的任务里失败占比）；
3. 失败率分母是**已结束**（completed/failed/cancelled），不是全部；
4. 跨用户列表按 `created_at` 倒序，筛选/分页下推 SQL；
5. 只有 admin 能读，普通用户 403。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.db.models  # noqa: F401
from app.api.v1.endpoints import admin_console
from app.core.exceptions import AppException
from app.core.security import create_access_token
from app.db.base import Base
from app.db.models.billing import Plan, Subscription
from app.db.models.llm_credential import LLMCredential
from app.db.models.task import Task
from app.db.models.user import User
from app.db.session import get_db


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _user(factory, name: str, *, role: str = "user", active: bool = True) -> User:
    async with factory() as session:
        user = User(
            username=name,
            email=f"{name}@example.com",
            password_hash="x",
            role=role,
            is_active=active,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _task(
    factory,
    user_id,
    *,
    status: str = "completed",
    tokens: int = 0,
    is_byok: bool = False,
    created_at: datetime | None = None,
    title: str = "后台用例任务",
    snapshot: dict[str, Any] | None = None,
) -> Task:
    async with factory() as session:
        task = Task(
            user_id=user_id,
            title=title,
            description="d",
            task_type="literature_review",
            status=status,
            config={},
            used_tokens=tokens,
            is_byok=is_byok,
            model_snapshot=snapshot,
            created_at=created_at or datetime.now(UTC),
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        return task


async def _credential(factory, user_id, *, status: str = "active", name: str = "k") -> None:
    async with factory() as session:
        session.add(
            LLMCredential(
                user_id=user_id,
                name=name,
                provider="deepseek",
                base_url="https://api.deepseek.com/v1",
                model="deepseek-chat",
                api_key_ciphertext="v1:fake",
                api_key_hint="sk-…fake",
                key_fingerprint="f" * 32,
                status=status,
            )
        )
        await session.commit()


async def _subscription(
    factory, user_id, *, plan_name: str = "专业版", status: str = "active"
) -> None:
    async with factory() as session:
        plan_id = f"plan-{plan_name}"
        plan = await session.get(Plan, plan_id)
        if plan is None:
            session.add(
                Plan(
                    id=plan_id,
                    name=plan_name,
                    monthly_reviews=50,
                    max_workspaces=3,
                    max_seats_per_workspace=3,
                    allow_api_access=True,
                    allow_team=False,
                    price_cents=0,
                    description="test",
                    is_active=True,
                )
            )
            await session.flush()
        now = datetime.now(UTC)
        session.add(
            Subscription(
                user_id=user_id,
                plan_id=plan_id,
                status=status,
                period_start=now,
                period_end=now + timedelta(days=30),
                seats_purchased=1,
                source="manual",
            )
        )
        await session.commit()


def _client(factory) -> TestClient:
    async def _get_db():
        async with factory() as session:
            yield session

    application = FastAPI()
    application.include_router(admin_console.router, prefix="/api/v1")
    application.dependency_overrides[get_db] = _get_db

    @application.exception_handler(AppException)
    async def _handle(_: Any, exc: AppException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"message": exc.message})

    return TestClient(application)


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


class TestAuth:
    def test_admin_can_read(self, factory) -> None:
        admin = asyncio.run(_user(factory, "admin_user", role="admin"))
        client = _client(factory)
        for path in ("/api/v1/admin/console/overview", "/api/v1/admin/console/tasks"):
            resp = client.get(path, headers=_auth(admin))
            assert resp.status_code == 200, path
        assert client.get("/api/v1/admin/console/users", headers=_auth(admin)).status_code == 200

    def test_regular_user_is_forbidden(self, factory) -> None:
        user = asyncio.run(_user(factory, "plain_user"))
        client = _client(factory)
        for path in (
            "/api/v1/admin/console/overview",
            "/api/v1/admin/console/tasks",
            "/api/v1/admin/console/users",
        ):
            assert client.get(path, headers=_auth(user)).status_code == 403, path


class TestOverview:
    def test_window_aggregation(self, factory) -> None:
        now = datetime.now(UTC)
        alice = asyncio.run(_user(factory, "alice"))
        bob = asyncio.run(_user(factory, "bob"))

        # 窗口内：completed / failed / running / waiting_approval / byok
        asyncio.run(
            _task(
                factory,
                alice.id,
                status="completed",
                tokens=100,
                created_at=now - timedelta(days=1),
            )
        )
        asyncio.run(
            _task(factory, alice.id, status="failed", tokens=20, created_at=now - timedelta(days=2))
        )
        asyncio.run(
            _task(factory, bob.id, status="running", tokens=7, created_at=now - timedelta(days=1))
        )
        asyncio.run(
            _task(factory, bob.id, status="waiting_approval", created_at=now - timedelta(days=3))
        )
        asyncio.run(
            _task(
                factory,
                bob.id,
                status="completed",
                tokens=50,
                is_byok=True,
                created_at=now - timedelta(days=1),
                snapshot={"name": "DeepSeek 官方", "model": "deepseek-chat", "is_byok": True},
            )
        )
        # 窗口外：不计入窗口指标，但计入 total
        asyncio.run(
            _task(
                factory, alice.id, status="failed", tokens=999, created_at=now - timedelta(days=30)
            )
        )

        asyncio.run(_credential(factory, alice.id, status="active"))
        asyncio.run(_credential(factory, bob.id, status="invalid", name="k2"))

        admin = asyncio.run(_user(factory, "admin2", role="admin"))
        resp = _client(factory).get(
            "/api/v1/admin/console/overview", params={"days": 7}, headers=_auth(admin)
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()

        assert data["users"]["total"] == 3
        assert data["users"]["active_in_window"] == 2  # alice + bob（admin 无任务）
        assert data["tasks"]["total"] == 6
        assert data["tasks"]["created_in_window"] == 5
        assert data["tasks"]["failed_in_window"] == 1
        # 窗口内已结束：completed ×2 + failed ×1
        assert data["tasks"]["finished_in_window"] == 3
        assert data["tasks"]["failure_rate"] == pytest.approx(1 / 3)
        assert data["tasks"]["pending_action"] == 1
        assert data["tasks"]["by_status"]["completed"] == 2
        assert data["tokens_used_in_window"] == 100 + 20 + 7 + 50
        assert data["byok_tasks_in_window"] == 1
        assert data["byok_ratio"] == pytest.approx(1 / 5)
        assert data["active_credentials"] == 1

    def test_empty_database(self, factory) -> None:
        admin = asyncio.run(_user(factory, "admin3", role="admin"))
        data = _client(factory).get("/api/v1/admin/console/overview", headers=_auth(admin)).json()
        assert data["tasks"]["failure_rate"] == 0.0
        assert data["byok_ratio"] == 0.0
        assert data["tasks"]["total"] == 0


class TestTaskList:
    def _seed(self, factory):
        now = datetime.now(UTC)
        alice = asyncio.run(_user(factory, "alice_t"))
        bob = asyncio.run(_user(factory, "bob_t"))
        asyncio.run(
            _task(factory, alice.id, title="平台模型任务", created_at=now - timedelta(hours=3))
        )
        asyncio.run(
            _task(
                factory,
                bob.id,
                status="failed",
                title="失败任务",
                created_at=now - timedelta(hours=2),
            )
        )
        asyncio.run(
            _task(
                factory,
                bob.id,
                status="waiting_approval",
                title="BYOK 任务",
                is_byok=True,
                created_at=now - timedelta(hours=1),
                snapshot={"name": "DeepSeek 官方", "model": "deepseek-chat", "is_byok": True},
            )
        )
        return alice, bob

    def test_cross_user_rows_and_order(self, factory) -> None:
        self._seed(factory)
        admin = asyncio.run(_user(factory, "admin4", role="admin"))
        data = (
            _client(factory)
            .get("/api/v1/admin/console/tasks", params={"limit": 10}, headers=_auth(admin))
            .json()
        )
        assert data["total"] == 3
        # created_at 倒序：最新的是 BYOK 任务
        assert [row["title"] for row in data["items"]] == ["BYOK 任务", "失败任务", "平台模型任务"]
        byok_row = data["items"][0]
        assert byok_row["owner_username"] == "bob_t"
        assert byok_row["is_byok"] is True
        assert byok_row["model_label"] == "DeepSeek 官方（deepseek-chat）"
        assert data["items"][2]["model_label"] is None

    def test_status_and_query_filter(self, factory) -> None:
        self._seed(factory)
        admin = asyncio.run(_user(factory, "admin5", role="admin"))
        client = _client(factory)
        failed = client.get(
            "/api/v1/admin/console/tasks", params={"status": "failed"}, headers=_auth(admin)
        ).json()
        assert failed["total"] == 1 and failed["items"][0]["title"] == "失败任务"
        searched = client.get(
            "/api/v1/admin/console/tasks", params={"q": "BYOK"}, headers=_auth(admin)
        ).json()
        assert searched["total"] == 1 and searched["items"][0]["title"] == "BYOK 任务"
        # 分页：total 是过滤后的总数，不是当前页
        page = client.get(
            "/api/v1/admin/console/tasks",
            params={"limit": 1, "offset": 1},
            headers=_auth(admin),
        ).json()
        assert page["total"] == 3 and len(page["items"]) == 1
        assert page["items"][0]["title"] == "失败任务"


class TestUserList:
    def test_plan_and_counts(self, factory) -> None:
        alice = asyncio.run(_user(factory, "alice_u"))
        asyncio.run(_user(factory, "bob_u"))
        asyncio.run(_user(factory, "carol_u", active=False))
        asyncio.run(_subscription(factory, alice.id, plan_name="专业版"))
        asyncio.run(_task(factory, alice.id))
        asyncio.run(_task(factory, alice.id, status="failed"))
        asyncio.run(_credential(factory, alice.id))

        admin = asyncio.run(_user(factory, "admin6", role="admin"))
        data = (
            _client(factory)
            .get("/api/v1/admin/console/users", params={"limit": 10}, headers=_auth(admin))
            .json()
        )
        assert data["total"] == 4
        rows = {row["username"]: row for row in data["items"]}
        assert rows["alice_u"]["plan_name"] == "专业版"
        assert rows["alice_u"]["task_count"] == 2
        assert rows["alice_u"]["credential_count"] == 1
        assert rows["bob_u"]["plan_name"] is None
        assert rows["bob_u"]["task_count"] == 0
        assert rows["carol_u"]["is_active"] is False

    def test_role_and_query_filter(self, factory) -> None:
        asyncio.run(_user(factory, "plain_a"))
        asyncio.run(_user(factory, "boss_a", role="admin"))
        admin = asyncio.run(_user(factory, "boss_b", role="admin"))
        client = _client(factory)
        admins = client.get(
            "/api/v1/admin/console/users", params={"role": "admin"}, headers=_auth(admin)
        ).json()
        assert {row["username"] for row in admins["items"]} == {"boss_a", "boss_b"}
        searched = client.get(
            "/api/v1/admin/console/users", params={"q": "plain"}, headers=_auth(admin)
        ).json()
        assert searched["total"] == 1 and searched["items"][0]["username"] == "plain_a"
