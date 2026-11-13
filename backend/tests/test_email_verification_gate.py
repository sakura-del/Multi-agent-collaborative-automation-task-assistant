"""邮箱验证门禁（2026-11-13）：未验证的账号不能登录。

## 缺陷（用户实测）

前端 `register()` 注册成功后**自动登录**（stores/auth.ts "注册成功后自动登录"），
后端 `/auth/login` 又不检查 `email_verified_at` → 邮箱没验证就直接进了系统，
验证邮件形同虚设。

## 设计口径

1. **门禁用显式配置** `EMAIL__REQUIRE_VERIFIED_LOGIN`（默认 **False**）：
   项目铁律是「未配置 SMTP 时绝不阻塞用户」——sender=logging 时验证邮件根本发不出去，
   门禁会把本地开发与 CI 全部锁在门外。生产 .env 显式开 true。
2. **未验证登录不计入失败锁定**：用户没输错密码，只是没点邮件；计入失败会把
   正常用户锁 15 分钟（M-3 的机制反噬自己）。
3. **重发端点必须免登录**：旧实现要求 `CurrentUserDep`——未验证用户拿不到 token，
   永远无法重发，死锁。改为按邮箱请求 + 统一模糊响应（防邮箱枚举）。
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.db.models  # noqa: F401  确保 ORM 注册
from app.api.v1.endpoints import auth_lifecycle
from app.db.base import Base
from app.db.session import get_db

API = "/api/v1/auth"

_TOKEN_RE = re.compile(r"token=([A-Za-z0-9_\-]+)")


class FakeEmailSender:
    """记录发信内容的 fake：零真实 SMTP，测试从正文里取 token。"""

    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []

    async def send(self, *, to: str, subject: str, body: str) -> bool:
        self.sent.append({"to": to, "subject": subject, "body": body})
        return True

    def tokens(self) -> list[str]:
        return [m for message in self.sent for m in _TOKEN_RE.findall(message["body"])]

    def to_addresses(self) -> list[str]:
        return [message["to"] for message in self.sent]


@dataclass
class Env:
    client: TestClient
    sender: FakeEmailSender
    session_factory: async_sessionmaker[Any]
    app: FastAPI


def _build_env(tmp_path: Path, *, gate: bool) -> tuple[Env, Any]:
    """真实应用（create_app）+ 临时 SQLite + fake sender。

    注意发信有**两条路径**，都要接管才能捕获全部邮件：
    1. 端点参数注入（``SenderDep`` → Depends(get_email_sender)）→ dependency_overrides；
    2. ``auth.register`` 注册后**直接调用** ``issue_verification_email``（不走 Depends）
       → 内部引用模块级 ``get_cached_email_sender``，必须 setattr 才能拦到。
       既有 test_auth_lifecycle 只覆盖了路径 1——它从未捕获过注册时发的那封。
    """
    import app.api.v1.endpoints.auth_lifecycle as lifecycle
    from app.core.config import settings
    from app.main import create_app

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'gate.db').as_posix()}",
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
    app.dependency_overrides[lifecycle.get_email_sender] = lambda: sender
    original_cached = lifecycle.get_cached_email_sender
    lifecycle.get_cached_email_sender = lambda: sender  # type: ignore[assignment]

    # 门禁开关（§5 #47：patch 的是模块级 settings 对象，不是 get_settings()）
    original_gate = settings.email.require_verified_login
    settings.email.require_verified_login = gate

    client = TestClient(app)
    client.__enter__()
    env = Env(client=client, sender=sender, session_factory=session_factory, app=app)
    return env, (settings, original_gate, lifecycle, original_cached, client, app, engine)


@pytest.fixture
def gated_env(tmp_path: Path) -> Iterator[Env]:
    """门禁开启的环境（模拟生产：SMTP 可用 + REQUIRE_VERIFIED_LOGIN=true）。"""
    env, restore = _build_env(tmp_path, gate=True)
    settings, original_gate, lifecycle, original_cached, client, app, engine = restore
    try:
        yield env
    finally:
        settings.email.require_verified_login = original_gate
        lifecycle.get_cached_email_sender = original_cached  # type: ignore[assignment]
        client.__exit__(None, None, None)
        app.dependency_overrides.clear()
        import asyncio

        asyncio.run(engine.dispose())


@pytest.fixture
def open_env(tmp_path: Path) -> Iterator[Env]:
    """门禁关闭的环境（默认：本地开发 / CI，未配 SMTP）。"""
    env, restore = _build_env(tmp_path, gate=False)
    settings, original_gate, lifecycle, original_cached, client, app, engine = restore
    try:
        yield env
    finally:
        settings.email.require_verified_login = original_gate
        lifecycle.get_cached_email_sender = original_cached  # type: ignore[assignment]
        client.__exit__(None, None, None)
        app.dependency_overrides.clear()
        import asyncio

        asyncio.run(engine.dispose())


def _register(env: Env, username: str = "gateuser") -> None:
    response = env.client.post(
        f"{API}/register",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "password123",
        },
    )
    assert response.status_code == 201, response.text


def _login(env: Env, account: str = "gateuser", password: str = "password123"):
    return env.client.post(f"{API}/login", json={"account": account, "password": password})


def _confirm_with_first_token(env: Env) -> None:
    """从 fake 发信记录里取第一个 token，走真实 confirm 端点完成验证。"""
    tokens = env.sender.tokens()
    assert tokens, "注册后应签发验证邮件"
    response = env.client.post(f"{API}/verify-email/confirm", json={"token": tokens[0]})
    assert response.status_code == 200, response.text


class TestGateEnabled:
    """生产形态：SMTP 可用 + 门禁开 → 未验证不能进系统。"""

    def test_unverified_login_rejected_with_reason(self, gated_env: Env) -> None:
        _register(gated_env)
        response = _login(gated_env)

        assert response.status_code == 403, response.text
        body = response.json()
        assert body.get("error_code") == "EMAIL_NOT_VERIFIED"
        assert (body.get("details") or {}).get("reason") == "email_not_verified"
        # 绝不返回令牌
        assert "access_token" not in body

    def test_gate_does_not_count_as_login_failure(self, gated_env: Env) -> None:
        """未验证 ≠ 密码错误：反复登录不能把用户锁进 M-3 的账号退避。"""
        from app.core.login_throttle import InMemoryLoginThrottle

        # 换成内存实现：本机 Redis 不可达时真实组件 fail-open，断言不了计数
        previous = getattr(gated_env.app.state, "login_throttle", None)
        throttle = InMemoryLoginThrottle(limit=10, window_seconds=900)
        gated_env.app.state.login_throttle = throttle
        try:
            _register(gated_env)
            for _ in range(4):
                assert _login(gated_env).status_code == 403

            import asyncio

            assert asyncio.run(throttle.check("gateuser")) is None, "未验证登录不应累积账号失败计数"
        finally:
            if previous is None:
                del gated_env.app.state.login_throttle
            else:
                gated_env.app.state.login_throttle = previous

    def test_verified_user_can_login(self, gated_env: Env) -> None:
        _register(gated_env)
        _confirm_with_first_token(gated_env)

        response = _login(gated_env)
        assert response.status_code == 200, response.text
        assert "access_token" in response.json()

    def test_resend_request_needs_no_login(self, gated_env: Env) -> None:
        """重发端点免登录——否则未验证用户永远收不到第二封（死锁）。

        注册时那封占用 60 秒冷却（防滥发的正确行为），因此这里把冷却设 0
        单测「免登录 + 真发信」路径；冷却本身由下一个用例测。
        """
        from app.core.config import settings

        original = settings.email.verification_resend_cooldown_seconds
        settings.email.verification_resend_cooldown_seconds = 0
        try:
            _register(gated_env)
            before = len(gated_env.sender.sent)

            response = gated_env.client.post(
                f"{API}/verify-email/request", json={"email": "gateuser@example.com"}
            )
            assert response.status_code == 200, response.text
            assert len(gated_env.sender.sent) == before + 1, "应重发一封验证邮件"
        finally:
            settings.email.verification_resend_cooldown_seconds = original

    def test_resend_response_is_uniform_for_unknown_email(self, gated_env: Env) -> None:
        """防邮箱枚举：不存在 / 已验证 / 未验证三种邮箱返回**同一**消息。"""
        _register(gated_env)  # gateuser@example.com（未验证）

        responses = [
            gated_env.client.post(f"{API}/verify-email/request", json={"email": email}).json()
            for email in (
                "gateuser@example.com",  # 存在且未验证 → 发信
                "who-is-this@example.com",  # 不存在 → 不发
            )
        ]
        # 再把 gateuser 验证掉，请求同一个邮箱 → 已验证，也不发
        _confirm_with_first_token(gated_env)
        verified = gated_env.client.post(
            f"{API}/verify-email/request", json={"email": "gateuser@example.com"}
        ).json()

        messages = {r.get("message") for r in responses} | {verified.get("message")}
        assert len(messages) == 1, f"响应消息不统一，可被用于枚举：{messages}"

        assert gated_env.sender.to_addresses().count("who-is-this@example.com") == 0
        assert (
            gated_env.sender.to_addresses().count("gateuser@example.com") == 1
        ), "已验证后再次请求不应发信"

    def test_resend_is_rate_limited_per_email(self, gated_env: Env) -> None:
        """冷却期内反复请求只发一封；冷却过期后能再发一封。"""
        import asyncio
        from datetime import UTC, datetime, timedelta

        _register(gated_env)
        first = len(gated_env.sender.sent)

        for _ in range(3):
            response = gated_env.client.post(
                f"{API}/verify-email/request", json={"email": "gateuser@example.com"}
            )
            assert response.status_code == 200, response.text
        assert len(gated_env.sender.sent) == first, "冷却期内不应重复发信"

        # 把最近一次签发时间改到冷却之外（DB 时间不好 mock，直接改库——
        # test_auth_lifecycle 对 expires_at 有同样的先例）
        async def backdate() -> None:
            async with gated_env.session_factory() as session:
                await session.execute(
                    sa.text(
                        "UPDATE email_token SET created_at = :past " "WHERE purpose = 'verify'"
                    ),
                    {"past": datetime.now(UTC) - timedelta(hours=1)},
                )
                await session.commit()

        asyncio.run(backdate())

        response = gated_env.client.post(
            f"{API}/verify-email/request", json={"email": "gateuser@example.com"}
        )
        assert response.status_code == 200, response.text
        assert len(gated_env.sender.sent) == first + 1, "冷却过期后应能重发"


class TestGateDisabled:
    """默认形态（本地开发 / CI，未配 SMTP）：门禁关，注册即可登录。"""

    def test_unverified_user_can_login_when_gate_off(self, open_env: Env) -> None:
        _register(open_env)
        response = _login(open_env)
        assert response.status_code == 200, response.text
        assert "access_token" in response.json()

    def test_resend_endpoint_still_uniform_without_gate(self, open_env: Env) -> None:
        from app.core.config import settings

        original = settings.email.verification_resend_cooldown_seconds
        settings.email.verification_resend_cooldown_seconds = 0
        try:
            _register(open_env)
            before = len(open_env.sender.sent)
            response = open_env.client.post(
                f"{API}/verify-email/request", json={"email": "gateuser@example.com"}
            )
            assert response.status_code == 200
            assert len(open_env.sender.sent) == before + 1
        finally:
            settings.email.verification_resend_cooldown_seconds = original
