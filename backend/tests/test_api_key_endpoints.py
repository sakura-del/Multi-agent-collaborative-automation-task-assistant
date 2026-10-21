"""API Key 管理端点测试（Phase 2 · 开放 API Task 4）。

## 本文件锁定

1. **明文只返回一次**：创建响应含 `key`，列表响应**绝不**含 `key`；
2. **管理端点拒绝 Key 鉴权**：一个 Key 能生成新 Key = 权限提升；
3. scope 校验（未知 scope → 422/400）；
4. 吊销后列表不含、认证失效；
5. 只能操作自己的 Key（跨用户 404）。
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
from app.api.v1.endpoints import api_keys as api_keys_endpoint
from app.core.exceptions import AppException
from app.core.security import create_access_token
from app.db.base import Base
from app.db.models.user import User
from app.db.session import get_db
from app.security.api_key_service import ApiKeyService


@pytest.fixture
def key_app() -> Any:
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
    application.include_router(api_keys_endpoint.router, prefix="/api/v1")

    @application.exception_handler(AppException)
    async def _handle(_: Any, exc: AppException) -> Any:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error_code": exc.error_code, "message": exc.message},
        )

    application.dependency_overrides[get_db] = _get_db
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


def _user(factory, name: str) -> User:
    return asyncio.run(_make_user(factory, name))


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


class TestCreateKey:
    def test_create_returns_plaintext_once(self, key_app) -> None:
        user = _user(key_app.state.factory, "mk_owner")
        client = key_app.state.client

        resp = client.post(
            "/api/v1/api-keys",
            headers=_headers(user),
            json={"name": "我的脚本", "scopes": ["tasks:read", "tasks:write"]},
        )

        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["key"].startswith("maa_")
        assert body["prefix"] == body["key"][: len(body["prefix"])]
        assert body["name"] == "我的脚本"
        assert set(body["scopes"]) == {"tasks:read", "tasks:write"}
        assert body["revoked_at"] is None

    def test_list_never_returns_plaintext(self, key_app) -> None:
        """**安全硬断言**：列表响应绝不能含明文（否则「只显示一次」形同虚设）。"""
        user = _user(key_app.state.factory, "mk_list")
        client = key_app.state.client
        created = client.post("/api/v1/api-keys", headers=_headers(user), json={"name": "k"}).json()

        listed = client.get("/api/v1/api-keys", headers=_headers(user)).json()

        assert len(listed) == 1
        assert "key" not in listed[0], "列表绝不能返回明文"
        assert created["key"] not in str(listed), "明文不能出现在列表响应里"

    def test_default_scopes(self, key_app) -> None:
        user = _user(key_app.state.factory, "mk_default")
        client = key_app.state.client

        resp = client.post("/api/v1/api-keys", headers=_headers(user), json={"name": "k"})

        assert set(resp.json()["scopes"]) == {"tasks:read", "tasks:write"}

    def test_unknown_scope_rejected(self, key_app) -> None:
        """未知 scope → **422**（Pydantic `Literal` 在 schema 层就拦住）。

        比服务层 400 更早、更准确（请求格式错，不是业务规则错）。
        服务层的 scope 校验仍然保留 —— 它是防御性兜底（万一有人绕过 schema
        直接调服务层）。
        """
        user = _user(key_app.state.factory, "mk_bad")
        client = key_app.state.client

        resp = client.post(
            "/api/v1/api-keys",
            headers=_headers(user),
            json={"name": "k", "scopes": ["tasks:nonsense"]},
        )

        assert resp.status_code == 422, resp.text

    def test_empty_scopes_rejected(self, key_app) -> None:
        user = _user(key_app.state.factory, "mk_empty")
        client = key_app.state.client

        resp = client.post(
            "/api/v1/api-keys", headers=_headers(user), json={"name": "k", "scopes": []}
        )

        assert resp.status_code == 400, resp.text

    def test_expires_in_days(self, key_app) -> None:
        user = _user(key_app.state.factory, "mk_exp")
        client = key_app.state.client

        resp = client.post(
            "/api/v1/api-keys",
            headers=_headers(user),
            json={"name": "k", "expires_in_days": 90},
        )

        assert resp.status_code == 201
        assert resp.json()["expires_at"] is not None

    def test_requires_auth(self, key_app) -> None:
        resp = key_app.state.client.post("/api/v1/api-keys", json={"name": "k"})

        assert resp.status_code == 401

    def test_rejects_api_key_auth(self, key_app) -> None:
        """**权限提升防护**：Key 不能生成新 Key。"""
        user = _user(key_app.state.factory, "mk_escalate")
        client = key_app.state.client

        async def _make_key() -> str:
            service = ApiKeyService(key_app.state.factory)
            _, plaintext = await service.create(user.id, name="k")
            return plaintext

        plaintext = asyncio.run(_make_key())
        resp = client.post(
            "/api/v1/api-keys", headers={"X-API-Key": plaintext}, json={"name": "新 Key"}
        )

        assert resp.status_code == 401, resp.text


class TestListKeys:
    def test_only_my_keys(self, key_app) -> None:
        alice = _user(key_app.state.factory, "lk_alice")
        bob = _user(key_app.state.factory, "lk_bob")
        client = key_app.state.client
        client.post("/api/v1/api-keys", headers=_headers(alice), json={"name": "alice 的"})
        client.post("/api/v1/api-keys", headers=_headers(bob), json={"name": "bob 的"})

        alice_keys = client.get("/api/v1/api-keys", headers=_headers(alice)).json()

        assert len(alice_keys) == 1
        assert alice_keys[0]["name"] == "alice 的"

    def test_revoked_excluded_by_default(self, key_app) -> None:
        user = _user(key_app.state.factory, "lk_rev")
        client = key_app.state.client
        created = client.post("/api/v1/api-keys", headers=_headers(user), json={"name": "k"}).json()

        client.delete(f"/api/v1/api-keys/{created['key_id']}", headers=_headers(user))
        listed = client.get("/api/v1/api-keys", headers=_headers(user)).json()

        assert listed == []


class TestRevoke:
    def test_revoke_makes_key_unusable(self, key_app) -> None:
        user = _user(key_app.state.factory, "rv_owner")
        client = key_app.state.client
        created = client.post("/api/v1/api-keys", headers=_headers(user), json={"name": "k"}).json()

        resp = client.delete(f"/api/v1/api-keys/{created['key_id']}", headers=_headers(user))
        assert resp.status_code == 204

        async def _check() -> Any:
            service = ApiKeyService(key_app.state.factory)
            return await service.authenticate(created["key"])

        assert asyncio.run(_check()) is None

    def test_revoke_others_key_returns_404(self, key_app) -> None:
        alice = _user(key_app.state.factory, "rv_alice")
        bob = _user(key_app.state.factory, "rv_bob")
        client = key_app.state.client
        created = client.post(
            "/api/v1/api-keys", headers=_headers(alice), json={"name": "k"}
        ).json()

        resp = client.delete(f"/api/v1/api-keys/{created['key_id']}", headers=_headers(bob))

        assert resp.status_code == 404, "跨用户必须 404（不泄漏存在性）"

    def test_revoke_is_idempotent(self, key_app) -> None:
        user = _user(key_app.state.factory, "rv_idem")
        client = key_app.state.client
        created = client.post("/api/v1/api-keys", headers=_headers(user), json={"name": "k"}).json()

        first = client.delete(f"/api/v1/api-keys/{created['key_id']}", headers=_headers(user))
        second = client.delete(f"/api/v1/api-keys/{created['key_id']}", headers=_headers(user))

        assert first.status_code == 204
        assert second.status_code == 204

    def test_revoke_unknown_key(self, key_app) -> None:
        user = _user(key_app.state.factory, "rv_unknown")
        client = key_app.state.client

        resp = client.delete(f"/api/v1/api-keys/{uuid.uuid4()}", headers=_headers(user))

        assert resp.status_code == 404
