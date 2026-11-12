"""速率限制中间件（Phase 1 · P1-D 接线层）。

把 `app.core.rate_limit.RateLimiter` 挂到 HTTP 请求链路：按「路由标签 + 用户/客户端 IP」
计数，超限返回 429（带 `Retry-After`）。

设计要点（与 verifier 的模块分工）：
- `rate_limit.py` 只提供**无状态**的限流原语（`hit()`）；本文件负责
  路由→规则映射、key 构造、Redis 客户端生命周期、熔断降级；
- **fail-open 是硬约束**：限流组件任何异常都必须放行，绝不能拖垮业务请求；
- **专用 Redis 客户端**：`socket_connect_timeout=1.0` / `socket_timeout=1.0`，
  不复用全局单例（否则 Redis 不可达时每个请求都要付数秒连接超时）；
- **连续失败熔断**：达到阈值后本进程不再尝试 Redis，直接放行并记一条 warning
  （与 Issue #3 R5② 的 `ReferenceVerificationCache` 同一思路）；
- 公开端点（健康检查 / 文档 / OpenAPI）不参与限流。
"""

from __future__ import annotations

import ipaddress
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.rate_limit import InMemoryRateLimiter, RateLimiter, RedisRateLimiter

logger = get_logger(__name__)

#: 不参与限流的路径前缀（探针/文档/静态）
_SKIP_PREFIXES: tuple[str, ...] = (
    "/api/v1/health",
    "/api/v1/ws",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/metrics",
)

#: 连续失败达到该次数后熔断（本进程不再尝试 Redis）
_CIRCUIT_BREAKER_THRESHOLD = 3

#: route label → (HTTP 方法, 路径前缀) 的规则选择
#: 顺序即优先级，先匹配者生效；未匹配的路径不参与限流（避免误伤长尾端点）。
#:
#: ⚠️ **所有写操作端点都必须在这里有对应规则**（`tests/test_rate_limit_coverage_and_quota.py`
#: 会逐条校验）。遗漏的后果真实存在：上传/渲染/删除等昂贵写操作可被单客户端无限刷。
_ROUTE_RULES: tuple[tuple[str, str, str], ...] = (
    # ===== 认证（含账号生命周期：验证/重置都发信，必须限流）=====
    ("auth_login", "POST", "/api/v1/auth/login"),
    ("auth_register", "POST", "/api/v1/auth/register"),
    ("auth_register", "POST", "/api/v1/auth/password-reset/request"),
    ("auth_register", "POST", "/api/v1/auth/password-reset/confirm"),
    ("auth_register", "POST", "/api/v1/auth/verify-email/request"),
    ("auth_register", "POST", "/api/v1/auth/verify-email/confirm"),
    # ===== 任务（创建单独限额；确认/控制/WS ticket 归 task_control）=====
    ("task_create", "POST", "/api/v1/tasks"),
    ("task_control", "POST", "/api/v1/tasks/"),
    # ===== 知识库（上传与删除都算「写」）=====
    ("upload", "POST", "/api/v1/knowledge/documents"),
    ("upload", "DELETE", "/api/v1/knowledge/documents"),
    ("search", "POST", "/api/v1/knowledge/search"),
    # ===== 引用样式（上传/删除都是外部文件解析）=====
    ("verify", "POST", "/api/v1/citation-styles"),
    ("verify", "DELETE", "/api/v1/citation-styles"),
    # ===== Word 模板（上传/删除；渲染单独更严的限额——它要跑 docxtpl）=====
    ("template_render", "POST", "/api/v1/templates/"),
    ("template_write", "POST", "/api/v1/templates"),
    ("template_write", "DELETE", "/api/v1/templates"),
    # ===== 参考文献导入 =====
    ("verify", "POST", "/api/v1/references/import"),
)


def route_rule_label(method: str, path: str) -> str | None:
    """按方法与路径前缀解析限流规则标签；不参与限流的请求返回 None。"""
    upper_method = method.upper()
    for label, rule_method, prefix in _ROUTE_RULES:
        if upper_method != rule_method:
            continue
        # `/api/v1/tasks` 精确匹配「创建任务」，其子路径归 task_control
        if prefix == "/api/v1/tasks":
            if path.rstrip("/") == "/api/v1/tasks":
                return label
            continue
        if path.startswith(prefix):
            return label
    return None


