"""FastAPI 依赖项：鉴权与当前用户上下文。

阶段0任务3：JWT 鉴权 + user_id 隔离。

设计要点：
- get_current_user: 解析 access_token → 查库 → 返回 User 对象
- get_current_active_user: 确保账号未禁用
- get_current_admin_user: 确保角色为 admin

用户隔离约定：
所有按 user_id 隔离的资源查询（Task/Subtask/KnowledgeDoc/HumanFeedback），
在 service 层调用时必须传入 current_user.id 作为 WHERE 过滤条件，
从查询层面杜绝越权访问他人数据；查不到即返回 404，避免通过 403 泄漏资源存在性。
"""

from __future__ import annotations

import uuid

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenException, UnauthorizedException
from app.core.security import TOKEN_TYPE_ACCESS, decode_token
from app.db.models.user import User
from app.db.session import get_db


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """解析请求中的 access_token 并返回当前用户。

    Token 来源：Authorization: Bearer <access_token>
    中间件已尝试预解析 user_id/iat 到 request.state（若存在），此处复用避免重复解码。
    """
    # 优先读取中间件预解析的 user_id（中间件不查库，仅解码）
    user_id_str: str | None = getattr(request.state, "user_id", None)
    token_iat: float | None = getattr(request.state, "token_iat", None)

    if user_id_str is None:
        # 中间件未解析到，此处自行从 Authorization 头解析
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            raise UnauthorizedException("缺少有效的鉴权令牌")
        token = auth_header.removeprefix("Bearer ").strip()
        payload = decode_token(token, expected_type=TOKEN_TYPE_ACCESS)
        user_id_str = payload.get("sub")
        token_iat = payload.get("iat")

    if not user_id_str:
        raise UnauthorizedException("令牌缺少用户标识")

    try:
        user_id = uuid.UUID(user_id_str)
    except (ValueError, TypeError) as exc:
        raise UnauthorizedException("令牌用户标识格式非法") from exc

    # 查库获取最新用户状态（支持强制下线 / 禁用即时生效）
    user = await db.get(User, user_id)
    if user is None:
        raise UnauthorizedException("用户不存在")

    # 强制下线检查：令牌签发时间早于 token_invalid_before 则失效
    if user.token_invalid_before is not None and token_iat is not None:
        # token_invalid_before 是 timezone-aware datetime，转 Unix 秒比较
        if token_iat < user.token_invalid_before.timestamp():
            raise UnauthorizedException("令牌已被强制失效，请重新登录")

    # 把用户对象挂到 request.state，后续 service 层可直接取用
    request.state.user = user
    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """确保当前账号处于启用状态。"""
    if not current_user.is_active:
        raise UnauthorizedException("账号已被禁用")
    return current_user


async def get_current_admin_user(
    current_user: User = Depends(get_current_active_user),
) -> User:
    """要求当前用户为管理员。"""
    if current_user.role != "admin":
        raise ForbiddenException("需要管理员权限")
    return current_user
