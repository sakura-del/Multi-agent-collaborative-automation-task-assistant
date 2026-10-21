"""双通道鉴权测试（Phase 2 · 开放 API Task 3）。

## 本文件锁定**最关键的风险点**

Task 3 改的是**共用鉴权依赖**，影响面覆盖既有全部任务端点（400+ 测试依赖它）。
所以这里的重点不是「Key 能用」，而是**「JWT 仍然能用」**——回归保护。

## 三条设计约束

1. `X-API-Key` 优先，其次 `Authorization: Bearer`；
2. **scope 校验只对 Key 生效**：JWT 用户走既有角色逻辑（网页前端不该被 scope 卡住）；
3. 管理端点（生成/列出/吊销 Key）**拒绝 Key 鉴权** —— 否则一个 Key 能生成新 Key，
   是权限提升。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import app.db.models  # noqa: F401
from app.api.deps import get_current_user_or_api_key, require_scope
from app.core.security import create_access_token
from app.db.base import Base
from app.db.models.user import User
from app.db.session import get_db
from app.security.api_key_service import ApiKeyService


@pytest.fixture
def auth_app() -> Any:
    """最小 app：只挂两条探针端点，验证鉴权依赖本身。"""
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

    application = FastAPI()

    # 生产在 `app/main.py` 注册全局 handler；独立测试 app 需自备，
    # 否则 `UnauthorizedException` 会变成 500（而不是 401）
    from fastapi.responses import JSONResponse

    from app.core.exceptions import AppException

    @application.exception_handler(AppException)
    async def _handle_app(_: Any, exc: AppException) -> Any:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error_code": exc.error_code, "message": exc.message},
        )

    @application.get("/probe")
    async def probe(
        user: User = Depends(get_current_user_or_api_key),
    ) -> dict[str, str]:
        return {"user_id": str(user.id), "username": user.username}

    @application.get("/write-probe")
    async def write_probe(
        user: User = Depends(get_current_user_or_api_key),
        _: None = Depends(require_scope("tasks:write")),
    ) -> dict[str, str]:
        return {"user_id": str(user.id)}

    @application.get("/artifacts-probe")
    async def artifacts_probe(
        user: User = Depends(get_current_user_or_api_key),
        _: None = Depends(require_scope("artifacts:read")),
    ) -> dict[str, str]:
        return {"user_id": str(user.id)}

    application.dependency_overrides[get_db] = _get_db
    application.state.factory = factory
    application.state.engine = engine
    application.state.client = TestClient(application)
    return application


async def _make_user(factory, name: str) -> User:
    async with factory() as session:
        user = User(username=name, email=f"{name}@example.com", password_hash="x", is_active=True)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


def _client(app) -> TestClient:
    return app.state.client


def _setup_user_and_key(app, name: str, scopes: list[str] | None = None) -> tuple[User, str]:
    """同步包装：建用户 + 建 Key（测试里要拿明文）。"""
    factory = app.state.factory

    async def _run() -> tuple[User, str]:
        user = await _make_user(factory, name)
        service = ApiKeyService(factory)
        _, plaintext = await service.create(
            user.id, name="test", scopes=scopes or ["tasks:read", "tasks:write"]
        )
        return user, plaintext

    return asyncio.run(_run())


class TestJwtStillWorks:
    """**回归保护**：改共用依赖后 JWT 必须照旧可用。"""

    def test_jwt_bearer_authenticates(self, auth_app) -> None:
        user = asyncio.run(_make_user(auth_app.state.factory, "jwt_user"))
        token = create_access_token(str(user.id))

        resp = _client(auth_app).get("/probe", headers={"Authorization": f"Bearer {token}"})

        assert resp.status_code == 200, resp.text
        assert resp.json()["user_id"] == str(user.id)

    def test_jwt_skips_scope_check(self, auth_app) -> None:
        """**关键**：JWT 用户不受 scope 限制（走既有角色逻辑）。

        若这条红，说明 scope 校验错误地作用到了网页前端 —— 会让所有网页操作 403。
        """
        user = asyncio.run(_make_user(auth_app.state.factory, "jwt_scope"))
        token = create_access_token(str(user.id))

        # 该端点要求 tasks:write，JWT 用户应无条件通过
        resp = _client(auth_app).get("/write-probe", headers={"Authorization": f"Bearer {token}"})

        assert resp.status_code == 200, resp.text

    def test_jwt_also_passes_artifacts_scope(self, auth_app) -> None:
        user = asyncio.run(_make_user(auth_app.state.factory, "jwt_art"))
        token = create_access_token(str(user.id))

        resp = _client(auth_app).get(
            "/artifacts-probe", headers={"Authorization": f"Bearer {token}"}
        )

        assert resp.status_code == 200, resp.text

    def test_no_credentials_returns_401(self, auth_app) -> None:
        resp = _client(auth_app).get("/probe")

        assert resp.status_code == 401


class TestApiKeyWorks:
    def test_api_key_authenticates(self, auth_app) -> None:
        user, plaintext = _setup_user_and_key(auth_app, "key_user")

        resp = _client(auth_app).get("/probe", headers={"X-API-Key": plaintext})

        assert resp.status_code == 200, resp.text
        assert resp.json()["user_id"] == str(user.id)

    def test_api_key_preferred_over_bearer(self, auth_app) -> None:
        """两套凭证同时给时，Key 优先（显式通道优先于隐式）。"""
        key_user, plaintext = _setup_user_and_key(auth_app, "prefer_key")
        jwt_user = asyncio.run(_make_user(auth_app.state.factory, "prefer_jwt"))
        token = create_access_token(str(jwt_user.id))

        resp = _client(auth_app).get(
            "/probe",
            headers={"X-API-Key": plaintext, "Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 200
        assert resp.json()["user_id"] == str(key_user.id), "Key 优先"

    def test_invalid_api_key_returns_401(self, auth_app) -> None:
        resp = _client(auth_app).get("/probe", headers={"X-API-Key": "maa_invalid000000"})

        assert resp.status_code == 401

    def test_revoked_api_key_returns_401(self, auth_app) -> None:
        user, plaintext = _setup_user_and_key(auth_app, "revoked_key")

        async def _revoke() -> None:
            service = ApiKeyService(auth_app.state.factory)
            keys = await service.list_for_user(user.id)
            await service.revoke(user.id, keys[0].id)

        asyncio.run(_revoke())
        resp = _client(auth_app).get("/probe", headers={"X-API-Key": plaintext})

        assert resp.status_code == 401


class TestScopeEnforcement:
    def test_key_with_scope_passes(self, auth_app) -> None:
        _, plaintext = _setup_user_and_key(
            auth_app, "scope_ok", scopes=["tasks:read", "tasks:write"]
        )

        resp = _client(auth_app).get("/write-probe", headers={"X-API-Key": plaintext})

        assert resp.status_code == 200, resp.text

    def test_key_without_scope_returns_403(self, auth_app) -> None:
        """**scope 不足必须 403**（不是 401——凭证有效，是权限不够）。"""
        _, plaintext = _setup_user_and_key(auth_app, "scope_deny", scopes=["tasks:read"])

        resp = _client(auth_app).get("/write-probe", headers={"X-API-Key": plaintext})

        assert resp.status_code == 403, resp.text
        assert "scope" in resp.text.lower() or "权限" in resp.text

    def test_artifacts_scope_separate(self, auth_app) -> None:
        """`artifacts:read` 与 `tasks:read` 互相独立（不能靠一个换另一个）。"""
        _, plaintext = _setup_user_and_key(auth_app, "scope_art", scopes=["tasks:read"])

        resp = _client(auth_app).get("/artifacts-probe", headers={"X-API-Key": plaintext})

        assert resp.status_code == 403, resp.text


class TestErrorMessages:
    def test_401_message_does_not_leak_reason(self, auth_app) -> None:
        """**不泄漏失效原因**：吊销/过期/不存在都返回同一句。

        区分原因等于告诉攻击者「这个 Key 曾经有效」。
        """
        resp_missing = _client(auth_app).get(
            "/probe", headers={"X-API-Key": "maa_doesnotexist0000000000"}
        )
        user, plaintext = _setup_user_and_key(auth_app, "leak_revoked")

        async def _revoke() -> None:
            service = ApiKeyService(auth_app.state.factory)
            keys = await service.list_for_user(user.id)
            await service.revoke(user.id, keys[0].id)

        asyncio.run(_revoke())
        resp_revoked = _client(auth_app).get("/probe", headers={"X-API-Key": plaintext})

        assert resp_missing.status_code == 401
        assert resp_revoked.status_code == 401
        # 错误体不应包含 "revoked" / "expired" 这类词
        body = resp_revoked.text.lower()
        for word in ("revoked", "expired", "吊销", "过期"):
            assert word not in body, f"401 响应泄漏了失效原因：{word}"


class TestBothChannelsIndependent:
    def test_key_from_other_user_gets_own_identity(self, auth_app) -> None:
        """不同用户的 Key 各自识别为各自用户（不能串号）。"""
        alice, alice_key = _setup_user_and_key(auth_app, "ind_alice")
        bob, bob_key = _setup_user_and_key(auth_app, "ind_bob")
        client = _client(auth_app)

        alice_resp = client.get("/probe", headers={"X-API-Key": alice_key})
        bob_resp = client.get("/probe", headers={"X-API-Key": bob_key})

        assert alice_resp.json()["user_id"] == str(alice.id)
        assert bob_resp.json()["user_id"] == str(bob.id)
        assert alice_resp.json()["user_id"] != bob_resp.json()["user_id"]

    def test_api_key_not_accepted_as_bearer(self, auth_app) -> None:
        """Key 不能塞进 Bearer（两条通道物理隔离）。"""
        _, plaintext = _setup_user_and_key(auth_app, "wrong_channel")

        resp = _client(auth_app).get("/probe", headers={"Authorization": f"Bearer {plaintext}"})

        assert resp.status_code == 401
