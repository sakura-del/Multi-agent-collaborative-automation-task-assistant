"""鉴权相关 Pydantic 模型：注册/登录请求、令牌响应、用户信息。

严格区分请求输入（Create/Login）与响应输出（Out/Token），
避免 password_hash 等敏感字段泄漏到 API 响应。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserCreate(BaseModel):
    """注册请求体。"""

    username: str = Field(..., min_length=3, max_length=64, description="登录用户名")
    email: EmailStr = Field(..., description="邮箱")
    password: str = Field(..., min_length=8, max_length=128, description="密码明文")


class UserLogin(BaseModel):
    """登录请求体：支持用户名或邮箱登录。"""

    account: str = Field(..., description="用户名或邮箱")
    password: str = Field(..., description="密码明文")


class TokenRefresh(BaseModel):
    """刷新令牌请求体。"""

    refresh_token: str = Field(..., description="refresh_token")


class UserOut(BaseModel):
    """用户信息响应体：不含敏感字段。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    email: EmailStr
    role: str
    is_active: bool
    created_at: datetime


class TokenOut(BaseModel):
    """登录/刷新成功后返回的令牌对。"""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., description="access_token 有效期（秒）")


class TokenExpirePayload(BaseModel):
    """令牌校验失败时返回给前端的载荷结构。"""

    error_code: str = "TOKEN_EXPIRED"
    message: str = "access_token 已过期，请使用 refresh_token 换发新令牌"
