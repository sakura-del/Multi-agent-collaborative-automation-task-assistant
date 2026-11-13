"""账号生命周期端点：邮箱验证 / 密码重置（Phase 1 P1-C / 计划 2026-10-20-00 §1.3）。

    POST /api/v1/auth/verify-email/request      重发验证邮件（需登录）
    POST /api/v1/auth/verify-email/confirm      {token} 完成邮箱验证
    POST /api/v1/auth/password-reset/request    {email} 申请重置（**恒定响应**，防账号枚举）
    POST /api/v1/auth/password-reset/confirm    {token, new_password} 完成重置

安全要点：
- 令牌**只存 sha256 哈希**，明文只出现在邮件正文里，且**单次使用**（``used_at``）+ 过期拒绝；
- 密码重置申请对「存在/不存在/已禁用」账号返回**逐字节相同**的响应（防枚举）；
- 重置成功写入 ``token_invalid_before``，使此前签发的 access/refresh token 失效（强制重新登录）；
- 发信走可替换的 ``EmailSender``；**未配置 SMTP 只记日志、绝不阻塞**账号流程。

路由由 Lead 在 ``app/api/v1/router.py`` 统一挂载（本模块不自挂载），最终路径前缀 ``/api/v1/auth``。
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user
from app.core.config import get_settings, settings
from app.core.exceptions import ValidationException
from app.core.logging import get_logger
from app.core.security import hash_password
from app.db.models.email_token import EmailToken, EmailTokenPurpose
from app.db.models.user import User
from app.db.repositories.email_token import EmailTokenRepository
from app.db.repositories.user import UserRepository
from app.db.session import get_db
from app.schemas.auth_lifecycle import (
    EmailVerificationConfirm,
    EmailVerificationRequest,
    LifecycleMessageOut,
    PasswordResetConfirm,
    PasswordResetRequest,
)
from app.services.email_sender import EmailSender, get_cached_email_sender

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# ===== 文案（重置申请文案必须与账号是否存在无关）=====
MSG_VERIFY_SENT = "验证邮件已发送，请查收并在有效期内完成验证。"
MSG_ALREADY_VERIFIED = "邮箱已验证，无需重复验证。"
MSG_VERIFY_SUCCESS = "邮箱验证成功。"
MSG_RESET_REQUESTED = "如果该邮箱已注册，我们已发送密码重置邮件，请查收。"
MSG_RESET_SUCCESS = "密码重置成功，请使用新密码重新登录。"

DEFAULT_TOKEN_TTL_HOURS = 24
DEFAULT_FRONTEND_BASE_URL = "http://localhost:5173"

EMAIL_TOKEN_BYTES = 32
"""令牌随机字节数（``token_urlsafe(32)`` ≈ 43 字符，256 bit 熵）。"""

_MAX_TTL_HOURS = 24 * 30


# ===== 依赖 =====


def get_email_sender() -> EmailSender:
    """邮件发送器依赖（进程级单例）。

    无参数、可同步构造：既可作为 FastAPI 依赖注入，也可在非请求上下文（如
    ``auth.register`` 注册后自动发验证邮件）直接调用；测试用
    ``app.dependency_overrides[get_email_sender]`` 注入 fake。
    """
    return get_cached_email_sender()


CurrentUserDep = Annotated[User, Depends(get_current_active_user)]
DbDep = Annotated[AsyncSession, Depends(get_db)]
SenderDep = Annotated[EmailSender, Depends(get_email_sender)]


# ===== 配置读取 =====


def _email_config() -> Any | None:
    """``settings.email``（配置未就绪时返回 ``None``，调用方走默认值）。"""
    return getattr(get_settings(), "email", None)


def _ttl_hours(name: str) -> int:
    """令牌有效期（小时）：配置优先，非法值回退默认 24h。"""
    value = getattr(_email_config(), name, None)
    if value is None:
        return DEFAULT_TOKEN_TTL_HOURS
    try:
        hours = int(value)
    except (TypeError, ValueError):
        return DEFAULT_TOKEN_TTL_HOURS
    return min(max(hours, 1), _MAX_TTL_HOURS)


def _frontend_base_url() -> str:
    """前端基地址（用于拼接验证/重置链接），去掉尾部斜杠。"""
    value = str(getattr(_email_config(), "frontend_base_url", "") or "").strip()
    return (value or DEFAULT_FRONTEND_BASE_URL).rstrip("/")


# ===== 令牌工具 =====


def generate_token() -> str:
    """生成一次性明文令牌（只在发信链路出现，不落库）。"""
    return secrets.token_urlsafe(EMAIL_TOKEN_BYTES)


def hash_token(token: str) -> str:
    """令牌哈希：``sha256(token)`` 十六进制（64 字符，与库中列一致）。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    """把数据库读回的 naive 时间按 UTC 解释（SQLite 不保留 tzinfo）。"""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def _issue_token(
    db: AsyncSession, *, user_id: Any, purpose: EmailTokenPurpose, ttl_hours: int
) -> str:
    """签发令牌：先把该用户同用途未消费令牌作废，再写入新哈希并提交。"""
    now = datetime.now(UTC)
    repository = EmailTokenRepository(db)
    invalidated = await repository.invalidate_outstanding(user_id, purpose.value, now=now)
    if invalidated:
        logger.info("email_token_outstanding_invalidated", purpose=purpose.value, count=invalidated)
    token = generate_token()
    await repository.create(
        EmailToken(
            user_id=user_id,
            purpose=purpose.value,
            token_hash=hash_token(token),
            expires_at=now + timedelta(hours=ttl_hours),
        )
    )
    await db.commit()
    return token


