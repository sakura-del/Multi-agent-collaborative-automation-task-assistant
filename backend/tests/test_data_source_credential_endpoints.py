"""用户自带数据源凭据测试（Issue #67 · BYOK）。

## 本文件锁定

1. **明文只进不出**：PUT/GET 响应只有脱敏串，DB 里只有 `v1:` 密文；
2. **越权即不可用**：A 的密文换给 B 解密必须失败（AAD 绑定 user_id），
   B 的列表里也看不到 A 的配置；
3. **每用户每数据源一条**：再次 PUT 是覆盖（换 Key），不新增行；
4. **白名单**：未知 provider 给可读错误，不是静默写入；
5. DELETE 幂等 204，删完运行时读不到 Key（返回 None）。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.db.models  # noqa: F401
from app.api.v1.endpoints import data_source_credentials as credentials_endpoint
from app.core.exceptions import AppException
from app.core.security import create_access_token
from app.db.base import Base
from app.db.models.data_source_credential import DataSourceCredential
from app.db.models.user import User
from app.db.session import get_db
from app.security.credential_cipher import CredentialCipher, CredentialDecryptError
from app.security.data_source_credential_service import DataSourceCredentialService, mask_key

PLAINTEXT = "wfabcdef0123456789abcdef"
OTHER_PLAINTEXT = "wf999888777666555444333"


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


def _put(app: Any, user: User, provider: str = "wanfang", api_key: str = PLAINTEXT) -> Any:
    return app.state.client.put(
        f"/api/v1/data-source-credentials/{provider}",
        headers=_headers(user),
        json={"api_key": api_key},
    )


def _rows(factory: Any) -> list[DataSourceCredential]:
    async def _run() -> list[DataSourceCredential]:
        async with factory() as session:
            result = await session.execute(
                select(DataSourceCredential).order_by(DataSourceCredential.created_at)
            )
            return list(result.scalars().all())

    return asyncio.run(_run())


def _service_call(factory: Any, user: User, provider: str = "wanfang") -> str | None:
    async def _run() -> str | None:
        async with factory() as session:
            return await DataSourceCredentialService().get_decrypted_key(session, user.id, provider)

    return asyncio.run(_run())


class TestMaskKey:
    def test_long_key_keeps_only_edges(self) -> None:
        masked = mask_key("wfabcdef0123456789abcdef")
        assert masked.startswith("wfabcd")
        assert masked.endswith("cdef")
        assert "0123456789" not in masked

    def test_short_key_is_fully_masked(self) -> None:
        assert mask_key("short") == "****"


class TestDataSourceCredentialUpsert:
    def test_put_stores_ciphertext_and_returns_mask_only(self, credential_app: Any) -> None:
        user = _make_user(credential_app.state.factory, "ds_a")

        response = _put(credential_app, user)

        assert response.status_code == 200
        body = response.json()
        assert body["provider"] == "wanfang"
        assert body["configured"] is True
        assert body["status"] == "active"
        assert body["key_mask"] == mask_key(PLAINTEXT)
        # 明文绝不出现在响应里（包括嵌套）
        assert PLAINTEXT not in response.text

        rows = _rows(credential_app.state.factory)
        assert len(rows) == 1
        assert rows[0].api_key_ciphertext != PLAINTEXT
        assert rows[0].api_key_ciphertext.startswith("v1:")
        assert PLAINTEXT not in rows[0].api_key_ciphertext

    def test_put_twice_overwrites_same_row(self, credential_app: Any) -> None:
        user = _make_user(credential_app.state.factory, "ds_b")

        assert _put(credential_app, user).status_code == 200
        first = _rows(credential_app.state.factory)[0]
        first_ciphertext = first.api_key_ciphertext

        response = _put(credential_app, user, api_key=OTHER_PLAINTEXT)

        assert response.status_code == 200
        assert response.json()["key_mask"] == mask_key(OTHER_PLAINTEXT)
        rows = _rows(credential_app.state.factory)
        assert len(rows) == 1
        assert rows[0].api_key_ciphertext != first_ciphertext
        assert _service_call(credential_app.state.factory, user) == OTHER_PLAINTEXT

    def test_decrypted_key_roundtrip(self, credential_app: Any) -> None:
        user = _make_user(credential_app.state.factory, "ds_c")

        _put(credential_app, user)

        assert _service_call(credential_app.state.factory, user) == PLAINTEXT

    def test_unknown_provider_is_rejected(self, credential_app: Any) -> None:
        user = _make_user(credential_app.state.factory, "ds_d")

        response = _put(credential_app, user, provider="not-a-source")

        assert response.status_code == 422
        assert "暂不支持" in response.text
        assert _rows(credential_app.state.factory) == []

    def test_key_with_internal_whitespace_is_rejected(self, credential_app: Any) -> None:
        user = _make_user(credential_app.state.factory, "ds_e")

        response = _put(credential_app, user, api_key="wfabc def0123456789")

        assert response.status_code == 422
        assert "空白" in response.text


class TestDataSourceCredentialRead:
    def test_get_lists_supported_provider_even_when_unconfigured(self, credential_app: Any) -> None:
        user = _make_user(credential_app.state.factory, "ds_f")

        response = credential_app.state.client.get(
            "/api/v1/data-source-credentials", headers=_headers(user)
        )

        assert response.status_code == 200
        body = response.json()
        assert [item["provider"] for item in body] == ["wanfang"]
        assert body[0]["configured"] is False
        assert body[0]["key_mask"] is None

    def test_configured_flag_turns_true_after_put(self, credential_app: Any) -> None:
        user = _make_user(credential_app.state.factory, "ds_g")

        _put(credential_app, user)
        response = credential_app.state.client.get(
            "/api/v1/data-source-credentials", headers=_headers(user)
        )

        body = response.json()
        assert len(body) == 1
        assert body[0]["configured"] is True
        assert body[0]["key_mask"] == mask_key(PLAINTEXT)


class TestDataSourceCredentialIsolation:
    def test_other_user_sees_no_config(self, credential_app: Any) -> None:
        alice = _make_user(credential_app.state.factory, "ds_alice")
        bob = _make_user(credential_app.state.factory, "ds_bob")
        _put(credential_app, alice)

        response = credential_app.state.client.get(
            "/api/v1/data-source-credentials", headers=_headers(bob)
        )

        body = response.json()
        assert body[0]["configured"] is False
        assert _service_call(credential_app.state.factory, bob) is None

    def test_ciphertext_cannot_be_decrypted_by_another_user(self, credential_app: Any) -> None:
        alice = _make_user(credential_app.state.factory, "ds_alice2")
        bob = _make_user(credential_app.state.factory, "ds_bob2")
        _put(credential_app, alice)
        token = _rows(credential_app.state.factory)[0].api_key_ciphertext

        cipher = CredentialCipher.from_settings()
        with pytest.raises(CredentialDecryptError):
            cipher.decrypt(token, user_id=bob.id)

        # 自己的密文仍可解（对照：证明失败原因不是「密钥坏了」）
        assert cipher.decrypt(token, user_id=alice.id) == PLAINTEXT


class TestDataSourceCredentialDelete:
    def test_delete_returns_204_and_clears_key(self, credential_app: Any) -> None:
        user = _make_user(credential_app.state.factory, "ds_h")
        _put(credential_app, user)

        response = credential_app.state.client.delete(
            "/api/v1/data-source-credentials/wanfang", headers=_headers(user)
        )

        assert response.status_code == 204
        assert _rows(credential_app.state.factory) == []
        assert _service_call(credential_app.state.factory, user) is None

    def test_delete_is_idempotent(self, credential_app: Any) -> None:
        user = _make_user(credential_app.state.factory, "ds_i")

        first = credential_app.state.client.delete(
            "/api/v1/data-source-credentials/wanfang", headers=_headers(user)
        )
        second = credential_app.state.client.delete(
            "/api/v1/data-source-credentials/wanfang", headers=_headers(user)
        )

        assert first.status_code == 204
        assert second.status_code == 204


class TestDataSourceCredentialService:
    def test_require_provider_normalizes_case(self) -> None:
        service = DataSourceCredentialService()

        assert service.require_provider("WanFang") == "wanfang"

    def test_upsert_requires_non_empty_key(self, credential_app: Any) -> None:
        user = _make_user(credential_app.state.factory, "ds_j")

        async def _run() -> None:
            async with credential_app.state.factory() as session:
                with pytest.raises(Exception) as excinfo:
                    await DataSourceCredentialService().upsert(session, user.id, "wanfang", "   ")
                assert "不能为空" in str(excinfo.value)

        asyncio.run(_run())

    def test_counts_row_per_user(self, credential_app: Any) -> None:
        alice = _make_user(credential_app.state.factory, "ds_k1")
        bob = _make_user(credential_app.state.factory, "ds_k2")
        _put(credential_app, alice)
        _put(credential_app, bob)

        async def _run() -> int:
            async with credential_app.state.factory() as session:
                result = await session.execute(select(func.count(DataSourceCredential.id)))
                return int(result.scalar_one())

        assert asyncio.run(_run()) == 2
