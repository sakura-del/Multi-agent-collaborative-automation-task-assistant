"""统一免密认证端点：邮箱验证码（OTP）+ 一键登录链接（Magic Link）一体流。

路由前缀：/api/v1/auth
- POST /email-otp/request  发送 6 位验证码与一键登录链接（老邮箱登录，新邮箱自动建号）
- POST /email-otp/verify   核验验证码或一键登录令牌，直接签发 JWT（新邮箱自动创建已验证账号）
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, EmailStr, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.auth_lifecycle import get_email_sender
from app.core.config import get_settings, settings
from app.core.exceptions import RateLimitedException, UnauthorizedException, ValidationException
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
)
from app.db.models.user import User
from app.db.repositories.user import UserRepository
from app.db.session import get_db
from app.schemas.auth import TokenOut
from app.services.email_sender import EmailSender

router = APIRouter(prefix="/auth", tags=["auth"])
logger = get_logger(__name__)

OTP_TTL_SECONDS = 600
"""验证码与一键登录链接有效期（10 分钟）。"""

OTP_COOLDOWN_SECONDS = 60
"""同一邮箱重复发送冷却时间（60 秒）。"""

OTP_MAX_ATTEMPTS = 5
"""单个验证码最大允许试错次数（防 6 位数字爆破）。"""

_SAFE_USERNAME_RE = re.compile(r"[^a-zA-Z0-9_]+")


class EmailOtpRequestIn(BaseModel):
    """请求发送邮箱验证码/一键登录链接。"""

    email: EmailStr


class EmailOtpRequestOut(BaseModel):
    """发送结果响应。"""

    message: str
    cooldown_seconds: int = OTP_COOLDOWN_SECONDS


class EmailOtpVerifyIn(BaseModel):
    """核验邮箱验证码或一键登录链接（二选一）。"""

    email: EmailStr | None = None
    code: str | None = Field(default=None, min_length=6, max_length=6)
    magic_token: str | None = Field(default=None, min_length=16, max_length=256)

    @model_validator(mode="after")
    def _validate_choice(self) -> EmailOtpVerifyIn:
        has_otp = bool(self.email and self.code)
        has_magic = bool(self.magic_token)
        if not has_otp and not has_magic:
            raise ValueError("必须提供 email + code 或 magic_token")
        return self


class _MemoryOtpStore:
    """进程内 TTL 存储（无 Redis 或测试环境兜底）。"""

    def __init__(self) -> None:
        self._data: dict[str, tuple[str, float]] = {}

    def _purge_if_expired(self, key: str) -> None:
        entry = self._data.get(key)
        if entry is not None and entry[1] <= time.monotonic():
            self._data.pop(key, None)

    async def get(self, key: str) -> str | None:
        self._purge_if_expired(key)
        entry = self._data.get(key)
        return entry[0] if entry is not None else None

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self._data[key] = (value, time.monotonic() + ttl)

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)


_memory_store = _MemoryOtpStore()


def _resolve_store(request: Request) -> Any:
    """优先使用 app.state.redis；不可用时回退内存 TTL 存储。"""
    redis = getattr(request.app.state, "redis", None)
    return redis if redis is not None else _memory_store


def _normalize_email(raw: str) -> str:
    return raw.strip().lower()


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _frontend_base_url() -> str:
    email_cfg = getattr(get_settings(), "email", None)
    base = getattr(email_cfg, "frontend_base_url", None) or "http://localhost:5173"
    return str(base).rstrip("/")


async def _generate_unique_username(repo: UserRepository, email: str) -> str:
    """根据邮箱前缀生成唯一用户名（长度 3~64）。"""
    local_part = email.split("@", 1)[0]
    cleaned = _SAFE_USERNAME_RE.sub("_", local_part).strip("_")
    if len(cleaned) < 2:
        cleaned = "user"
    prefix = cleaned[:48]
    for _ in range(8):
        suffix = secrets.token_hex(2)
        candidate = f"{prefix}_{suffix}"
        if await repo.get_by_username(candidate) is None:
            return candidate
    return f"u_{secrets.token_hex(6)}"


def _issue_tokens(user: User) -> TokenOut:
    """签发 access_token 与 refresh_token（与 /auth/login 口径完全一致）。"""
    return TokenOut(
        access_token=create_access_token(user.id, role=user.role),
        refresh_token=create_refresh_token(user.id),
        token_type="bearer",
        expires_in=settings.jwt_access_token_expire_minutes * 60,
    )


@router.post(
    "/email-otp/request",
    response_model=EmailOtpRequestOut,
    summary="发送邮箱验证码与一键登录链接",
)
async def request_email_otp(
    payload: EmailOtpRequestIn,
    request: Request,
    sender: EmailSender = Depends(get_email_sender),
) -> EmailOtpRequestOut:
    """向指定邮箱发送 6 位验证码与一键登录链接；新老邮箱统一走同一入口。"""
    email = _normalize_email(str(payload.email))
    store = _resolve_store(request)

    cooldown_key = f"email_otp:cooldown:{email}"
    if await store.get(cooldown_key) is not None:
        raise RateLimitedException("发送过于频繁，请 60 秒后再试")

    code = f"{secrets.randbelow(1_000_000):06d}"
    magic_token = secrets.token_urlsafe(32)

    otp_key = f"email_otp:code:{email}"
    magic_key = f"email_otp:magic:{_sha256_hex(magic_token)}"

    await store.setex(
        otp_key,
        OTP_TTL_SECONDS,
        json.dumps({"hash": _sha256_hex(f"{email}:{code}"), "attempts": 0}),
    )
    await store.setex(magic_key, OTP_TTL_SECONDS, email)
    await store.setex(cooldown_key, OTP_COOLDOWN_SECONDS, "1")

    magic_link = f"{_frontend_base_url()}/login?magic_token={magic_token}"
    body = (
        f"您好，\n\n"
        f"您的 VeriPaper 登录验证码为：{code}\n"
        f"（10 分钟内有效，请勿泄露给他人）\n\n"
        f"您也可以直接点击下方链接一键登录工作台：\n"
        f"{magic_link}\n\n"
        f"如非本人操作，请忽略此邮件。"
    )
    await sender.send(to=email, subject="VeriPaper 登录验证码", body=body)
    logger.info("email_otp_requested", email=email)
    return EmailOtpRequestOut(
        message="验证码已发送至你的邮箱，请查收。",
        cooldown_seconds=OTP_COOLDOWN_SECONDS,
    )


@router.post(
    "/email-otp/verify",
    response_model=TokenOut,
    summary="验证邮箱验证码或一键登录链接",
)
async def verify_email_otp(
    payload: EmailOtpVerifyIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenOut:
    """校验 6 位验证码或 magic_token；老用户直接登录，新邮箱自动创建已验证账号。"""
    store = _resolve_store(request)
    verified_email: str | None = None

    if payload.magic_token:
        magic_key = f"email_otp:magic:{_sha256_hex(payload.magic_token.strip())}"
        raw_email = await store.get(magic_key)
        if not raw_email:
            raise UnauthorizedException("登录链接无效或已过期，请重新获取")
        await store.delete(magic_key)
        verified_email = (
            raw_email.decode("utf-8") if isinstance(raw_email, bytes) else str(raw_email)
        )
        await store.delete(f"email_otp:code:{verified_email}")
    else:
        assert payload.email is not None and payload.code is not None
        email = _normalize_email(str(payload.email))
        code = payload.code.strip()
        if not code.isdigit() or len(code) != 6:
            raise ValidationException("请输入 6 位数字验证码")

        otp_key = f"email_otp:code:{email}"
        raw_record = await store.get(otp_key)
        if not raw_record:
            raise UnauthorizedException("验证码已过期或不存在，请重新发送")

        record_text = (
            raw_record.decode("utf-8") if isinstance(raw_record, bytes) else str(raw_record)
        )
        record = json.loads(record_text)
        attempts = int(record.get("attempts", 0))
        if attempts >= OTP_MAX_ATTEMPTS:
            await store.delete(otp_key)
            raise UnauthorizedException("验证码错误次数过多，请重新获取")

        expected_hash = record.get("hash", "")
        if _sha256_hex(f"{email}:{code}") != expected_hash:
            next_attempts = attempts + 1
            if next_attempts >= OTP_MAX_ATTEMPTS:
                await store.delete(otp_key)
                raise UnauthorizedException("验证码错误次数过多，请重新获取")
            await store.setex(
                otp_key,
                OTP_TTL_SECONDS,
                json.dumps({"hash": expected_hash, "attempts": next_attempts}),
            )
            raise UnauthorizedException("验证码错误，请重试")

        await store.delete(otp_key)
        verified_email = email

    repo = UserRepository(db)
    user = await repo.get_by_email(verified_email)
    now = datetime.now(UTC)

    if user is None:
        username = await _generate_unique_username(repo, verified_email)
        user = User(
            username=username,
            email=verified_email,
            password_hash=hash_password(secrets.token_urlsafe(32)),
            role="user",
            is_active=True,
            email_verified_at=now,
        )
        await repo.create(user)
        logger.info("email_otp_auto_registered", user_id=str(user.id), email=verified_email)
    else:
        if not user.is_active:
            raise UnauthorizedException("账号已被禁用")
        if user.email_verified_at is None:
            user.email_verified_at = now
            await db.flush()

    return _issue_tokens(user)
