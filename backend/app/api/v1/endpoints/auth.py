"""鉴权端点：注册 / 登录 / 令牌刷新 / 当前用户信息。

阶段1：改用 UserRepository 访问数据，路由层只做参数校验与令牌签发。

路由前缀：/api/v1/auth
- POST /register  注册新用户
- POST /login     用户名/邮箱登录
- POST /refresh   用 refresh_token 换发新 access_token
- GET  /me        获取当前登录用户信息（需 access_token）
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user
from app.core.config import settings
from app.core.exceptions import UnauthorizedException, ValidationException
from app.core.security import (
    TOKEN_TYPE_REFRESH,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.db.models.user import User
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
    return user


@router.post("/login", response_model=TokenOut, summary="登录获取令牌")
async def login(
    payload: UserLogin,
    db: AsyncSession = Depends(get_db),
) -> TokenOut:
    """登录：支持用户名或邮箱，校验通过后签发 access_token + refresh_token。"""
    repo = UserRepository(db)
    user = await repo.get_by_account(payload.account)
    if user is None or not verify_password(payload.password, user.password_hash):
        # 统一返回"账号或密码错误"，避免通过提示差异枚举有效账号
        raise UnauthorizedException("账号或密码错误")
    if not user.is_active:
        raise UnauthorizedException("账号已被禁用")

    return TokenOut(
        access_token=create_access_token(user.id, role=user.role),
        refresh_token=create_refresh_token(user.id),
        expires_in=_access_token_expires_in(),
    )


@router.post("/refresh", response_model=TokenOut, summary="刷新 access_token")
async def refresh_token(
    payload: TokenRefresh,
    db: AsyncSession = Depends(get_db),
) -> TokenOut:
    """用 refresh_token 换发新的 access_token（同时签发新 refresh_token，实现轮换）。"""
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
        if token_data["iat"] < user.token_invalid_before.timestamp():
            raise UnauthorizedException("刷新令牌已被失效，请重新登录")

    return TokenOut(
        access_token=create_access_token(user.id, role=user.role),
        refresh_token=create_refresh_token(user.id),
        expires_in=60 * 60,
    )


@router.get("/me", response_model=UserOut, summary="获取当前登录用户")
async def get_me(
    current_user: User = Depends(get_current_active_user),
) -> User:
    """返回当前 access_token 对应的用户信息。"""
    return current_user
