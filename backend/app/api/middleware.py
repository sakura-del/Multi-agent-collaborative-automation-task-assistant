"""鉴权中间件：轻量级用户上下文预注入。

阶段0任务3：FastAPI 中间件校验 Token 并注入当前用户上下文。

设计原则：
- 仅做 token 解码与上下文预注入，不查库、不强制拦截
- 解码失败静默留空，由路由层 Depends(get_current_user) 决定是否拒绝
- 公开端点（/health、/docs、/api/v1/auth/*）不受影响

与依赖项的分工：
  中间件 → 解码 token，存 user_id / iat 到 request.state（无 DB 开销）
  依赖项 → 复用 request.state，查库校验用户状态与强制下线
两者协同实现"中间件注入上下文 + 依赖项精确鉴权"。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.security import TOKEN_TYPE_ACCESS, decode_token


class AuthContextMiddleware(BaseHTTPMiddleware):
    """预解析 Authorization 头，把 user_id / iat 注入 request.state。

    不做任何拦截，仅"尽力而为"地填充上下文：
    - 有效 token：request.state.user_id / request.state.token_iat 被设置
    - 无 token 或无效：上述属性不存在，依赖项会按需返回 401
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.removeprefix("Bearer ").strip()
            try:
                payload = decode_token(token, expected_type=TOKEN_TYPE_ACCESS)
                # 仅存可信字段，sub(user_id) 与 iat(签发时间) 供依赖项做强制下线校验
                request.state.user_id = payload.get("sub")
                request.state.token_iat = payload.get("iat")
            except Exception:
                # token 无效/过期：静默跳过，由下游依赖项决定是否拒绝
                # 不在中间件层抛错，保证公开端点与无 token 请求正常通过
                pass

        return await call_next(request)
