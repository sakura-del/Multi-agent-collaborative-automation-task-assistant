"""管理员侧 BYOK 渗透率看板（Issue #15 P2）。

## 锁定的口径

1. 任务/token 占比的分母是**窗口内全部任务**；
2. 用户渗透率的分母是**窗口内跑过任务的用户**，不是注册用户；
3. 「有凭据」与「用过 BYOK」分开：配了 key 但没切模型的人不算渗透；
4. 窗口外任务、其他用户数据不得混入；
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
from app.api.v1.endpoints import admin_byok
from app.billing.byok_analytics import build_byok_overview
from app.core.exceptions import AppException
from app.core.security import create_access_token
from app.db.base import Base
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


async def _user(factory, name: str, *, role: str = "user") -> User:
    async with factory() as session:
        user = User(
            username=name,
            email=f"{name}@example.com",
            password_hash="x",
            role=role,
            is_active=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


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


async def _task(
    factory,
    user_id,
    *,
    tokens: int,
    is_byok: bool,
    created_at: datetime,
    snapshot: dict[str, Any] | None = None,
) -> None:
    async with factory() as session:
        session.add(
            Task(
                user_id=user_id,
                title="byok 看板用例",
                description="d",
                task_type="literature_review",
                status="completed",
                config={},
                used_tokens=tokens,
                is_byok=is_byok,
                model_snapshot=snapshot,
                created_at=created_at,
            )
        )
        await session.commit()


class TestOverview:
    @pytest.mark.asyncio
    async def test_totals_penetration_and_providers(self, factory) -> None:
        now = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
        alice = await _user(factory, "alice")
        bob = await _user(factory, "bob")
        carol = await _user(factory, "carol")
        await _credential(factory, alice.id, status="active")
        await _credential(factory, bob.id, status="invalid", name="k2")
        # carol 配了 key 但窗口内没跑过任务：不能抬高「活跃用户渗透率」，
        # 否则 10 个配了 key、2 个活跃时会出现 500% 的荒谬数字。
        await _credential(factory, carol.id, status="active", name="k3")

        await _task(factory, alice.id, tokens=100, is_byok=False, created_at=now)
        await _task(
            factory,
            alice.id,
            tokens=50,
            is_byok=True,
            created_at=now,
            snapshot={"is_byok": True, "provider": "deepseek", "model": "deepseek-chat"},
        )
        await _task(factory, bob.id, tokens=200, is_byok=False, created_at=now)
        # 窗口外：不得计入
        await _task(
            factory,
            alice.id,
            tokens=999,
            is_byok=True,
            created_at=now - timedelta(days=40),
            snapshot={"is_byok": True, "provider": "deepseek", "model": "deepseek-chat"},
        )

        async with factory() as session:
            data = await build_byok_overview(session, days=30, now=now)

        assert data["totals"] == {
            "tasks": 3,
            "tokens": 350,
            "byok_tasks": 1,
            "byok_tokens": 50,
            "byok_task_share": 0.3333,
            "byok_token_share": 0.1429,
        }
        assert data["users"] == {
            "active": 2,
            "byok_active": 1,
            "with_credentials": 2,
            "with_active_credentials": 1,
            "byok_penetration": 0.5,
            "credential_penetration": 1.0,
        }
        assert data["credentials"] == {"total": 3, "active": 2, "invalid": 1, "disabled": 0}
        assert data["providers"] == [
            {"provider": "deepseek", "model": "deepseek-chat", "tasks": 1, "tokens": 50}
        ]
        assert len(data["top_users"]) == 1
        assert data["top_users"][0]["username"] == "alice"
        assert data["top_users"][0]["byok_token_share"] == 0.3333
        # 30 天窗口每天都有点，图表不断档
        assert len(data["trend"]) == 31
        assert data["trend"][-1]["date"] == "2026-11-16"
        assert data["trend"][-1]["byok_tasks"] == 1

    @pytest.mark.asyncio
    async def test_empty_window(self, factory) -> None:
        async with factory() as session:
            data = await build_byok_overview(
                session, days=7, now=datetime(2026, 11, 16, tzinfo=UTC)
            )

        assert data["totals"]["tasks"] == 0
        assert data["users"]["byok_penetration"] == 0.0
        assert len(data["trend"]) == 8


def _client(factory) -> TestClient:
    async def _get_db():
        async with factory() as session:
            yield session

    application = FastAPI()
    application.include_router(admin_byok.router, prefix="/api/v1")
    application.dependency_overrides[get_db] = _get_db

    @application.exception_handler(AppException)
    async def _handle(_: Any, exc: AppException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"message": exc.message})

    return TestClient(application)


class TestAuth:
    def test_admin_can_read(self, factory) -> None:
        admin = asyncio.run(_user(factory, "admin_user", role="admin"))
        asyncio.run(
            _task(
                factory,
                admin.id,
                tokens=10,
                is_byok=False,
                created_at=datetime.now(UTC),
            )
        )
        client = _client(factory)

        resp = client.get(
            "/api/v1/admin/byok/overview",
            headers={"Authorization": f"Bearer {create_access_token(str(admin.id))}"},
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["totals"]["tasks"] == 1

    def test_normal_user_is_forbidden(self, factory) -> None:
        user = asyncio.run(_user(factory, "normal_user"))
        client = _client(factory)

        resp = client.get(
            "/api/v1/admin/byok/overview",
            headers={"Authorization": f"Bearer {create_access_token(str(user.id))}"},
        )

        assert resp.status_code == 403

    def test_days_out_of_range_is_422(self, factory) -> None:
        admin = asyncio.run(_user(factory, "admin_days", role="admin"))
        client = _client(factory)

        resp = client.get(
            "/api/v1/admin/byok/overview?days=0",
            headers={"Authorization": f"Bearer {create_access_token(str(admin.id))}"},
        )

        assert resp.status_code == 422
