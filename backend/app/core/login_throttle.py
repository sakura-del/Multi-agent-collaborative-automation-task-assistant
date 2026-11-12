"""登录失败退避：按账号累计失败次数并临时锁定（Issue #11 M-3）。

## 为什么需要它

IP 维度的限流挡不住两件事：

1. **慢速爆破**：60 秒 5 次以内完全合规，一天能试 7200 次；
2. **换 IP 继续试**：同一账号从不同出口轮流试，IP 桶各算各的。

所以需要按**账号**累计失败：连续失败 N 次后锁定一段时间，期间即使密码正确也拒绝
（否则爆破者只要猜中就照常登录，退避等于没做）。

## 设计取舍

- **只放内存/Redis 两种实现，计数不做持久化**：这是临时状态，重启丢失可接受
  （代价只是重新累计失败次数，不影响已登录会话）；
- **key 用账号名的哈希**：Redis 里不该出现明文用户名，`KEYS`/监控面板都会看到；
- **失败即 fail-open**：由调用方 try/except，组件故障不能把所有人挡在门外；
- **锁定按规范化账号名**（去空格 + 小写）：否则 `Demo` / `demo` 各记一份，换个大小写就绕过。
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from typing import Any, Protocol

from app.core.logging import get_logger

logger = get_logger(__name__)

#: 单机实现最多跟踪的账号数（防止被随机账号名刷爆内存）
_MAX_TRACKED_ACCOUNTS = 10_000

# Lua：INCR 后仅在首次计数时设置窗口 TTL，保证「计数」和「过期」原子完成
_FAILURE_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return current
"""


def normalize_account(account: str) -> str:
    """账号名规范化：去首尾空白 + 转小写。

    登录本身按原样查询数据库（用户名是否大小写敏感由数据决定），
    但**失败计数必须归一**，否则换个大小写就是一份新计数器。
    """
    return account.strip().lower()


def _account_key(account: str) -> str:
    """账号名转存储键：哈希后取前 32 位，避免明文用户名出现在 Redis key 里。"""
    digest = hashlib.sha256(account.encode("utf-8")).hexdigest()
    return f"login_fail:{digest[:32]}"


class LoginThrottle(Protocol):
    """登录失败退避协议（端点只依赖这三个方法，实现可替换）。"""

    async def check(self, account: str) -> int | None:
        """返回剩余锁定秒数；未锁定返回 ``None``。"""
        ...

    async def register_failure(self, account: str) -> int:
        """记录一次失败，返回当前窗口内的累计次数。"""
        ...

    async def reset(self, account: str) -> None:
        """清零该账号的失败计数（登录成功时调用）。"""
        ...


class InMemoryLoginThrottle:
    """进程内实现：单机 / 测试 / Redis 不可用时的兜底。"""

    def __init__(
        self,
        *,
        limit: int,
        window_seconds: int,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._limit = max(1, int(limit))
        self._window = max(1, int(window_seconds))
        self._clock = clock or time.monotonic
        # account -> (窗口重置时刻, 累计次数)
        self._entries: dict[str, tuple[float, int]] = {}

    def _evict_expired(self, now: float) -> None:
        expired = [key for key, (reset_at, _) in self._entries.items() if reset_at <= now]
        for key in expired:
            self._entries.pop(key, None)

    async def check(self, account: str) -> int | None:
        now = self._clock()
        entry = self._entries.get(account)
        if entry is None:
            return None
        reset_at, count = entry
        if now >= reset_at:
            self._entries.pop(account, None)
            return None
        if count < self._limit:
            return None
        return max(1, round(reset_at - now))

    async def register_failure(self, account: str) -> int:
        now = self._clock()
        if len(self._entries) >= _MAX_TRACKED_ACCOUNTS:
            self._evict_expired(now)

        reset_at, count = self._entries.get(account, (0.0, 0))
        if now >= reset_at:
            reset_at, count = now + self._window, 0
        count += 1
        self._entries[account] = (reset_at, count)
        return count

    async def reset(self, account: str) -> None:
        self._entries.pop(account, None)


class RedisLoginThrottle:
    """Redis 实现：多进程/多副本共享同一份计数（生产用）。"""

    def __init__(
        self,
        *,
        redis_client: Any,
        limit: int,
        window_seconds: int,
        key_prefix: str = "ratelimit",
    ) -> None:
        self._redis = redis_client
        self._limit = max(1, int(limit))
        self._window = max(1, int(window_seconds))
        self._key_prefix = key_prefix

    def _key(self, account: str) -> str:
        return f"{self._key_prefix}:{_account_key(account)}"

    async def check(self, account: str) -> int | None:
        key = self._key(account)
        raw = await self._redis.get(key)
        if raw is None:
            return None
        try:
            count = int(raw)
        except (TypeError, ValueError):
            return None
        if count < self._limit:
            return None
        ttl = await self._redis.ttl(key)
        return max(1, int(ttl or self._window))

    async def register_failure(self, account: str) -> int:
        raw = await self._redis.eval(_FAILURE_SCRIPT, 1, self._key(account), self._window)
        return int(raw)

    async def reset(self, account: str) -> None:
        await self._redis.delete(self._key(account))
