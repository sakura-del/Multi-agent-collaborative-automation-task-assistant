"""P1-C 账号体系（邮箱验证 / 密码重置）契约测试（计划 2026-10-20-00 §1.3）。

覆盖：
- 邮箱验证：申请 → 邮件（fake sender 捕获 token）→ 确认 → `email_verified_at` 落库；
  单次使用、过期拒绝、未登录 401、已验证不重发；
- 密码重置：**恒定响应**（存在/不存在账号响应逐字节相同）、重置成功后旧密码失效、
  旧 access_token 失效（`token_invalid_before`）、单次使用、弱密码拒绝、用途隔离；
- 令牌安全：**只存哈希**（库里无明文）、TTL 默认 24h；
- Alembic 迁移：链头唯一、SQLite 上升级建列+建表、downgrade 完整回滚、可往返。

铁律：零真实 SMTP / 零真实 PostgreSQL（TestClient + 临时文件型 SQLite；发信全部走 fake）。
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.db.models  # noqa: F401  确保既有 ORM 注册到 Base.metadata
from app.api.v1.endpoints import auth_lifecycle
from app.db.base import Base
from app.db.models.email_token import EmailToken  # noqa: F401  注册 email_token 表
from app.db.models.user import User
from app.db.session import get_db

API = "/api/v1/auth"
BACKEND_DIR = Path(__file__).resolve().parent.parent
VERSIONS_DIR = BACKEND_DIR / "alembic" / "versions"
PREVIOUS_HEAD = "a4c9e1d7b2f8"

USERNAME = "alice"
EMAIL = "alice@example.com"
PASSWORD = "password123"
NEW_PASSWORD = "brand-new-password-456"

_TOKEN_RE = re.compile(r"token=([A-Za-z0-9_\-]+)")


# ===== 测试环境 =====


class FakeEmailSender:
    """记录发信内容的 fake：零真实 SMTP，测试从正文里取 token。"""

    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []

    async def send(self, *, to: str, subject: str, body: str) -> bool:
        self.sent.append({"to": to, "subject": subject, "body": body})
        return True

    @property
    def last(self) -> dict[str, str]:
        assert self.sent, "期望至少发送过一封邮件"
        return self.sent[-1]

    def tokens(self) -> list[str]:
        return [match for message in self.sent for match in _TOKEN_RE.findall(message["body"])]


@dataclass
class Env:
    client: TestClient
    sender: FakeEmailSender
    session_factory: async_sessionmaker[Any]
    app: FastAPI


@pytest.fixture
def env(tmp_path: Path) -> Iterator[Env]:
    """真实应用（create_app）+ 临时 SQLite；只覆盖 DB 与邮件发送器两个依赖。"""
    from app.main import create_app

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'lifecycle.db').as_posix()}",
        connect_args={"timeout": 10},
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    state = {"created": False}

    async def override_get_db() -> Any:
        if not state["created"]:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            state["created"] = True
        async with session_factory() as session:
            yield session

    sender = FakeEmailSender()
    app: FastAPI = create_app()
    app.include_router(auth_lifecycle.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[auth_lifecycle.get_email_sender] = lambda: sender

    client = TestClient(app)
    client.__enter__()
    try:
        yield Env(client=client, sender=sender, session_factory=session_factory, app=app)
    finally:
        client.__exit__(None, None, None)
        app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


# ===== 工具 =====


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register(
    client: TestClient,
    *,
    username: str = USERNAME,
    email: str = EMAIL,
    password: str = PASSWORD,
) -> None:
    resp = client.post(
        f"{API}/register",
        json={"username": username, "email": email, "password": password},
    )
    assert resp.status_code == 201, resp.text


def _login(client: TestClient, *, account: str = USERNAME, password: str = PASSWORD) -> str:
    resp = client.post(f"{API}/login", json={"account": account, "password": password})
    assert resp.status_code == 200, resp.text
    return str(resp.json()["access_token"])


def _register_and_login(client: TestClient) -> str:
    _register(client)
    return _login(client)


def _rows(env: Env, sql: str, **params: Any) -> list[dict[str, Any]]:
    """在 TestClient 的事件循环里读库（避免跨 loop 使用 aiosqlite 连接）。"""

    async def run() -> list[dict[str, Any]]:
        async with env.session_factory() as session:
            result = await session.execute(text(sql), params)
            return [dict(row) for row in result.mappings()]

    return env.client.portal.call(run)


def _scalar(env: Env, sql: str, **params: Any) -> Any:
    rows = _rows(env, sql, **params)
    assert rows, f"期望至少一行：{sql}"
    return next(iter(rows[0].values()))


def _execute(env: Env, sql: str, **params: Any) -> None:
    async def run() -> None:
        async with env.session_factory() as session:
            await session.execute(text(sql), params)
            await session.commit()

    env.client.portal.call(run)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _user_row(env: Env, email: str = EMAIL) -> dict[str, Any]:
    """在 TestClient 事件循环里用 ORM 读取用户关键字段。"""

    async def run() -> dict[str, Any]:
        async with env.session_factory() as session:
            user = await session.scalar(sa.select(User).where(User.email == email))
            assert user is not None, f"用户不存在：{email}"
            return {
                "email_verified_at": user.email_verified_at,
                "token_invalid_before": user.token_invalid_before,
                "is_active": user.is_active,
                "password_hash": user.password_hash,
            }

    return env.client.portal.call(run)


# ===== 邮箱验证 =====


class TestVerifyEmail:
    def test_request_then_confirm_marks_email_verified(self, env: Env) -> None:
        access = _register_and_login(env.client)

        requested = env.client.post(f"{API}/verify-email/request", headers=_auth(access))

        assert requested.status_code == 200, requested.text
        assert len(env.sender.sent) == 1
        assert env.sender.last["to"] == EMAIL
        assert "验证" in env.sender.last["subject"]
        assert "token=" in env.sender.last["body"]

        token = env.sender.tokens()[0]
        confirmed = env.client.post(f"{API}/verify-email/confirm", json={"token": token})

        assert confirmed.status_code == 200, confirmed.text
        assert "成功" in confirmed.json()["message"]
        assert _user_row(env)["email_verified_at"] is not None
        used_at = _scalar(env, "SELECT used_at FROM email_token WHERE purpose = 'verify'")
        assert used_at is not None

    def test_confirm_token_is_single_use(self, env: Env) -> None:
        access = _register_and_login(env.client)
        env.client.post(f"{API}/verify-email/request", headers=_auth(access))
        token = env.sender.tokens()[0]

        first = env.client.post(f"{API}/verify-email/confirm", json={"token": token})
        second = env.client.post(f"{API}/verify-email/confirm", json={"token": token})

        assert first.status_code == 200, first.text
        assert second.status_code == 422, second.text
        assert "使用" in second.json()["message"]

    def test_confirm_rejects_unknown_token(self, env: Env) -> None:
        resp = env.client.post(f"{API}/verify-email/confirm", json={"token": "x" * 32})

        assert resp.status_code == 422
        assert "无效" in resp.json()["message"] or "过期" in resp.json()["message"]

    def test_confirm_rejects_expired_token(self, env: Env) -> None:
        access = _register_and_login(env.client)
        env.client.post(f"{API}/verify-email/request", headers=_auth(access))
        token = env.sender.tokens()[0]
        _execute(
            env,
            "UPDATE email_token SET expires_at = '2000-01-01 00:00:00' WHERE purpose = 'verify'",
        )

        resp = env.client.post(f"{API}/verify-email/confirm", json={"token": token})

        assert resp.status_code == 422, resp.text
        assert "过期" in resp.json()["message"]

    def test_request_requires_authentication(self, env: Env) -> None:
        assert env.client.post(f"{API}/verify-email/request").status_code == 401

    def test_request_when_already_verified_does_not_resend(self, env: Env) -> None:
        access = _register_and_login(env.client)
        env.client.post(f"{API}/verify-email/request", headers=_auth(access))
        env.client.post(f"{API}/verify-email/confirm", json={"token": env.sender.tokens()[0]})
        sent_before = len(env.sender.sent)

        again = env.client.post(f"{API}/verify-email/request", headers=_auth(access))

        assert again.status_code == 200, again.text
        assert "已验证" in again.json()["message"]
        assert len(env.sender.sent) == sent_before

    def test_verify_token_ttl_defaults_to_24_hours(self, env: Env) -> None:
        access = _register_and_login(env.client)
        env.client.post(f"{API}/verify-email/request", headers=_auth(access))

        hours = _scalar(
            env,
            "SELECT (julianday(expires_at) - julianday(created_at)) * 24.0 "
            "FROM email_token WHERE purpose = 'verify'",
        )

        assert 23.5 <= float(hours) <= 24.5

    def test_token_is_stored_as_hash_only(self, env: Env) -> None:
        access = _register_and_login(env.client)
        env.client.post(f"{API}/verify-email/request", headers=_auth(access))
        # ⚠️ 注册时已自动发过一封验证邮件（Phase 1 P1-C 接线），重发会作废旧令牌；
        # 因此取**最后一封**（最新），并查「仍未被消费」的那一行（旧行是作废状态）。
        token = env.sender.tokens()[-1]

        stored = _scalar(
            env,
            "SELECT token_hash FROM email_token " "WHERE purpose = 'verify' AND used_at IS NULL",
        )

        assert stored == _hash(token)
        assert token not in str(stored)
        assert len(str(stored)) == 64
        # 整张表都不得出现明文 token（含已作废的历史行）
        everything = _rows(env, "SELECT * FROM email_token")
        assert all(token not in str(value) for row in everything for value in row.values())


# ===== 密码重置 =====


class TestPasswordReset:
    def test_reset_request_response_is_constant(self, env: Env) -> None:
        """存在 / 不存在的账号必须返回逐字节相同的响应（防账号枚举）。"""
        _register(env.client)

        existing = env.client.post(f"{API}/password-reset/request", json={"email": EMAIL})
        unknown = env.client.post(
            f"{API}/password-reset/request", json={"email": "ghost@example.com"}
        )

        assert existing.status_code == unknown.status_code == 200
        assert existing.json() == unknown.json()
        assert "已发送" in existing.json()["message"]
        # 只有真实账号才真的发信
        assert len(env.sender.sent) == 1
        assert env.sender.last["to"] == EMAIL

    def test_reset_flow_changes_password_and_invalidates_old_tokens(self, env: Env) -> None:
        old_access = _register_and_login(env.client)
        assert env.client.get(f"{API}/me", headers=_auth(old_access)).status_code == 200

        env.client.post(f"{API}/password-reset/request", json={"email": EMAIL})
        token = env.sender.tokens()[0]
        confirmed = env.client.post(
            f"{API}/password-reset/confirm",
            json={"token": token, "new_password": NEW_PASSWORD},
        )

        assert confirmed.status_code == 200, confirmed.text
        # 旧密码失效、新密码可用
        assert (
            env.client.post(
                f"{API}/login", json={"account": USERNAME, "password": PASSWORD}
            ).status_code
            == 401
        )
        _login(env.client, password=NEW_PASSWORD)
        # 旧 access_token 立即失效（token_invalid_before）
        assert env.client.get(f"{API}/me", headers=_auth(old_access)).status_code == 401

        # token_invalid_before 写入的是 UTC 时刻；SQLite 读回是 naive，
        # 必须按 UTC 解释（用 utc_timestamp，与 deps 的消费口径一致）。
        # ⚠️ 不要用 `invalid_before.timestamp()`：那会把 naive 按**本地时区**解释，
        # 在 UTC+8 机器上偏移 28800 秒（正是本用例曾暴露的缺陷形态）。
        from app.core.security import utc_timestamp

        invalid_before = _user_row(env)["token_invalid_before"]
        assert invalid_before is not None
        assert abs(utc_timestamp(invalid_before) - time.time()) < 300

    def test_reset_token_is_single_use(self, env: Env) -> None:
        _register(env.client)
        env.client.post(f"{API}/password-reset/request", json={"email": EMAIL})
        token = env.sender.tokens()[0]

        first = env.client.post(
            f"{API}/password-reset/confirm",
            json={"token": token, "new_password": NEW_PASSWORD},
        )
        second = env.client.post(
            f"{API}/password-reset/confirm",
            json={"token": token, "new_password": "another-password-789"},
        )

        assert first.status_code == 200, first.text
        assert second.status_code == 422, second.text
        _login(env.client, password=NEW_PASSWORD)

    def test_reset_confirm_rejects_weak_password(self, env: Env) -> None:
        _register(env.client)
        env.client.post(f"{API}/password-reset/request", json={"email": EMAIL})
        token = env.sender.tokens()[0]

        resp = env.client.post(
            f"{API}/password-reset/confirm", json={"token": token, "new_password": "short"}
        )

        assert resp.status_code == 422  # pydantic 校验：与注册密码同规则（≥8）
        _login(env.client)  # 原密码仍然可用（未被改动）

    def test_reset_confirm_rejects_unknown_token(self, env: Env) -> None:
        resp = env.client.post(
            f"{API}/password-reset/confirm",
            json={"token": "y" * 32, "new_password": NEW_PASSWORD},
        )

        assert resp.status_code == 422

    def test_verify_token_cannot_be_used_for_password_reset(self, env: Env) -> None:
        """用途隔离：验证 token 不能当重置 token 用（purpose 必须匹配）。"""
        access = _register_and_login(env.client)
        env.client.post(f"{API}/verify-email/request", headers=_auth(access))
        verify_token = env.sender.tokens()[0]

        resp = env.client.post(
            f"{API}/password-reset/confirm",
            json={"token": verify_token, "new_password": NEW_PASSWORD},
        )

        assert resp.status_code == 422
        _login(env.client)  # 密码未被改动

    def test_reset_request_for_inactive_user_is_constant_and_sends_nothing(self, env: Env) -> None:
        _register(env.client)
        active_response = env.client.post(f"{API}/password-reset/request", json={"email": EMAIL})
        assert active_response.status_code == 200
        sent = len(env.sender.sent)

        _execute(env, 'UPDATE "user" SET is_active = 0 WHERE email = :email', email=EMAIL)
        inactive = env.client.post(f"{API}/password-reset/request", json={"email": EMAIL})

        assert inactive.status_code == 200
        assert inactive.json() == active_response.json()  # 恒定响应
        assert len(env.sender.sent) == sent  # 禁用账号不发信


# ===== Alembic 迁移 =====


def _load_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"p1c_migration_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migration_paths() -> list[Path]:
    return sorted(VERSIONS_DIR.glob("*.py"))


def _lifecycle_migration() -> ModuleType:
    matches = sorted(VERSIONS_DIR.glob("*_add_email_lifecycle.py"))
    assert len(matches) == 1, f"期望恰好一个账号体系迁移，实际 {matches}"
    return _load_module(matches[0])


def _create_pre_migration_user_table(connection: Any) -> None:
    """建「迁移前」的 user 表（不含 email_verified_at）。

    迁移测试必须针对**上一版 schema**：直接用当前 ORM `User.__table__` 建表会已包含
    新字段，导致 `ADD COLUMN` 重复。这里用最小 DDL 模拟旧表结构。
    """
    connection.exec_driver_sql(
        'CREATE TABLE "user" ('
        "id CHAR(32) NOT NULL PRIMARY KEY, "
        "username VARCHAR(64) NOT NULL, "
        "email VARCHAR(128) NOT NULL, "
        "password_hash VARCHAR(128) NOT NULL, "
        "role VARCHAR(16) NOT NULL, "
        "is_active BOOLEAN NOT NULL, "
        "token_invalid_before DATETIME, "
        "created_at DATETIME NOT NULL, "
        "updated_at DATETIME NOT NULL)"
    )


class TestEmailLifecycleMigration:
    """迁移必须在 SQLite 与 PostgreSQL 双方言可跑；downgrade 完整可回滚。"""

    def test_revision_follows_previous_head_and_is_unique_head(self) -> None:
        migration = _lifecycle_migration()

        assert migration.down_revision == PREVIOUS_HEAD
        assert isinstance(migration.revision, str) and migration.revision

        revisions: list[str] = []
        down_revisions: list[str] = []
        for path in _migration_paths():
            module = _load_module(path)
            revisions.append(module.revision)
            if module.down_revision is not None:
                down_revisions.append(module.down_revision)

        referenced = set(down_revisions)
        heads = [revision for revision in revisions if revision not in referenced]
        # 迁移链必须**无分叉**（恰好一个 head）。注意：后续新增迁移会把 head 前移，
        # 因此断言「唯一 head 存在」而不是「head 就是本迁移」——否则每加一版迁移都会误报。
        assert len(heads) == 1, f"迁移链存在分叉（多个 head）：{heads}"
        assert migration.revision in revisions

    def test_upgrade_adds_user_column_and_email_token_table_on_sqlite(self, tmp_path: Path) -> None:
        migration = _lifecycle_migration()
        engine = sa.create_engine(f"sqlite:///{(tmp_path / 'upgrade.db').as_posix()}")
        try:
            with engine.begin() as connection:
                _create_pre_migration_user_table(connection)  # 迁移前提：旧版 user 表已存在
                context = MigrationContext.configure(connection)
                with Operations.context(context):
                    migration.upgrade()

                inspector = sa.inspect(connection)
                user_columns = {column["name"]: column for column in inspector.get_columns("user")}
                assert "email_verified_at" in user_columns
                assert user_columns["email_verified_at"]["nullable"] is True

                assert "email_token" in inspector.get_table_names()
                token_columns = {column["name"] for column in inspector.get_columns("email_token")}
                assert {
                    "id",
                    "user_id",
                    "purpose",
                    "token_hash",
                    "expires_at",
                    "used_at",
                    "created_at",
                } <= token_columns

                unique_constraints = {
                    constraint["name"]
                    for constraint in inspector.get_unique_constraints("email_token")
                }
                assert "uq_email_token_token_hash" in unique_constraints
                index_names = {index["name"] for index in inspector.get_indexes("email_token")}
                assert "ix_email_token_user_id" in index_names

                ddl = connection.exec_driver_sql(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='email_token'"
                ).scalar_one()
                assert "REFERENCES user" in ddl
                assert "ON DELETE CASCADE" in ddl
        finally:
            engine.dispose()

    def test_downgrade_rolls_back_completely(self, tmp_path: Path) -> None:
        migration = _lifecycle_migration()
        engine = sa.create_engine(f"sqlite:///{(tmp_path / 'roundtrip.db').as_posix()}")
        try:
            with engine.begin() as connection:
                _create_pre_migration_user_table(connection)
                context = MigrationContext.configure(connection)
                with Operations.context(context):
                    migration.upgrade()
                assert "email_token" in sa.inspect(connection).get_table_names()

                with Operations.context(context):
                    migration.downgrade()

                inspector = sa.inspect(connection)
                assert "email_token" not in inspector.get_table_names()
                user_columns = {column["name"] for column in inspector.get_columns("user")}
                assert "email_verified_at" not in user_columns
                # 原有 user 表结构必须完好（username/email/password_hash 仍在）
                assert {"username", "email", "password_hash"} <= user_columns
                leftover = connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE tbl_name='email_token'"
                ).all()
                assert leftover == []
        finally:
            engine.dispose()

    def test_upgrade_downgrade_upgrade_round_trip(self, tmp_path: Path) -> None:
        migration = _lifecycle_migration()
        engine = sa.create_engine(f"sqlite:///{(tmp_path / 'twice.db').as_posix()}")
        try:
            with engine.begin() as connection:
                _create_pre_migration_user_table(connection)
                context = MigrationContext.configure(connection)
                for _ in range(2):
                    with Operations.context(context):
                        migration.upgrade()
                    assert "email_token" in sa.inspect(connection).get_table_names()
                    with Operations.context(context):
                        migration.downgrade()
                    assert "email_token" not in sa.inspect(connection).get_table_names()
        finally:
            engine.dispose()