async def consume_email_token(
    db: AsyncSession, *, token: str, purpose: EmailTokenPurpose
) -> EmailToken:
    """校验并消费一次性令牌（**不提交**，由调用方与其他变更一起提交）。

    拒绝条件（一律 422，不泄漏细节差异）：不存在、用途不符、已使用、已过期。
    """
    normalized = (token or "").strip()
    if not normalized:
        raise ValidationException("令牌不能为空")

    record = await EmailTokenRepository(db).get_by_hash(hash_token(normalized))
    if record is None or record.purpose != purpose.value:
        raise ValidationException("验证链接无效，请重新申请。")
    if record.used_at is not None:
        raise ValidationException("该链接已被使用，请重新申请。")
    now = datetime.now(UTC)
    if _as_utc(record.expires_at) <= now:
        raise ValidationException("验证链接已过期，请重新申请。")
    record.used_at = now
    return record


# ===== 发信 =====


def _verify_body(token: str, ttl_hours: int) -> str:
    return (
        "你好，请点击下面的链接完成邮箱验证：\n\n"
        f"{_frontend_base_url()}/verify-email?token={token}\n\n"
        f"该链接 {ttl_hours} 小时内有效，且只能使用一次。如非本人操作，请忽略本邮件。"
    )


def _reset_body(token: str, ttl_hours: int) -> str:
    return (
        "你好，我们收到了你的密码重置请求，请点击下面的链接设置新密码：\n\n"
        f"{_frontend_base_url()}/reset-password?token={token}\n\n"
        f"该链接 {ttl_hours} 小时内有效，且只能使用一次。如非本人操作，请忽略本邮件。"
    )


async def _deliver(sender: EmailSender, *, to: str, subject: str, body: str) -> bool:
    """发送邮件并兜住一切异常（邮件失败不得影响账号流程）。"""
    try:
        return bool(await sender.send(to=to, subject=subject, body=body))
    except Exception as exc:  # 第三方实现可能抛错：邮件失败不得影响账号流程
        logger.warning("email_delivery_failed", to=to, error=str(exc))
        return False


async def issue_verification_email(
    db: AsyncSession, user: User, sender: EmailSender | None = None
) -> bool:
    """给未验证用户签发并发送验证邮件；返回是否真的发送。

    - 已验证 → 返回 ``False``（不重复发信）；
    - ``sender`` 缺省时用进程内默认发送器，便于 ``auth.register`` 注册后**一行接入**；
    - 发信失败不影响令牌签发（用户可再次申请）。
    """
    if user.email_verified_at is not None:
        return False
    ttl_hours = _ttl_hours("verify_token_ttl_hours")
    token = await _issue_token(
        db, user_id=user.id, purpose=EmailTokenPurpose.VERIFY, ttl_hours=ttl_hours
    )
    sent = await _deliver(
        sender or get_email_sender(),
        to=user.email,
        subject="验证你的邮箱",
        body=_verify_body(token, ttl_hours),
    )
    logger.info("verify_email_issued", user_id=str(user.id), email_sent=sent)
    return sent


