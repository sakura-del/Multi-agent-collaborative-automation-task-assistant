"""邮箱验证码（OTP）与一键登录链接（Magic Link）一体流测试。"""

from __future__ import annotations

import re

import pytest
from httpx import AsyncClient

from app.api.v1.endpoints.auth_lifecycle import get_email_sender
from app.main import app


class _CaptureSender:
    """捕获发送的邮件，便于提取验证码与 magic_token。"""

    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []

    async def send(self, *, to: str, subject: str, body: str) -> bool:
        self.messages.append({"to": to, "subject": subject, "body": body})
        return True


def _extract_code(body: str) -> str:
    match = re.search(r"验证码为：(\d{6})", body)
    assert match is not None, f"未在邮件正文找到 6 位验证码: {body}"
    return match.group(1)


def _extract_magic_token(body: str) -> str:
    match = re.search(r"magic_token=([A-Za-z0-9_\-]+)", body)
    assert match is not None, f"未在邮件正文找到 magic_token: {body}"
    return match.group(1)


@pytest.mark.asyncio
async def test_email_otp_auto_register_and_relogin(client: AsyncClient) -> None:
    """新邮箱通过 6 位验证码自动建号并登录；同一邮箱再次验证直接登录同一账号。"""
    sender = _CaptureSender()
    app.dependency_overrides[get_email_sender] = lambda: sender
    try:
        # 1) 新邮箱请求验证码
        resp = await client.post(
            "/api/v1/auth/email-otp/request",
            json={"email": "Researcher.New@Example.com"},
        )
        assert resp.status_code == 200
        assert len(sender.messages) == 1
        code = _extract_code(sender.messages[0]["body"])

        # 2) 验证通过 → 自动建号并直接返回 JWT
        verify_resp = await client.post(
            "/api/v1/auth/email-otp/verify",
            json={"email": "researcher.new@example.com", "code": code},
        )
        assert verify_resp.status_code == 200
        tokens = verify_resp.json()
        assert tokens["access_token"]
        assert tokens["refresh_token"]

        # 3) 用拿到的 token 调 /auth/me，邮箱与已验证状态完整
        me_resp = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        assert me_resp.status_code == 200
        me = me_resp.json()
        assert me["email"] == "researcher.new@example.com"
        assert me["email_verified"] is True

        # 4) 二次使用同一验证码失败（单次消费）
        replay_resp = await client.post(
            "/api/v1/auth/email-otp/verify",
            json={"email": "researcher.new@example.com", "code": code},
        )
        assert replay_resp.status_code == 401
    finally:
        app.dependency_overrides.pop(get_email_sender, None)


@pytest.mark.asyncio
async def test_email_otp_magic_link_and_cooldown(client: AsyncClient) -> None:
    """邮件内的一键登录链接可直接换取 JWT，且 60 秒内重复请求触发限流。"""
    sender = _CaptureSender()
    app.dependency_overrides[get_email_sender] = lambda: sender
    try:
        resp = await client.post(
            "/api/v1/auth/email-otp/request",
            json={"email": "magic.user@example.com"},
        )
        assert resp.status_code == 200
        magic_token = _extract_magic_token(sender.messages[0]["body"])

        # 冷却期内重复发送被拒
        cooldown_resp = await client.post(
            "/api/v1/auth/email-otp/request",
            json={"email": "magic.user@example.com"},
        )
        assert cooldown_resp.status_code == 429

        # 点链接直接登录
        verify_resp = await client.post(
            "/api/v1/auth/email-otp/verify",
            json={"magic_token": magic_token},
        )
        assert verify_resp.status_code == 200
        assert verify_resp.json()["access_token"]
    finally:
        app.dependency_overrides.pop(get_email_sender, None)


@pytest.mark.asyncio
async def test_email_otp_locks_after_five_wrong_attempts(client: AsyncClient) -> None:
    """连续输错 5 次验证码后销毁该验证码，防止 6 位数字被爆破。"""
    sender = _CaptureSender()
    app.dependency_overrides[get_email_sender] = lambda: sender
    try:
        await client.post(
            "/api/v1/auth/email-otp/request",
            json={"email": "bruteforce.test@example.com"},
        )
        real_code = _extract_code(sender.messages[0]["body"])
        wrong_code = "000000" if real_code != "000000" else "999999"

        for _ in range(5):
            r = await client.post(
                "/api/v1/auth/email-otp/verify",
                json={"email": "bruteforce.test@example.com", "code": wrong_code},
            )
            assert r.status_code == 401

        # 即使第 6 次输入正确验证码也已被销毁
        final_resp = await client.post(
            "/api/v1/auth/email-otp/verify",
            json={"email": "bruteforce.test@example.com", "code": real_code},
        )
        assert final_resp.status_code == 401
    finally:
        app.dependency_overrides.pop(get_email_sender, None)
