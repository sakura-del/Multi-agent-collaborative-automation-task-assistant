"""速率限制组件（Phase 1 · P1-D，计划 §1.4）。

设计要点：
- `RateLimiter` 是结构化协议：中间件只依赖 `hit(...)`，实现可替换（Redis / 单机内存）；
- **fail-open 是硬约束**：限流是旁路能力，Redis 超时 / 连接失败 / 未注入时一律放行并记
  warning，绝不向上抛异常把业务请求拖垮；
- Redis 实现用一段 Lua 脚本把 `INCR` 与「首次 `EXPIRE`」绑定成原子操作：
  既避免「INCR 成功但 EXPIRE 未执行」导致窗口永不过期，也避免并发读改写丢计数；
- 窗口内计数如实累加（被拒绝的命中同样计入），返回值第二项是剩余额度（不小于 0）。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Protocol

from app.core.logging import get_logger

logger = get_logger(__name__)

_DEFAULT_KEY_PREFIX = "ratelimit"

# 单机实现最多跟踪的 key 数，超过时惰性清理已过期窗口（防止长期运行内存膨胀）
_MAX_TRACKED_KEYS = 10_000

# Lua：INCR 后仅在首次命中时设置窗口 TTL（固定窗口；原子完成，无 INCR/EXPIRE 崩溃窗口）
_RATE_LIMIT_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return current
"""


class RateLimiter(Protocol):
    """速率限制协议（中间件只依赖此接口，实现可替换）。"""

    async def hit(self, key: str, *, limit: int, window_seconds: int) -> tuple[bool, int]:
        """记录一次命中，返回 ``(是否放行, 剩余额度)``。"""
        ...


class RedisRateLimiter:
    """Redis 固定窗口限流器：``INCR`` + ``EXPIRE``（Lua 原子）；故障时 fail-open 放行。

    键格式 ``{key_prefix}:{window_seconds}:{key}``：窗口长度进 key，避免同一业务键
    在不同路由/不同窗口配置下互相串窗口。
    """

    def __init__(
        self,
        *,
        redis_client: Any | None = None,
        key_prefix: str = _DEFAULT_KEY_PREFIX,
    ) -> None:
        self._redis = redis_client
        self._key_prefix = key_prefix

    def _redis_key(self, key: str, window_seconds: int) -> str:
        return f"{self._key_prefix}:{window_seconds}:{key}"

    async def hit(self, key: str, *, limit: int, window_seconds: int) -> tuple[bool, int]:
        """记录一次命中；Redis 任何异常都放行（fail-open）并记一条 warning。"""
        safe_limit = max(0, int(limit))
        window = max(1, int(window_seconds))
        if self._redis is None:
            logger.warning(
                "rate_limit_redis_unavailable",
                key=key,
                error="未注入 Redis 客户端",
            )
            return True, safe_limit

        redis_key = self._redis_key(key, window)
        try:
            raw = await self._redis.eval(_RATE_LIMIT_SCRIPT, 1, redis_key, window)
            count = int(raw)
        except Exception as exc:
            # fail-open：限流组件故障绝不能拖垮服务，放行并按「未扣减」返回剩余额度
            logger.warning("rate_limit_redis_unavailable", key=key, error=str(exc))
            return True, safe_limit

        remaining = max(safe_limit - count, 0)
        return count <= safe_limit, remaining


class InMemoryRateLimiter:
    """进程内固定窗口限流器：单机部署 / 测试 / Redis 不可用时的兜底实现。

    `clock` 可注入（默认 ``time.monotonic``），便于测试窗口过期而无需 sleep。
    """

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.monotonic
        # 存储键 -> (窗口重置时刻, 窗口内计数)；窗口长度进键，与 Redis 实现口径一致
        self._windows: dict[str, tuple[float, int]] = {}

    @staticmethod
    def _storage_key(key: str, window_seconds: int) -> str:
        return f"{window_seconds}:{key}"

    def _evict_expired(self, now: float) -> None:
        """惰性清理过期窗口，避免 key 无限增长。"""
        expired = [key for key, (reset_at, _) in self._windows.items() if reset_at <= now]
        for key in expired:
            self._windows.pop(key, None)

    async def hit(self, key: str, *, limit: int, window_seconds: int) -> tuple[bool, int]:
        safe_limit = max(0, int(limit))
        window = max(1, int(window_seconds))
        now = self._clock()
        if len(self._windows) >= _MAX_TRACKED_KEYS:
            self._evict_expired(now)

        storage_key = self._storage_key(key, window)
        reset_at, count = self._windows.get(storage_key, (0.0, 0))
        if now >= reset_at:
            reset_at, count = now + window, 0
        count += 1
        self._windows[storage_key] = (reset_at, count)

        if count > safe_limit:
            return False, 0
        return True, max(safe_limit - count, 0)
