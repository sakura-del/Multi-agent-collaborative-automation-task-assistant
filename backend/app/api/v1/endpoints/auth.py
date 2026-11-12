"""鉴权端点：注册 / 登录 / 令牌刷新 / 当前用户信息。

阶段1：改用 UserRepository 访问数据，路由层只做参数校验与令牌签发。

路由前缀：/api/v1/auth
- POST /register  注册新用户
- POST /login     用户名/邮箱登录
- POST /refresh   用 refresh_token 换发新 access_token
- GET  /me        获取当前登录用户信息（需 access_token）
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user
from app.core.config import settings
from app.core.exceptions import (
    RateLimitedException,
    UnauthorizedException,
    ValidationException,
)
from app.core.logging import get_logger
from app.core.login_throttle import (
    InMemoryLoginThrottle,
    LoginThrottle,
    RedisLoginThrottle,
    normalize_account,
)
from app.core.security import (
    TOKEN_TYPE_REFRESH,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    utc_timestamp,
    verify_password,
)
from app.db.models.user import User
from app.db.repositories.revoked_token import RevokedTokenRepository
from app.db.repositories.user import UserRepository
from app.db.session import get_db
from app.schemas.auth import (
    TokenOut,
    TokenRefresh,
    UserCreate,
    UserLogin,
    UserOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])

logger = get_logger(__name__)


def _access_token_expires_in() -> int:
    """access_token 有效期（秒），统一从 settings 派生，避免与令牌实际有效期脱节。"""
    return settings.jwt_access_token_expire_minutes * 60


@router.post("/register", response_model=UserOut, status_code=201, summary="注册新用户")
async def register(
    payload: UserCreate,
    db: AsyncSession = Depends(get_db),
) -> User:
    """注册新用户：用户名与邮箱均唯一，冲突时返回 422。"""
    repo = UserRepository(db)
    # 预查重：利用 Repository 封装，给出更友好的冲突字段提示
    existing = await repo.exists(payload.username, payload.email)
    if existing is not None:
        conflict = "username" if existing.username == payload.username else "email"
        raise ValidationException(
            f"该{conflict}已被注册",
            details={"conflict_field": conflict},
        )

    user = User(
        username=payload.username,
        email=payload.email,
        password_hash=hash_password(payload.password),
        role="user",
    )
    try:
        # Repository.create 只 flush，此处统一 commit 提交事务
        await repo.create(user)
        await db.commit()
    except IntegrityError as exc:
        # 并发场景下唯一索引兜底
        await db.rollback()
        raise ValidationException("用户名或邮箱已被注册") from exc

    # Phase 1 P1-C：注册后自动发验证邮件。
    # 未配置 SMTP 时走 LoggingEmailSender（只记日志），**绝不阻塞注册**；
    # 发信失败也只记 warning，不能让"已成功建号"的用户看到 500。
    from app.api.v1.endpoints.auth_lifecycle import issue_verification_email

    await issue_verification_email(db, user)
    return user


@router.post("/login", response_model=TokenOut, summary="登录获取令牌")
async def login(
    payload: UserLogin,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenOut:
    """登录：支持用户名或邮箱，校验通过后签发 access_token + refresh_token。

    Issue #11 M-3：登录失败按**账号**累计，连续失败达到阈值后临时锁定。
    锁定期间即使密码正确也拒绝——否则爆破者只要猜中就照常登录，退避等于没做。
    """
    throttle = _resolve_login_throttle(request)
    account_key = normalize_account(payload.account)

    locked_for = await _throttle_check(throttle, account_key)
    if locked_for is not None:
        logger.warning(
            "login_locked", account_hash=_hashed_account(account_key), retry_after=locked_for
        )
        raise RateLimitedException(
            "登录失败次数过多，请稍后再试",
            retry_after=locked_for,
            details={"reason": "account_locked"},
        )

    repo = UserRepository(db)
    user = await repo.get_by_account(payload.account)
    if user is None or not verify_password(payload.password, user.password_hash):
        failures = await _throttle_register_failure(throttle, account_key)
        logger.warning(
            "login_failed",
            account_hash=_hashed_account(account_key),
            consecutive_failures=failures,
        )
        # 统一返回"账号或密码错误"，避免通过提示差异枚举有效账号
        raise UnauthorizedException("账号或密码错误")
    if not user.is_active:
        raise UnauthorizedException("账号已被禁用")

    await _throttle_reset(throttle, account_key)

    return TokenOut(
        access_token=create_access_token(user.id, role=user.role),
        refresh_token=create_refresh_token(user.id),
        expires_in=_access_token_expires_in(),
    )


def _hashed_account(account: str) -> str:
    """日志里只留账号名的哈希前缀：退避排障需要，明文用户名不该进日志。"""
    return hashlib.sha256(account.encode("utf-8")).hexdigest()[:8]


def _resolve_login_throttle(request: Request) -> LoginThrottle:
    """取登录退避组件（首次使用时按配置构造，并缓存在 `app.state` 上）。"""
    existing = getattr(request.app.state, "login_throttle", None)
    if existing is not None:
        return existing

    cfg = settings.rate_limit
    throttle: LoginThrottle
    if cfg.backend == "memory":
        throttle = InMemoryLoginThrottle(
            limit=cfg.login_failure_limit, window_seconds=cfg.login_failure_window_seconds
        )
    else:
        try:
            import redis.asyncio as aioredis

            client = aioredis.from_url(
                settings.redis.url,
                socket_connect_timeout=1.0,
                socket_timeout=1.0,
                decode_responses=False,
            )
            throttle = RedisLoginThrottle(
                redis_client=client,
                limit=cfg.login_failure_limit,
                window_seconds=cfg.login_failure_window_seconds,
                key_prefix=cfg.key_prefix,
            )
        except Exception as exc:  # pragma: no cover - 依赖缺失时退化为内存
            logger.warning("login_throttle_redis_unavailable", error=str(exc))
            throttle = InMemoryLoginThrottle(
                limit=cfg.login_failure_limit, window_seconds=cfg.login_failure_window_seconds
            )

    request.app.state.login_throttle = throttle
    return throttle


async def _throttle_check(throttle: LoginThrottle, account: str) -> int | None:
    """查锁定状态；组件故障一律放行（fail-open，不能让 Redis 挂了就没人能登录）。"""
    try:
        return await throttle.check(account)
    except Exception as exc:
        logger.warning("login_throttle_check_failed", error=str(exc))
        return None


async def _throttle_register_failure(throttle: LoginThrottle, account: str) -> int:
    try:
        return await throttle.register_failure(account)
    except Exception as exc:
        logger.warning("login_throttle_register_failed", error=str(exc))
        return 0


async def _throttle_reset(throttle: LoginThrottle, account: str) -> None:
    try:
        await throttle.reset(account)
    except Exception as exc:
        logger.warning("login_throttle_reset_failed", error=str(exc))


def _refresh_token_expiry(token_data: dict, fallback_from: datetime) -> datetime:
    """取 refresh 令牌自身的过期时刻，作为吊销记录的存活期。

    优先读 JWT 的 `exp`；缺失时按配置的 refresh 有效期兜底——吊销记录宁可多留一会儿，
    也不能比令牌先过期（那等于提前放行一个还能用的旧令牌）。
    """
    exp = token_data.get("exp")
    if isinstance(exp, (int, float)):
        return datetime.fromtimestamp(float(exp), UTC)
    return fallback_from + timedelta(days=settings.jwt_refresh_token_expire_days)


@router.post("/refresh", response_model=TokenOut, summary="刷新 access_token")
async def refresh_token(
    payload: TokenRefresh,
    db: AsyncSession = Depends(get_db),
) -> TokenOut:
    """用 refresh_token 换发新令牌（轮换 + 旧令牌立即失效）。

    Issue #11 M-1：原先换发后旧 refresh 仍可再用，等于令牌泄露后无法止损。
    现在的口径：
    1. 换发即把旧 `jti` 写入吊销表（存活期 = 令牌自身 exp）；
    2. 旧令牌再来 → 401，**并把整个家族作废**（`token_invalid_before = now`）。
    """
    # refresh_token 必须用 expected_type=refresh 校验，防止用 access_token 冒充
    token_data = decode_token(payload.refresh_token, expected_type=TOKEN_TYPE_REFRESH)
    try:
        user_id = uuid.UUID(token_data["sub"])
    except (KeyError, ValueError, TypeError) as exc:
        raise UnauthorizedException("刷新令牌非法") from exc

    repo = UserRepository(db)
    user = await repo.get(user_id)
    if user is None or not user.is_active:
        raise UnauthorizedException("用户不存在或已禁用")

    # 强制下线检查：refresh_token 同样受 token_invalid_before 约束
    if user.token_invalid_before is not None and "iat" in token_data:
        # 同 deps.get_current_user：naive datetime 必须按 UTC 解释
        if token_data["iat"] < utc_timestamp(user.token_invalid_before):
            raise UnauthorizedException("刷新令牌已被失效，请重新登录")

    jti = token_data.get("jti")
    if not isinstance(jti, str) or not jti:
        # 本修复之前签发的令牌没有 jti，无法登记吊销；留着就是一条绕过路径
        raise UnauthorizedException(
            "刷新令牌已失效，请重新登录",
            details={"reason": "missing_jti"},
        )

    now = datetime.now(UTC)
    revoked_repo = RevokedTokenRepository(db)

    # 先抢吊销登记：抢不到说明这个 jti 已经被用过（重放）
    inserted = await revoked_repo.add(
        jti,
        user_id=user.id,
        expires_at=_refresh_token_expiry(token_data, now),
    )
    if not inserted:
        # 复用：要么合法端重试（多标签页同时刷新），要么攻击者先用了。
        # ⚠️ 判定时刻必须**重新取**：请求开头那个 `now` 早于本次插入的 `created_at`，
        # 拿它算间隔会得到负数 → 宽限期恒成立（2026-11-12 实测踩到，单测过、全量才暴露）。
        check_time = datetime.now(UTC)
        revoked_at = await revoked_repo.get_revoked_at(jti)
        elapsed = None if revoked_at is None else check_time.timestamp() - utc_timestamp(revoked_at)
        grace = settings.jwt_refresh_reuse_grace_seconds
        # grace <= 0 明确表示「关闭宽限」：配置成 0 却因为同一瞬间 elapsed == 0 走进宽限分支，
        # 会让「最严格配置」变成随机行为。
        within_grace = grace > 0 and elapsed is not None and elapsed <= grace
        if within_grace:
            # 宽限期内：只拒绝这次重放，不作废家族——否则两个标签页就能把用户自己踢下线
            logger.warning(
                "refresh_token_reuse_within_grace",
                user_id=str(user.id),
                jti=jti,
                elapsed_seconds=round(elapsed or 0.0, 2),
            )
            raise UnauthorizedException(
                "刷新令牌刚被使用，请稍后重试或重新登录",
                details={"reason": "refresh_reuse_grace"},
            )

        # 超过宽限期还在用旧令牌：认定泄露，作废该用户全部令牌
        user.token_invalid_before = check_time
        await db.commit()
        logger.warning("refresh_token_reuse_detected", user_id=str(user.id), jti=jti)
        raise UnauthorizedException(
            "刷新令牌已被使用，请重新登录",
            details={"reason": "refresh_reuse"},
        )

    # 顺手清理自然过期的记录（令牌过期后这些行已经没有意义）
    purged = await revoked_repo.purge_expired(now=now)
    await db.commit()

    if purged:
        logger.info("refresh_token_revocations_purged", count=purged)
    logger.info("refresh_token_rotated", user_id=str(user.id))

    return TokenOut(
        access_token=create_access_token(user.id, role=user.role),
        refresh_token=create_refresh_token(user.id),
        expires_in=_access_token_expires_in(),
    )


@router.get("/me", response_model=UserOut, summary="获取当前登录用户")
async def get_me(
    current_user: User = Depends(get_current_active_user),
) -> User:
    """返回当前 access_token 对应的用户信息。"""
    return current_user
