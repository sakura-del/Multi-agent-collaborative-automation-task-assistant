"""API Key 服务测试（Phase 2 · 开放 API Task 2）。

## 本文件锁定

1. **明文只返回一次**：`create` 返回 `(record, plaintext)`，之后任何读取路径
   都拿不到明文；
2. **哈希校验**：`authenticate(plaintext)` 能找回记录，错误明文返回 None；
3. **失效判定**：吊销 / 过期 / 不存在 → 都返回 None（**不区分**，避免探测）；
4. **scope 判定**：`has_scope` 精确匹配；
5. **`last_used_at` 节流**：同一 Key 短时间内多次调用只写一次库
   （否则每个 API 请求一次 UPDATE，是真实的性能负担）；
6. **吊销幂等**：重复吊销不报错。

## 为什么「失效不区分原因」

返回「Key 已吊销」vs「Key 不存在」等于告诉攻击者「这个 Key 曾经有效」。
统一返回 401 且不解释，是标准做法（与登录「用户名或密码错误」同理）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.db.models  # noqa: F401
from app.db.base import Base
from app.db.models.api_key import API_KEY_PREFIX, ApiKey
from app.db.models.user import User
from app.security.api_key_service import ApiKeyService


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


async def _user(factory, name: str) -> User:
    async with factory() as session:
        user = User(username=name, email=f"{name}@example.com", password_hash="x", is_active=True)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


class TestCreate:
    @pytest.mark.asyncio
    async def test_returns_plaintext_once(self, factory) -> None:
        user = await _user(factory, "s_owner")
        service = ApiKeyService(factory)

        record, plaintext = await service.create(user.id, name="我的脚本")

        assert plaintext.startswith(API_KEY_PREFIX)
        assert len(plaintext) > 30, "明文必须够长（熵不足可被猜）"
        # 记录里只有前缀与哈希，没有明文
        assert record.prefix == plaintext[: len(record.prefix)]
        assert record.key_hash != plaintext
        assert plaintext not in record.key_hash

    @pytest.mark.asyncio
    async def test_default_scopes(self, factory) -> None:
        user = await _user(factory, "s_default")
        service = ApiKeyService(factory)

        record, _ = await service.create(user.id, name="k")

        assert set(record.scopes) == {"tasks:read", "tasks:write"}

    @pytest.mark.asyncio
    async def test_custom_scopes(self, factory) -> None:
        user = await _user(factory, "s_custom")
        service = ApiKeyService(factory)

        record, _ = await service.create(user.id, name="只读脚本", scopes=["tasks:read"])

        assert record.scopes == ["tasks:read"]

    @pytest.mark.asyncio
    async def test_invalid_scope_rejected(self, factory) -> None:
        user = await _user(factory, "s_bad")
        service = ApiKeyService(factory)

        with pytest.raises(ValueError, match="未知 scope"):
            await service.create(user.id, name="k", scopes=["tasks:nonsense"])

    @pytest.mark.asyncio
    async def test_empty_scopes_rejected(self, factory) -> None:
        """空 scope 的 Key 什么都不能做，建了只会让用户困惑。"""
        user = await _user(factory, "s_empty")
        service = ApiKeyService(factory)

        with pytest.raises(ValueError, match="至少"):
            await service.create(user.id, name="k", scopes=[])

    @pytest.mark.asyncio
    async def test_expires_at(self, factory) -> None:
        user = await _user(factory, "s_exp")
        service = ApiKeyService(factory)

        record, _ = await service.create(user.id, name="k", expires_in_days=90)

        assert record.expires_at is not None
        expires = record.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        delta = expires - datetime.now(UTC)
        assert timedelta(days=89) < delta < timedelta(days=91)

    @pytest.mark.asyncio
    async def test_plaintext_is_unique_per_key(self, factory) -> None:
        user = await _user(factory, "s_uniq")
        service = ApiKeyService(factory)

        _, first = await service.create(user.id, name="k1")
        _, second = await service.create(user.id, name="k2")

        assert first != second


class TestAuthenticate:
    @pytest.mark.asyncio
    async def test_valid_key_returns_record_and_user(self, factory) -> None:
        user = await _user(factory, "a_owner")
        service = ApiKeyService(factory)
        record, plaintext = await service.create(user.id, name="k")

        result = await service.authenticate(plaintext)

        assert result is not None
        found_record, found_user = result
        assert found_record.id == record.id
        assert found_user.id == user.id

    @pytest.mark.asyncio
    async def test_wrong_key_returns_none(self, factory) -> None:
        user = await _user(factory, "a_wrong")
        service = ApiKeyService(factory)
        await service.create(user.id, name="k")

        assert await service.authenticate("maa_thisdoesnotexist000000000000") is None

    @pytest.mark.asyncio
    async def test_empty_or_garbage_returns_none(self, factory) -> None:
        service = ApiKeyService(factory)

        assert await service.authenticate("") is None
        assert await service.authenticate("   ") is None
        assert await service.authenticate("not-a-key") is None

    @pytest.mark.asyncio
    async def test_revoked_key_returns_none(self, factory) -> None:
        user = await _user(factory, "a_rev")
        service = ApiKeyService(factory)
        record, plaintext = await service.create(user.id, name="k")
        await service.revoke(user.id, record.id)

        assert await service.authenticate(plaintext) is None

    @pytest.mark.asyncio
    async def test_expired_key_returns_none(self, factory) -> None:
        user = await _user(factory, "a_exp")
        service = ApiKeyService(factory)
        record, plaintext = await service.create(user.id, name="k")
        # 手动改成昨天过期
        async with factory() as session:
            stored = await session.get(ApiKey, record.id)
            assert stored is not None
            stored.expires_at = datetime.now(UTC) - timedelta(days=1)
            await session.commit()

        assert await service.authenticate(plaintext) is None

    @pytest.mark.asyncio
    async def test_inactive_user_key_returns_none(self, factory) -> None:
        """用户被禁用后，其 Key 也立即失效（否则禁用形同虚设）。"""
        user = await _user(factory, "a_inactive")
        service = ApiKeyService(factory)
        _, plaintext = await service.create(user.id, name="k")

        async with factory() as session:
            stored = await session.get(User, user.id)
            assert stored is not None
            stored.is_active = False
            await session.commit()

        assert await service.authenticate(plaintext) is None

    @pytest.mark.asyncio
    async def test_updates_last_used_at(self, factory) -> None:
        user = await _user(factory, "a_used")
        service = ApiKeyService(factory)
        record, plaintext = await service.create(user.id, name="k")
        assert record.last_used_at is None

        await service.authenticate(plaintext)

        async with factory() as session:
            stored = await session.get(ApiKey, record.id)
            assert stored is not None
            assert stored.last_used_at is not None

    @pytest.mark.asyncio
    async def test_last_used_at_is_throttled(self, factory) -> None:
        """**节流**：短时间内多次认证只写一次库。

        不节流的代价：每个 API 请求一次 UPDATE，高并发下是真实的写放大。
        """
        user = await _user(factory, "a_throttle")
        service = ApiKeyService(factory)
        record, plaintext = await service.create(user.id, name="k")

        await service.authenticate(plaintext)
        async with factory() as session:
            stored = await session.get(ApiKey, record.id)
            assert stored is not None
            first_used = stored.last_used_at
        assert first_used is not None

        # 立刻再认证 3 次
        for _ in range(3):
            await service.authenticate(plaintext)

        async with factory() as session:
            stored = await session.get(ApiKey, record.id)
            assert stored is not None
            # 节流窗口内不应更新（时间戳保持不变）
            assert stored.last_used_at == first_used


class TestScopes:
    @pytest.mark.asyncio
    async def test_has_scope_exact_match(self, factory) -> None:
        user = await _user(factory, "sc_owner")
        service = ApiKeyService(factory)
        record, _ = await service.create(user.id, name="k", scopes=["tasks:read"])

        assert service.has_scope(record, "tasks:read") is True
        assert service.has_scope(record, "tasks:write") is False
        assert service.has_scope(record, "artifacts:read") is False

    @pytest.mark.asyncio
    async def test_has_scope_with_multiple(self, factory) -> None:
        user = await _user(factory, "sc_multi")
        service = ApiKeyService(factory)
        record, _ = await service.create(user.id, name="k", scopes=["tasks:read", "artifacts:read"])

        assert service.has_scope(record, "tasks:read") is True
        assert service.has_scope(record, "artifacts:read") is True
        assert service.has_scope(record, "tasks:write") is False


class TestListAndRevoke:
    @pytest.mark.asyncio
    async def test_list_for_user_excludes_revoked(self, factory) -> None:
        """列表默认不含已吊销的（但数据库里还在，审计用）。"""
        user = await _user(factory, "l_owner")
        service = ApiKeyService(factory)
        alive, _ = await service.create(user.id, name="活的")
        dead, _ = await service.create(user.id, name="死的")
        await service.revoke(user.id, dead.id)

        listed = await service.list_for_user(user.id)

        assert [item.id for item in listed] == [alive.id]

    @pytest.mark.asyncio
    async def test_list_includes_revoked_when_requested(self, factory) -> None:
        user = await _user(factory, "l_all")
        service = ApiKeyService(factory)
        _, _ = await service.create(user.id, name="活的")
        dead, _ = await service.create(user.id, name="死的")
        await service.revoke(user.id, dead.id)

        listed = await service.list_for_user(user.id, include_revoked=True)

        assert len(listed) == 2

    @pytest.mark.asyncio
    async def test_list_only_my_keys(self, factory) -> None:
        alice = await _user(factory, "l_alice")
        bob = await _user(factory, "l_bob")
        service = ApiKeyService(factory)
        await service.create(alice.id, name="alice 的")
        await service.create(bob.id, name="bob 的")

        alice_keys = await service.list_for_user(alice.id)

        assert len(alice_keys) == 1
        assert alice_keys[0].name == "alice 的"

    @pytest.mark.asyncio
    async def test_revoke_is_idempotent(self, factory) -> None:
        user = await _user(factory, "r_owner")
        service = ApiKeyService(factory)
        record, _ = await service.create(user.id, name="k")

        await service.revoke(user.id, record.id)
        await service.revoke(user.id, record.id)  # 不应抛异常

        async with factory() as session:
            stored = await session.get(ApiKey, record.id)
            assert stored is not None
            assert stored.revoked_at is not None

    @pytest.mark.asyncio
    async def test_cannot_revoke_others_key(self, factory) -> None:
        alice = await _user(factory, "r_alice")
        bob = await _user(factory, "r_bob")
        service = ApiKeyService(factory)
        record, plaintext = await service.create(alice.id, name="alice 的")

        with pytest.raises(ValueError, match="不存在"):
            await service.revoke(bob.id, record.id)

        # alice 的 Key 仍然有效
        assert await service.authenticate(plaintext) is not None

    @pytest.mark.asyncio
    async def test_revoke_unknown_key(self, factory) -> None:
        user = await _user(factory, "r_unknown")
        service = ApiKeyService(factory)

        with pytest.raises(ValueError, match="不存在"):
            await service.revoke(user.id, uuid.uuid4())


class TestHashStorage:
    @pytest.mark.asyncio
    async def test_database_stores_only_hash(self, factory) -> None:
        """**安全硬断言**：直接读库，确认明文不在任何列里。"""
        user = await _user(factory, "h_owner")
        service = ApiKeyService(factory)
        _, plaintext = await service.create(user.id, name="k")

        async with factory() as session:
            rows = (await session.execute(select(ApiKey))).scalars().all()
            assert len(rows) == 1
            stored = rows[0]
            # 逐列检查
            for column in ApiKey.__table__.columns:
                value = getattr(stored, column.key, None)
                if isinstance(value, str):
                    assert plaintext not in value, f"明文泄漏在列 {column.key}"
            # 哈希必须是 sha256 hex（64 字符）
            assert len(stored.key_hash) == 64
            assert all(c in "0123456789abcdef" for c in stored.key_hash)