async def issue_password_reset_email(
    db: AsyncSession, user: User, sender: EmailSender | None = None
) -> bool:
    """给用户签发并发送密码重置邮件；返回是否真的发送。"""
    ttl_hours = _ttl_hours("reset_token_ttl_hours")
    token = await _issue_token(
        db, user_id=user.id, purpose=EmailTokenPurpose.RESET, ttl_hours=ttl_hours
    )
    sent = await _deliver(
        sender or get_email_sender(),
        to=user.email,
        subject="重置你的密码",
        body=_reset_body(token, ttl_hours),
    )
    logger.info("password_reset_email_issued", user_id=str(user.id), email_sent=sent)
    return sent


# ===== 端点 =====


@router.post(
    "/verify-email/request",
    response_model=LifecycleMessageOut,
    summary="重发邮箱验证邮件（免登录，按邮箱）",
)
async def request_email_verification(
    payload: EmailVerificationRequest,
    db: DbDep,
    sender: SenderDep,
) -> LifecycleMessageOut:
    """按邮箱重发验证邮件；**不需要登录态**。

    2026-11-13 重构：旧版要求 ``CurrentUserDep``——未验证用户恰恰没有令牌，
    邮件过期后永远无法重发（死锁）。

    响应**恒定**（防邮箱枚举）：邮箱不存在 / 已验证 / 冷却中，与成功重发
    返回同一 message、同一状态码。滥用面分析：只有「真实存在且未验证」的
    邮箱会触发发信，且受按邮箱冷却（``verification_resend_cooldown_seconds``）
    限制；枚举不存在的邮箱零发信、零成本差異。
    """
    cooldown = timedelta(seconds=settings.email.verification_resend_cooldown_seconds)

    result = await db.execute(select(User).where(func.lower(User.email) == payload.email.lower()))
    user = result.scalar_one_or_none()
    if user is not None and user.email_verified_at is None:
        token_repo = EmailTokenRepository(db)
        last_created = await token_repo.latest_created_at(user.id, EmailTokenPurpose.VERIFY)
        # 冷却判断放在时间线最前：now 必须晚于 last_created（同毫秒提交也算在冷却内）
        if last_created is None or datetime.now(UTC) - _as_utc(last_created) >= cooldown:
            await issue_verification_email(db, user, sender=sender)
    return LifecycleMessageOut(message=MSG_VERIFY_SENT)


@router.post(
    "/verify-email/confirm",
    response_model=LifecycleMessageOut,
    summary="确认邮箱验证",
)
async def confirm_email_verification(
    payload: EmailVerificationConfirm,
    db: DbDep,
) -> LifecycleMessageOut:
    """消费验证令牌并把当前用户标记为「邮箱已验证」。"""
    record = await consume_email_token(db, token=payload.token, purpose=EmailTokenPurpose.VERIFY)
    user = await db.get(User, record.user_id)
    if user is None:
        raise ValidationException("账号不存在或已注销。")
    user.email_verified_at = datetime.now(UTC)
    await db.commit()
    logger.info("email_verified", user_id=str(user.id))
    return LifecycleMessageOut(message=MSG_VERIFY_SUCCESS)


@router.post(
    "/password-reset/request",
    response_model=LifecycleMessageOut,
    summary="申请密码重置（恒定响应）",
)
async def request_password_reset(
    payload: PasswordResetRequest,
    db: DbDep,
    sender: SenderDep,
) -> LifecycleMessageOut:
    """申请密码重置。

    **恒定响应**：账号存在 / 不存在 / 已禁用一律返回同一状态码与同一响应体，
    仅「存在且启用」时才真的发信——攻击者无法据此枚举注册邮箱。
    """
    user = await UserRepository(db).get_by_email(payload.email)
    if user is not None and user.is_active:
        await issue_password_reset_email(db, user, sender=sender)
    return LifecycleMessageOut(message=MSG_RESET_REQUESTED)


@router.post(
    "/password-reset/confirm",
    response_model=LifecycleMessageOut,
    summary="确认密码重置",
)
async def confirm_password_reset(
    payload: PasswordResetConfirm,
    db: DbDep,
) -> LifecycleMessageOut:
    """消费重置令牌 → 更新密码 → 让此前签发的所有 token 失效。"""
    record = await consume_email_token(db, token=payload.token, purpose=EmailTokenPurpose.RESET)
    user = await db.get(User, record.user_id)
    if user is None:
        raise ValidationException("账号不存在或已注销。")
    user.password_hash = hash_password(payload.new_password)
    # 强制重新登录：早于此刻签发的 access / refresh token 全部失效
    user.token_invalid_before = datetime.now(UTC)
    await db.commit()
    logger.info("password_reset_succeeded", user_id=str(user.id))
    return LifecycleMessageOut(message=MSG_RESET_SUCCESS)
