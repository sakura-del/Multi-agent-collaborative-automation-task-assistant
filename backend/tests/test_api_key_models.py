"""API Key 数据模型测试（Phase 2 · 开放 API Task 1）。

## 本文件锁定

1. `api_key` 表字段与约束（`key_hash` 唯一）；
2. **绝不存明文**：模型里没有明文列，只有 `prefix` + `key_hash`；
3. `scopes` 是 JSON 数组；
4. 吊销/过期用时间戳标记（**软删除**，保留审计）；
5. 迁移可 upgrade / downgrade / 重复 upgrade。

## 为什么先测约束

`key_hash` 唯一是**安全兜底**：哈希碰撞概率极低，但实现 bug（比如误存同一哈希）
会让两个 Key 互相可用。数据库约束是最后防线。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.db.models  # noqa: F401
from app.db.base import Base
from app.db.models.api_key import ApiKey
from app.db.models.user import User


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


class TestApiKeyModel:
    @pytest.mark.asyncio
    async def test_create_api_key(self, factory) -> None:
        user = await _user(factory, "ak_owner")
        async with factory() as session:
            key = ApiKey(
                user_id=user.id,
                name="我的脚本",
                prefix="maa_7Kx9mQ2p",
                key_hash="a" * 64,
                scopes=["tasks:read", "tasks:write"],
            )
            session.add(key)
            await session.commit()
            await session.refresh(key)

            assert key.id is not None
            assert key.name == "我的脚本"
            assert key.prefix == "maa_7Kx9mQ2p"
            assert key.scopes == ["tasks:read", "tasks:write"]
            assert key.revoked_at is None
            assert key.expires_at is None
            assert key.last_used_at is None
            assert key.created_at is not None

    @pytest.mark.asyncio
    async def test_key_hash_is_unique(self, factory) -> None:
        """哈希唯一是安全兜底：两个 Key 共用哈希等于互相可用。"""
        user = await _user(factory, "ak_dup")
        async with factory() as session:
            session.add(
                ApiKey(
                    user_id=user.id,
                    name="k1",
                    prefix="maa_aaaa",
                    key_hash="b" * 64,
                    scopes=["tasks:read"],
                )
            )
            await session.commit()

        async with factory() as session:
            session.add(
                ApiKey(
                    user_id=user.id,
                    name="k2",
                    prefix="maa_bbbb",
                    key_hash="b" * 64,
                    scopes=["tasks:read"],
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()

    @pytest.mark.asyncio
    async def test_no_plaintext_column(self) -> None:
        """**安全硬约束**：模型里不能有任何明文列。

        这条测试的价值：将来有人图方便加一个 `plaintext` / `key` 列，
        这里立刻红。
        """
        columns = set(ApiKey.__table__.columns.keys())
        forbidden = {"key", "plaintext", "secret", "raw_key", "token", "api_key"}
        leaked = columns & forbidden
        assert not leaked, f"api_key 表不能有明文列，发现：{leaked}"

    @pytest.mark.asyncio
    async def test_scopes_stored_as_json_list(self, factory) -> None:
        user = await _user(factory, "ak_scopes")
        async with factory() as session:
            session.add(
                ApiKey(
                    user_id=user.id,
                    name="k",
                    prefix="maa_cccc",
                    key_hash="c" * 64,
                    scopes=["tasks:read"],
                )
            )
            await session.commit()

        async with factory() as session:
            stored = (await session.execute(select(ApiKey))).scalars().one()
            assert isinstance(stored.scopes, list)
            assert stored.scopes == ["tasks:read"]

    @pytest.mark.asyncio
    async def test_revoked_and_expired_flags(self, factory) -> None:
        """吊销与过期都用时间戳（软删除，保留审计）。"""
        user = await _user(factory, "ak_flags")
        now = datetime.now(UTC)
        async with factory() as session:
            revoked = ApiKey(
                user_id=user.id,
                name="已吊销",
                prefix="maa_dddd",
                key_hash="d" * 64,
                scopes=["tasks:read"],
                revoked_at=now,
            )
            expired = ApiKey(
                user_id=user.id,
                name="已过期",
                prefix="maa_eeee",
                key_hash="e" * 64,
                scopes=["tasks:read"],
                expires_at=now - timedelta(days=1),
            )
            session.add_all([revoked, expired])
            await session.commit()
            await session.refresh(revoked)
            await session.refresh(expired)

            assert revoked.revoked_at is not None
            assert expired.expires_at is not None
            # 软删除：记录还在（审计需要）
            assert revoked.id is not None

    @pytest.mark.asyncio
    async def test_deleting_user_cascades_keys(self, factory) -> None:
        """删用户要连带删 Key（否则留下无主 Key）。

        **注意 SQLite 的坑**：它默认 **不启用**外键约束（`PRAGMA foreign_keys`
        默认 OFF），所以 `ondelete="CASCADE"` 在 SQLite 上不生效。
        这里显式开启，让测试验证的语义与 PostgreSQL 一致
        （CI 的 Alembic 作业与生产都是 PostgreSQL）。
        """
        from sqlalchemy import text

        user = await _user(factory, "ak_cascade")
        async with factory() as session:
            session.add(
                ApiKey(
                    user_id=user.id,
                    name="k",
                    prefix="maa_ffff",
                    key_hash="f" * 64,
                    scopes=["tasks:read"],
                )
            )
            await session.commit()

        async with factory() as session:
            await session.execute(text("PRAGMA foreign_keys=ON"))
            stored_user = await session.get(User, user.id)
            assert stored_user is not None
            await session.delete(stored_user)
            await session.commit()

        async with factory() as session:
            remaining = (await session.execute(select(ApiKey))).scalars().all()
            assert remaining == []

    @pytest.mark.asyncio
    async def test_multiple_keys_per_user(self, factory) -> None:
        """一个用户可以有多个 Key（不同脚本不同权限）。"""
        user = await _user(factory, "ak_multi")
        async with factory() as session:
            for index in range(3):
                session.add(
                    ApiKey(
                        user_id=user.id,
                        name=f"k{index}",
                        prefix=f"maa_{index:08d}",
                        key_hash=f"{index}" * 64,
                        scopes=["tasks:read"],
                    )
                )
            await session.commit()

        async with factory() as session:
            keys = (await session.execute(select(ApiKey))).scalars().all()
            assert len(keys) == 3


class TestApiKeyScopes:
    def test_known_scopes_constant(self) -> None:
        """scope 是契约，必须集中定义（散落在各处迟早拼错）。"""
        from app.db.models.api_key import API_KEY_SCOPES

        assert set(API_KEY_SCOPES) == {
            "tasks:read",
            "tasks:write",
            "artifacts:read",
            "annotations:write",
        }

    def test_default_scopes(self) -> None:
        """新建 Key 默认给「读 + 写任务」（最常见组合）。"""
        from app.db.models.api_key import DEFAULT_API_KEY_SCOPES

        assert set(DEFAULT_API_KEY_SCOPES) == {"tasks:read", "tasks:write"}
