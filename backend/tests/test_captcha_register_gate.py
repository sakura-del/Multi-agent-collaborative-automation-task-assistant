"""注册人机验证（ALTCHA 自托管，2026-11-13）。

## 为什么选 ALTCHA

- Cloudflare Turnstile 官方声明 **不支持中国大陆**（实测大陆探针 60s 加载不完），
  reCAPTCHA 的 Google 域名更不可用——面向中文用户的注册页不能用它们；
- ALTCHA 是 MIT 开源自托管 PoW：挑战生成/校验全在本后端，无第三方请求、
  无跨境数据传输、无配额与舆情风险；
- 与既有防线**叠加**而非替代：IP 限流（5 次/时）+ 账号锁定 + 邮箱验证门禁 + PoW。

## 设计口径

1. **默认关闭** `CAPTCHA__ENABLED=false`：本地开发/CI 不该被验证码挡住
   （与「未配 SMTP 不阻塞」同一条铁律）；生产 .env 显式开启。
2. **挑战无状态**：HMAC 签名，不落库；`data` 里嵌**客户端 IP 指纹**防「一处解、到处用」。
3. **一次性**：ALTCHA 的 payload 可被重复验证（库只验数学正确性），
   所以服务端按 `nonce` 记录已用（Redis SET NX EX，TTL = 挑战有效期），
   Redis 不可用 → fail-open（不能因为缓存挂了就没人能注册）。
4. **挑战端点免登录**：注册者本来就没有账号；生成挑战是纯 HMAC 计算，
   无副作用，被刷也不产生费用（不像发信）。
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.db.models  # noqa: F401
from app.api.v1.endpoints import auth_lifecycle
from app.db.base import Base
from app.db.session import get_db

API = "/api/v1/auth"
SECRET = "test-captcha-hmac-secret"
_TOKEN_RE = re.compile(r"token=([A-Za-z0-9_\-]+)")


class FakeEmailSender:
    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []

    async def send(self, *, to: str, subject: str, body: str) -> bool:
        self.sent.append({"to": to, "subject": subject, "body": body})
        return True


@dataclass
class Env:
    client: TestClient
    sender: FakeEmailSender


def _build_env(tmp_path: Path, *, enabled: bool) -> tuple[Env, Any]:
    import app.api.v1.endpoints.auth_lifecycle as lifecycle
    from app.core.config import settings
    from app.main import create_app

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'captcha.db').as_posix()}",
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

    original = (
        settings.captcha.enabled,
        settings.captcha.hmac_secret,
        settings.captcha.cost,
        settings.captcha.ttl_seconds,
    )
    settings.captcha.enabled = enabled
    settings.captcha.hmac_secret = SECRET
    settings.captcha.cost = 500  # 测试里解得快
    settings.captcha.ttl_seconds = 600
    # 注入内存版 nonce 存储：测试机 Redis 不可达，真实组件会 fail-open（设计如此），
    # 那样「重放检测」永远测不到。这里显式换成可控实现。
    from app.core.captcha import CaptchaService, InMemoryNonceStore

    if hasattr(app.state, "captcha"):
        del app.state.captcha
    app.state.captcha = CaptchaService(nonces=InMemoryNonceStore())

    client = TestClient(app)
    client.__enter__()
    env = Env(client=client, sender=sender)
    return env, (settings, original, lifecycle, original_cached, client, app, engine)


@pytest.fixture
def captcha_env(tmp_path: Path) -> Iterator[Env]:
    env, restore = _build_env(tmp_path, enabled=True)
    settings, original, lifecycle, original_cached, client, app, engine = restore
    try:
        yield env
    finally:
        (
            settings.captcha.enabled,
            settings.captcha.hmac_secret,
            settings.captcha.cost,
            settings.captcha.ttl_seconds,
        ) = original
        lifecycle.get_cached_email_sender = original_cached  # type: ignore[assignment]
        client.__exit__(None, None, None)
        app.dependency_overrides.clear()
        import asyncio

        asyncio.run(engine.dispose())


@pytest.fixture
def plain_env(tmp_path: Path) -> Iterator[Env]:
    """默认配置（CAPTCHA 关闭）：注册不该被挡。"""
    env, restore = _build_env(tmp_path, enabled=False)
    settings, original, lifecycle, original_cached, client, app, engine = restore
    try:
        yield env
    finally:
        (
            settings.captcha.enabled,
            settings.captcha.hmac_secret,
            settings.captcha.cost,
            settings.captcha.ttl_seconds,
        ) = original
        lifecycle.get_cached_email_sender = original_cached  # type: ignore[assignment]
        client.__exit__(None, None, None)
        app.dependency_overrides.clear()
        import asyncio

        asyncio.run(engine.dispose())


def _solve_payload(*, ip: str = "testclient", secret: str = SECRET, cost: int = 500) -> str:
    """用库真实解一个挑战（模拟浏览器里的 widget）。"""
    from altcha import Payload, create_challenge, solve_challenge

    from app.core.captcha import ip_fingerprint

    challenge = create_challenge(
        "PBKDF2/SHA-256",
        cost,
        hmac_secret=secret,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        data={"ip": ip_fingerprint(ip)},
    )
    solution = solve_challenge(challenge)
    assert solution is not None
    return Payload(challenge, solution).to_base64()


def _register(client: TestClient, *, captcha: str | None = None, username: str = "capuser"):
    body: dict[str, Any] = {
        "username": username,
        "email": f"{username}@example.com",
        "password": "password123",
    }
    if captcha is not None:
        body["captcha"] = captcha
    return client.post(f"{API}/register", json=body)


class TestCaptchaDisabled:
    """默认（本地开发 / CI）：注册不需要人机验证。"""

    def test_register_without_captcha_succeeds(self, plain_env: Env) -> None:
        assert _register(plain_env.client).status_code == 201

    def test_challenge_endpoint_reports_disabled(self, plain_env: Env) -> None:
        response = plain_env.client.get(f"{API}/captcha/challenge")
        assert response.status_code == 200
        assert response.json() == {"enabled": False}


class TestCaptchaEnabled:
    def test_challenge_endpoint_is_public_and_returns_v2_shape(self, captcha_env: Env) -> None:
        response = captcha_env.client.get(f"{API}/captcha/challenge")

        assert response.status_code == 200
        body = response.json()
        assert body["enabled"] is True
        # ALTCHA v2 协议：{parameters, signature}
        assert body["signature"]
        params = body["parameters"]
        assert params["algorithm"] == "PBKDF2/SHA-256"
        assert params["nonce"]  # 一次性凭据的载体
        assert params["expiresAt"] > 0
        assert "ip" in params["data"], "挑战必须绑定客户端 IP 指纹"

    def test_register_without_captcha_is_rejected(self, captcha_env: Env) -> None:
        response = _register(captcha_env.client)

        assert response.status_code == 422
        body = response.json()
        assert body["error_code"] == "CAPTCHA_FAILED"
        assert (body.get("details") or {}).get("reason") == "missing"

    def test_garbage_payload_is_rejected(self, captcha_env: Env) -> None:
        response = _register(captcha_env.client, captcha="bm90LWEtcmVhbC1wYXlsb2Fk")

        assert response.status_code == 422
        assert response.json()["error_code"] == "CAPTCHA_FAILED"

    def test_valid_payload_creates_user(self, captcha_env: Env) -> None:
        response = _register(captcha_env.client, captcha=_solve_payload())

        assert response.status_code == 201, response.text
        assert len(captcha_env.sender.sent) == 1, "注册后应签发验证邮件"

    def test_payload_is_single_use(self, captcha_env: Env) -> None:
        payload = _solve_payload()
        assert _register(captcha_env.client, captcha=payload, username="first").status_code == 201

        replay = _register(captcha_env.client, captcha=payload, username="second")

        assert replay.status_code == 422
        assert (replay.json().get("details") or {}).get("reason") == "nonce_used"

    def test_payload_bound_to_other_ip_is_rejected(self, captcha_env: Env) -> None:
        payload = _solve_payload(ip="198.51.100.7")  # 与 TestClient 的 "testclient" 不符

        response = _register(captcha_env.client, captcha=payload)

        assert response.status_code == 422
        assert (response.json().get("details") or {}).get("reason") == "ip_mismatch"

    def test_payload_signed_with_wrong_secret_is_rejected(self, captcha_env: Env) -> None:
        payload = _solve_payload(secret="attacker-guessed-secret")

        response = _register(captcha_env.client, captcha=payload)

        assert response.status_code == 422
        assert response.json()["error_code"] == "CAPTCHA_FAILED"

    def test_expired_challenge_is_rejected(self, captcha_env: Env) -> None:
        from altcha import Payload, create_challenge, solve_challenge

        from app.core.captcha import ip_fingerprint

        challenge = create_challenge(
            "PBKDF2/SHA-256",
            500,
            hmac_secret=SECRET,
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
            data={"ip": ip_fingerprint("testclient")},
        )
        solution = solve_challenge(challenge)
        assert solution is not None
        payload = Payload(challenge, solution).to_base64()

        response = _register(captcha_env.client, captcha=payload)

        assert response.status_code == 422
        assert (response.json().get("details") or {}).get("reason") == "expired"