def _normalize_ip(raw: str | None) -> str | None:
    """把 `X-Real-IP` 的值解析成规范 IP；非法值返回 None。

    只用标准库 `ipaddress` 判断，不做"看起来像 IP"的字符串猜测：`X-Real-IP` 由网关写，
    出现非 IP 说明请求绕过了网关（直连源站伪造），此时必须不采信。
    """
    if not raw:
        return None
    candidate = raw.strip()
    if not candidate or "," in candidate:
        # nginx 的 `$remote_addr` 永远是单个地址；带逗号说明不是网关写的
        return None
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def _client_identity(request: Request, *, trust_proxy: bool) -> str:
    """限流主体的稳定标识：优先登录用户，其次客户端 IP。

    `trust_proxy=False`（默认）时用 socket 对端地址——直连部署/本地开发就是这样。

    `trust_proxy=True` 时**只认 `X-Real-IP`**，且必须是合法 IP：

    - `X-Real-IP` 由 nginx 用 `$remote_addr` **无条件覆盖**写，客户端伪造的值会被冲掉；
    - `X-Forwarded-For` 一律不看。nginx 的 `$proxy_add_x_forwarded_for` 是
      「客户端自带的 XFF + 真实地址」拼接，第一段正是客户端自己写的——
      2026-11-11 的原实现取的就是第一段（`forwarded.split(",")[0]`），
      等于任何人加一个 `X-Forwarded-For: 1.1.1.1` 就换了一个限流桶。

    生产还要求源站只接受 Cloudflare 回源（`set_real_ip_from`），否则直连源站者
    无法伪造 `$remote_addr` 但仍可占用连接——那一层由 nginx 负责。
    """
    user_id = getattr(request.state, "user_id", None)
    if user_id:
        return f"user:{user_id}"
    if trust_proxy:
        gateway_ip = _normalize_ip(request.headers.get("x-real-ip"))
        if gateway_ip is not None:
            return f"ip:{gateway_ip}"
    client = request.client
    return f"ip:{client.host if client else 'unknown'}"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """按路由规则限流；任何内部异常一律放行（fail-open）。"""

    def __init__(self, app: Any, *, limiter: RateLimiter | None = None) -> None:
        super().__init__(app)
        cfg = get_settings().rate_limit
        self._enabled = cfg.enabled
        self._trust_proxy = cfg.trust_proxy
        self._rules = cfg.rules
        self._limiter = limiter or self._build_limiter()
        self._consecutive_failures = 0
        self._circuit_open = False

    def _build_limiter(self) -> RateLimiter:
        """构造限流器：优先专用 Redis 客户端，失败则退化为内存实现。"""
        cfg = get_settings().rate_limit
        if cfg.backend == "memory":
            return InMemoryRateLimiter()
        try:
            import redis.asyncio as aioredis

            redis_cfg = get_settings().redis
            # 专用客户端：短超时，避免 Redis 不可达时每请求都付长连接超时
            client = aioredis.from_url(
                redis_cfg.url,
                socket_connect_timeout=1.0,
                socket_timeout=1.0,
                decode_responses=False,
            )
            return RedisRateLimiter(redis_client=client, key_prefix=cfg.key_prefix)
        except Exception as exc:  # pragma: no cover - 依赖缺失/构造失败时的兜底
            logger.warning("rate_limit_redis_unavailable_fallback_memory", error=str(exc))
            return InMemoryRateLimiter()

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if not self._enabled:
            return await call_next(request)
        path = request.url.path
        if any(path.startswith(prefix) for prefix in _SKIP_PREFIXES):
            return await call_next(request)
        label = route_rule_label(request.method, path)
        if label is None:
            return await call_next(request)

        rule = getattr(self._rules, label)
        if self._circuit_open:
            # 熔断期：直接放行，不尝试 Redis
            return await call_next(request)

        identity = _client_identity(request, trust_proxy=self._trust_proxy)
        try:
            allowed, remaining = await self._limiter.hit(
                f"{label}:{identity}",
                limit=rule.limit,
                window_seconds=rule.window_seconds,
            )
            self._consecutive_failures = 0
        except Exception as exc:
            # fail-open：限流组件故障绝不能拦业务请求
            self._consecutive_failures += 1
            logger.warning(
                "rate_limit_check_failed",
                route=label,
                identity=identity,
                error=str(exc),
                consecutive_failures=self._consecutive_failures,
            )
            if self._consecutive_failures >= _CIRCUIT_BREAKER_THRESHOLD:
                self._circuit_open = True
                logger.warning("rate_limit_circuit_open", route=label)
            return await call_next(request)

        if allowed:
            return await call_next(request)

        # 可观测性：限流命中要能看到（"谁在刷"与"我们限额太紧"都需要这个数据）。
        # 标签用**规则名**（task_create 等低基数枚举），不是真实路径。
        from app.core import metrics as metrics_module

        metrics_module.inc_counter("maa_rate_limit_rejected", route=label)
        return JSONResponse(
            status_code=429,
            content={
                "error_code": "RATE_LIMITED",
                "message": "请求过于频繁，请稍后重试",
                "details": {
                    "route": label,
                    "limit": rule.limit,
                    "window_seconds": rule.window_seconds,
                    "remaining": remaining,
                },
            },
            headers={"Retry-After": str(max(1, rule.window_seconds))},
        )
