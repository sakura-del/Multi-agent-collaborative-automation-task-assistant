"""BYOK 凭据端点测试（Issue #15 · P0）。

## 本文件锁定

1. **明文只进不出**：创建/更新/列表/详情响应都不含明文，DB 里只有密文；
2. **越权即 404**：不能读、改、删、使用别人的凭据；
3. **预置厂商的 base_url 由服务端决定**，用户传了要拒绝（否则平台 key 会被导向任意地址）；
4. 自定义端点做**创建期**的第一道拦截：https、443、禁私网 IP 字面量；
5. 默认凭据唯一，删除后自动递补；
6. 轮换 key 递增 `key_version`（运行时缓存失效的依据）。
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
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.db.models  # noqa: F401
from app.api.v1.endpoints import llm_credentials as credentials_endpoint
from app.core import config
from app.core.exceptions import AppException
from app.core.security import create_access_token
from app.db.base import Base
from app.db.models.llm_credential import LLMCredential
from app.db.models.user import User
from app.db.session import get_db
from app.security.credential_cipher import CredentialCipher
from app.security.llm_credential_service import LLMCredentialService

PLAINTEXT = "sk-live-0123456789abcdef"


@pytest.fixture
def credential_app() -> Any:
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
    application.include_router(credentials_endpoint.router, prefix="/api/v1")

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


def _make_user(factory: Any, name: str) -> User:
    async def _run() -> User:
        async with factory() as session:
            user = User(
                username=name,
                email=f"{name}@example.com",
                password_hash="x",
                is_active=True,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user

    return asyncio.run(_run())


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


def _create(
    app: Any,
    user: User,
    *,
    name: str = "我的 DeepSeek",
    provider: str = "deepseek",
    model: str = "deepseek-chat",
    api_key: str = PLAINTEXT,
    base_url: str | None = None,
    is_default: bool = False,
) -> Any:
    payload: dict[str, Any] = {
        "name": name,
        "provider": provider,
        "model": model,
        "api_key": api_key,
        "is_default": is_default,
    }
    if base_url is not None:
        payload["base_url"] = base_url
    return app.state.client.post("/api/v1/llm-credentials", headers=_headers(user), json=payload)


def _rows(factory: Any) -> list[LLMCredential]:
    async def _run() -> list[LLMCredential]:
        async with factory() as session:
            result = await session.execute(
                select(LLMCredential).order_by(LLMCredential.created_at, LLMCredential.id)
            )
            return list(result.scalars().all())

    return asyncio.run(_run())


class TestCreate:
    def test_preset_uses_server_side_base_url(self, credential_app: Any) -> None:
        user = _make_user(credential_app.state.factory, "cred_preset")
        resp = _create(credential_app, user, provider="deepseek")

        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["base_url"] == "https://api.deepseek.com/v1"
        assert body["provider"] == "deepseek"
        assert body["provider_label"] == "DeepSeek"
        assert body["is_default"] is True  # 第一条自动成为默认
        assert body["key_version"] == 1

    def test_preset_rejects_user_base_url(self, credential_app: Any) -> None:
        """预置厂商若允许改 base_url，用户就能把平台的 key 导向自己的服务器。"""
        user = _make_user(credential_app.state.factory, "cred_preset_url")
        resp = _create(
            credential_app,
            user,
            provider="openai",
            base_url="https://evil.example.com/v1",
        )
        assert resp.status_code == 422, resp.text

    def test_custom_requires_base_url(self, credential_app: Any) -> None:
        user = _make_user(credential_app.state.factory, "cred_custom_none")
        resp = _create(credential_app, user, provider="custom", base_url=None)
        assert resp.status_code == 422, resp.text

    def test_custom_accepts_https(self, credential_app: Any) -> None:
        user = _make_user(credential_app.state.factory, "cred_custom_ok")
        resp = _create(
            credential_app,
            user,
            provider="custom",
            base_url="https://relay.example.com/v1/",
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["base_url"] == "https://relay.example.com/v1"

    @pytest.mark.parametrize(
        "url",
        [
            "http://relay.example.com/v1",  # 明文传输
            "https://127.0.0.1/v1",  # 环回
            "https://localhost/v1",  # 本机名
            "https://10.0.0.8/v1",  # 私网
            "https://169.254.169.254/latest",  # 云元数据
            "https://[::1]/v1",  # IPv6 环回
            "https://relay.example.com:8443/v1",  # 非白名单端口
            "https://user:pass@relay.example.com/v1",  # URL 内嵌凭据
        ],
    )
    def test_custom_rejects_unsafe_url(self, credential_app: Any, url: str) -> None:
        user = _make_user(credential_app.state.factory, f"cred_bad_{uuid.uuid4().hex[:6]}")
        resp = _create(credential_app, user, provider="custom", base_url=url)
        assert resp.status_code == 422, f"{url} 应被拒绝，实际 {resp.status_code}: {resp.text}"

    def test_duplicate_name_conflict(self, credential_app: Any) -> None:
        user = _make_user(credential_app.state.factory, "cred_dup")
        assert _create(credential_app, user, name="同名").status_code == 201
        resp = _create(credential_app, user, name="同名")
        assert resp.status_code == 409, resp.text

    def test_response_never_contains_plaintext(self, credential_app: Any) -> None:
        user = _make_user(credential_app.state.factory, "cred_leak")
        resp = _create(credential_app, user)
        body = resp.text
        assert PLAINTEXT not in body
        assert resp.json()["masked_key"] == "sk-…cdef"
        assert "ciphertext" not in resp.json()
        assert "api_key" not in resp.json()

    def test_short_key_not_fully_masked_away(self, credential_app: Any) -> None:
        """短 key 只显示后 4 位；不能因为「太短」把整串显示出来。"""
        user = _make_user(credential_app.state.factory, "cred_short")
        resp = _create(credential_app, user, api_key="12345678")
        assert resp.status_code == 201, resp.text
        assert resp.json()["masked_key"] == "…5678"

    def test_name_and_model_are_stripped(self, credential_app: Any) -> None:
        user = _make_user(credential_app.state.factory, "cred_strip")
        resp = _create(credential_app, user, name="  我的 key  ", model="  deepseek-chat  ")
        assert resp.status_code == 201, resp.text
        assert resp.json()["name"] == "我的 key"
        assert resp.json()["model"] == "deepseek-chat"

    def test_credential_count_is_capped(
        self, credential_app: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """凭据是用户可无限新增的行，必须有上限（防脚本把表填满）。"""
        from app.security import llm_credential_service as service_module

        monkeypatch.setattr(service_module, "MAX_CREDENTIALS_PER_USER", 2)
        user = _make_user(credential_app.state.factory, "cred_limit")

        assert _create(credential_app, user, name="one").status_code == 201
        assert _create(credential_app, user, name="two").status_code == 201
        resp = _create(credential_app, user, name="three")
        assert resp.status_code == 422, resp.text
        assert "最多" in resp.json()["message"]

    def test_missing_master_key_fails_closed(
        self, credential_app: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """没配主密钥时必须 503，**绝不能退化成明文存储**。"""
        monkeypatch.setattr(config.settings.secrets, "encryption_key", "")
        user = _make_user(credential_app.state.factory, "cred_nokey")

        resp = _create(credential_app, user)

        assert resp.status_code == 503, resp.text
        assert resp.json()["error_code"] == "ENCRYPTION_KEY_UNAVAILABLE"
        assert _rows(credential_app.state.factory) == []


class TestList:
    def test_list_masks_and_hides_ciphertext(self, credential_app: Any) -> None:
        user = _make_user(credential_app.state.factory, "cred_list")
        _create(credential_app, user)
        resp = credential_app.state.client.get("/api/v1/llm-credentials", headers=_headers(user))
        assert resp.status_code == 200, resp.text
        listed = resp.json()
        assert len(listed) == 1
        assert PLAINTEXT not in resp.text
        assert "ciphertext" not in resp.text

    def test_list_only_own(self, credential_app: Any) -> None:
        alice = _make_user(credential_app.state.factory, "cred_alice")
        bob = _make_user(credential_app.state.factory, "cred_bob")
        _create(credential_app, alice, name="alice-key")

        resp = credential_app.state.client.get("/api/v1/llm-credentials", headers=_headers(bob))
        assert resp.status_code == 200
        assert resp.json() == []

    def test_default_sorts_first(self, credential_app: Any) -> None:
        user = _make_user(credential_app.state.factory, "cred_sort")
        _create(credential_app, user, name="first")
        second = _create(credential_app, user, name="second").json()
        credential_app.state.client.patch(
            f"/api/v1/llm-credentials/{second['credential_id']}",
            headers=_headers(user),
            json={"is_default": True},
        )

        listed = credential_app.state.client.get(
            "/api/v1/llm-credentials", headers=_headers(user)
        ).json()
        assert listed[0]["name"] == "second"
        assert listed[0]["is_default"] is True
        assert sum(1 for item in listed if item["is_default"]) == 1

    def test_providers_catalog(self, credential_app: Any) -> None:
        user = _make_user(credential_app.state.factory, "cred_providers")
        resp = credential_app.state.client.get(
            "/api/v1/llm-credentials/providers", headers=_headers(user)
        )
        assert resp.status_code == 200, resp.text
        items = resp.json()
        keys = {item["provider"] for item in items}
        assert {"openai", "deepseek", "glm", "qwen", "siliconflow", "moonshot", "custom"} == keys
        custom = next(item for item in items if item["provider"] == "custom")
        assert custom["requires_base_url"] is True
        # 目录里不能出现任何密钥字段
        assert all("key" not in field for item in items for field in item)


class TestGet:
    def test_get_own(self, credential_app: Any) -> None:
        user = _make_user(credential_app.state.factory, "cred_get")
        created = _create(credential_app, user).json()
        resp = credential_app.state.client.get(
            f"/api/v1/llm-credentials/{created['credential_id']}", headers=_headers(user)
        )
        assert resp.status_code == 200
        assert resp.json()["credential_id"] == created["credential_id"]

    def test_other_user_gets_404(self, credential_app: Any) -> None:
        alice = _make_user(credential_app.state.factory, "cred_get_a")
        bob = _make_user(credential_app.state.factory, "cred_get_b")
        created = _create(credential_app, alice).json()

        resp = credential_app.state.client.get(
            f"/api/v1/llm-credentials/{created['credential_id']}", headers=_headers(bob)
        )
        assert resp.status_code == 404

    def test_missing_returns_404(self, credential_app: Any) -> None:
        user = _make_user(credential_app.state.factory, "cred_missing")
        resp = credential_app.state.client.get(
            f"/api/v1/llm-credentials/{uuid.uuid4()}", headers=_headers(user)
        )
        assert resp.status_code == 404


class TestUpdate:
    def test_rename(self, credential_app: Any) -> None:
        user = _make_user(credential_app.state.factory, "cred_rename")
        created = _create(credential_app, user, name="旧名").json()
        resp = credential_app.state.client.patch(
            f"/api/v1/llm-credentials/{created['credential_id']}",
            headers=_headers(user),
            json={"name": "新名"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["name"] == "新名"

    def test_rotate_key_increments_version(self, credential_app: Any) -> None:
        user = _make_user(credential_app.state.factory, "cred_rotate")
        created = _create(credential_app, user).json()
        assert created["key_version"] == 1

        resp = credential_app.state.client.patch(
            f"/api/v1/llm-credentials/{created['credential_id']}",
            headers=_headers(user),
            json={"api_key": "sk-rotated-9876543210"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["key_version"] == 2
        assert body["masked_key"] == "sk-…3210"
        assert "sk-rotated-9876543210" not in resp.text

    def test_set_default_clears_previous(self, credential_app: Any) -> None:
        user = _make_user(credential_app.state.factory, "cred_default")
        first = _create(credential_app, user, name="one").json()
        second = _create(credential_app, user, name="two").json()

        resp = credential_app.state.client.patch(
            f"/api/v1/llm-credentials/{second['credential_id']}",
            headers=_headers(user),
            json={"is_default": True},
        )
        assert resp.status_code == 200, resp.text

        rows = {row.name: row.is_default for row in _rows(credential_app.state.factory)}
        assert rows == {"one": False, "two": True}
        assert first["is_default"] is True  # 创建时是默认

    def test_duplicate_name_conflict(self, credential_app: Any) -> None:
        user = _make_user(credential_app.state.factory, "cred_upd_dup")
        _create(credential_app, user, name="A")
        target = _create(credential_app, user, name="B").json()
        resp = credential_app.state.client.patch(
            f"/api/v1/llm-credentials/{target['credential_id']}",
            headers=_headers(user),
            json={"name": "A"},
        )
        assert resp.status_code == 409, resp.text

    def test_other_user_cannot_update(self, credential_app: Any) -> None:
        alice = _make_user(credential_app.state.factory, "cred_upd_a")
        bob = _make_user(credential_app.state.factory, "cred_upd_b")
        created = _create(credential_app, alice).json()
        resp = credential_app.state.client.patch(
            f"/api/v1/llm-credentials/{created['credential_id']}",
            headers=_headers(bob),
            json={"name": "hijack"},
        )
        assert resp.status_code == 404
        assert _rows(credential_app.state.factory)[0].name == "我的 DeepSeek"

    def test_preset_cannot_change_base_url(self, credential_app: Any) -> None:
        user = _make_user(credential_app.state.factory, "cred_upd_url")
        created = _create(credential_app, user, provider="openai").json()
        resp = credential_app.state.client.patch(
            f"/api/v1/llm-credentials/{created['credential_id']}",
            headers=_headers(user),
            json={"base_url": "https://evil.example.com/v1"},
        )
        assert resp.status_code == 422, resp.text


class TestDelete:
    def test_delete_removes_row(self, credential_app: Any) -> None:
        user = _make_user(credential_app.state.factory, "cred_del")
        created = _create(credential_app, user).json()
        resp = credential_app.state.client.delete(
            f"/api/v1/llm-credentials/{created['credential_id']}", headers=_headers(user)
        )
        assert resp.status_code == 204, resp.text
        assert _rows(credential_app.state.factory) == []

    def test_delete_promotes_another_default(self, credential_app: Any) -> None:
        user = _make_user(credential_app.state.factory, "cred_del_default")
        first = _create(credential_app, user, name="one").json()
        _create(credential_app, user, name="two")

        credential_app.state.client.delete(
            f"/api/v1/llm-credentials/{first['credential_id']}", headers=_headers(user)
        )

        rows = _rows(credential_app.state.factory)
        assert len(rows) == 1
        assert rows[0].name == "two"
        assert rows[0].is_default is True

    def test_other_user_cannot_delete(self, credential_app: Any) -> None:
        alice = _make_user(credential_app.state.factory, "cred_del_a")
        bob = _make_user(credential_app.state.factory, "cred_del_b")
        created = _create(credential_app, alice).json()
        resp = credential_app.state.client.delete(
            f"/api/v1/llm-credentials/{created['credential_id']}", headers=_headers(bob)
        )
        assert resp.status_code == 404
        assert len(_rows(credential_app.state.factory)) == 1


class TestStorage:
    def test_db_stores_ciphertext_only(self, credential_app: Any) -> None:
        user = _make_user(credential_app.state.factory, "cred_db")
        _create(credential_app, user)

        rows = _rows(credential_app.state.factory)
        assert len(rows) == 1
        assert rows[0].api_key_ciphertext != PLAINTEXT
        assert PLAINTEXT not in rows[0].api_key_ciphertext
        assert rows[0].api_key_ciphertext.startswith("v1:")

    def test_ciphertext_decrypts_with_service(self, credential_app: Any) -> None:
        user = _make_user(credential_app.state.factory, "cred_decrypt")
        _create(credential_app, user)

        row = _rows(credential_app.state.factory)[0]
        service = LLMCredentialService(cipher=CredentialCipher.from_settings())
        assert service.decrypt_api_key(row) == PLAINTEXT

    def test_logs_do_not_contain_plaintext(
        self, credential_app: Any, caplog: pytest.LogCaptureFixture
    ) -> None:
        user = _make_user(credential_app.state.factory, "cred_log")
        with caplog.at_level("DEBUG"):
            assert _create(credential_app, user).status_code == 201
        assert PLAINTEXT not in caplog.text

    def test_settings_key_is_configured_for_tests(self) -> None:
        """保护性断言：conftest 必须给测试环境配主密钥，否则上面几条会「假绿」。"""
        assert config.settings.secrets.encryption_key


class TestModelContract:
    def test_no_plaintext_column(self) -> None:
        columns = {column.name for column in LLMCredential.__table__.columns}
        assert "api_key_ciphertext" in columns
        assert "api_key" not in columns
        assert "plaintext" not in columns
        assert "secret" not in columns

    def test_default_unique_index_declared(self) -> None:
        """默认凭据在 DB 层也唯一（应用层清理 + 索引兜底，防并发双默认）。"""
        indexes = {index.name for index in LLMCredential.__table__.indexes}
        assert "uq_llm_credential_user_default" in indexes

    def test_row_count_helper_sanity(self, credential_app: Any) -> None:
        user = _make_user(credential_app.state.factory, "cred_count")
        _create(credential_app, user)

        async def _count() -> int:
            async with credential_app.state.factory() as session:
                return int(
                    (await session.execute(select(func.count(LLMCredential.id)))).scalar_one()
                )

        assert asyncio.run(_count()) == 1
