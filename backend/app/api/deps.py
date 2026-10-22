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
from app.core.security import TOKEN_TYPE_ACCESS, decode_token, utc_timestamp
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
        # ⚠️ 必须用 utc_timestamp：SQLite 读回的 datetime 是 naive，
        # 直接 `.timestamp()` 会按**本地时区**解释（UTC+8 下少算 8 小时 → 强制下线失效）
        if token_iat < utc_timestamp(user.token_invalid_before):
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


# ===== 双通道鉴权（Phase 2 开放 API）=====

#: 开放 API 的 Key 请求头。
API_KEY_HEADER = "X-API-Key"

#: 请求状态上标记「本次请求是用 Key 认证的」，供 `require_scope` 判断。
_STATE_AUTH_VIA_KEY = "auth_via_api_key"

#: 请求状态上挂 ApiKey 记录（scope 校验与审计用）。
_STATE_API_KEY = "api_key_record"


def _single_session_factory(db: AsyncSession):  # type: ignore[no-untyped-def]
    """把请求级 session 适配成 `session_factory`（服务层签名要求）。

    注意：这里 yield 的是**同一个** session，不 commit/close —— 由 `get_db`
    统一管理事务边界。API Key 的 `last_used_at` 更新会 commit，与端点自己的
    事务共用一个 session 是可接受的（都在同一请求内）。
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _factory():  # type: ignore[no-untyped-def]
        yield db

    return _factory


async def get_current_user_or_api_key(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """双通道鉴权：**优先 `X-API-Key`，其次 `Authorization: Bearer`**。

    ## 为什么需要两条通道

    网页前端用 JWT（既有 400+ 测试与全部前端代码依赖它），开放 API / MCP 用
    Key（自动化工具不适合走登录流程）。两条通道**物理隔离**：

    - Key 塞进 `Authorization: Bearer` → 401（`decode_token` 不认）；
    - JWT 塞进 `X-API-Key` → 401（哈希查不到）。

    ## 为什么 Key 优先

    显式通道优先于隐式：客户端主动给了 Key，说明它就是想用 Key 的身份与 scope。
    若 JWT 优先，一个带旧 JWT 的自动化脚本会意外以「网页用户」身份绕过 scope。

    ## scope 只对 Key 生效

    JWT 用户走既有角色逻辑（`require_scope` 对 JWT 请求直接放行）。
    否则所有网页操作都会被 scope 卡死 —— 这是本任务最大的风险点，
    已用 `test_jwt_skips_scope_check` 钉死。
    """
    from app.security.api_key_service import ApiKeyService

    raw_key = request.headers.get(API_KEY_HEADER)
    if raw_key:
        service = ApiKeyService(_single_session_factory(db))
        result = await service.authenticate(raw_key)
        if result is None:
            # 统一 401，**不解释**失效原因（吊销/过期/不存在都会泄漏信息）
            raise UnauthorizedException("API Key 无效")
        record, user = result
        request.state.user = user
        setattr(request.state, _STATE_AUTH_VIA_KEY, True)
        setattr(request.state, _STATE_API_KEY, record)

        # Phase 2 计费：**开放 API 访问权由套餐决定**。
        # 免费档 `allow_api_access=False` → 403（不是 401：Key 本身有效）。
        # 为什么不放在 `require_scope` 里：scope 是「这个 Key 能做什么」，
        # 套餐是「这个用户能不能用 API」—— 两个正交维度，混在一起会让
        # 错误消息无法区分「Key 权限不足」与「套餐不含 API」。
        await _require_api_access(db, user)
        return user

    return await get_current_user(request, db)


async def _require_api_access(db: AsyncSession, user: User) -> None:
    """校验用户套餐含开放 API 权限（仅对 **Key 认证** 的请求调用）。

    JWT 请求**不检查**：网页是免费档的唯一入口，若也拦就没法用了。

    套餐表缺失（测试用 create_all 建表时没有种子）时**放行**：
    计费配置问题不该让开放 API 全面 403（fail-open，与配额同策略）。
    """
    from contextlib import asynccontextmanager

    from app.billing.services.plan_service import FeatureNotAvailable, PlanService

    @asynccontextmanager
    async def _factory():  # type: ignore[no-untyped-def]
        yield db

    service = PlanService(_factory)  # type: ignore[arg-type]
    # 先确认套餐表里有种子数据。**空表 → 放行**（fail-open）：
    # 计费配置缺失（测试用 create_all 建表、或误删 plan 行）不该让
    # 开放 API 全面 403。这与配额/限流的 fail-open 同策略。
    if not await _plan_table_seeded(db):
        return
    plan = await service.get_active_plan(user.id)
    if not plan.allow_api_access:
        raise FeatureNotAvailable(feature="api", plan_id=plan.id, label="开放 API")


async def _plan_table_seeded(db: AsyncSession) -> bool:
    """`plan` 表是否有种子数据（用于 fail-open 判定）。"""
    from sqlalchemy import func, select

    from app.db.models.billing import Plan

    try:
        count = await db.scalar(select(func.count()).select_from(Plan))
    except Exception:  # pragma: no cover - 表不存在时也放行
        return False
    return bool(count)


def require_scope(scope: str):  # type: ignore[no-untyped-def]
    """声明端点需要的 scope（**只对 Key 认证的请求生效**）。

    用法：

    ```python
    async def create_task(
        user: User = Depends(get_current_user_or_api_key),
        _: None = Depends(require_scope("tasks:write")),
    ): ...
    ```

    实现要点：scope 从 `request.state` 读，而不是从参数 —— 这样端点签名不必
    感知「本次是 Key 还是 JWT」，保持既有端点签名不变（改动面最小）。
    """
    from app.security.api_key_service import ApiKeyService

    async def _checker(request: Request) -> None:
        if not getattr(request.state, _STATE_AUTH_VIA_KEY, False):
            # JWT 请求：不校验 scope（走既有角色逻辑）
            return
        record = getattr(request.state, _STATE_API_KEY, None)
        if record is None:  # pragma: no cover - 依赖顺序保证
            raise UnauthorizedException("API Key 无效")
        if not ApiKeyService.has_scope(record, scope):
            raise ForbiddenException(
                f"API Key 缺少所需权限：{scope}（当前：{', '.join(record.scopes or [])}）"
            )

    return _checker
