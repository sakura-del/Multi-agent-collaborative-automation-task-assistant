"""按用户配额（Phase 1 · P1-D，计划 §1.4）。

- `QuotaService.check_and_consume(user_id, *, tokens)`：按**日窗口**（默认 UTC 日，可通过
  ``window_seconds`` 对齐到任意固定窗口）扣减 token 配额；超限抛 ``QuotaExceededError``
  （HTTP 429，带 ``retry_after`` 与已用/上限/剩余额度）。
- 判定与扣减由一段 Lua 脚本**原子**完成（``GET`` + 比较 + ``INCRBY`` + 首次 ``EXPIRE``）：
  并发下不会超卖，且**被拒绝的请求不扣减**。
- **fail-open**：Redis 不可用 / 未注入时放行并记 warning —— 配额是旁路控制，
  绝不能因为它的故障把业务请求拖垮。
- 阈值 / 窗口 / 时钟全部是构造参数，便于测试与后续配置驱动。
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.core.exceptions import AppException
from app.core.logging import get_logger

logger = get_logger(__name__)

DEFAULT_DAILY_TOKEN_LIMIT = 1_000_000
"""`QuotaService` **直接构造**时的默认日配额（与 ``config.llm.cost_budget.daily_token_limit`` 对齐）。

注意：`config.quota.daily_token_limit` 的默认值是 1_000_000，``-1`` 表示关闭配额；
接线层会按配置显式传入 ``daily_limit``，本常量只影响未显式传参的直接构造。
"""

_SECONDS_PER_DAY = 86_400
_DEFAULT_KEY_PREFIX = "quota"

# Lua：先比较后扣减（原子）；仅首次成功扣减时设置窗口 TTL；被拒绝不扣减
_QUOTA_SCRIPT = """
local used = tonumber(redis.call('GET', KEYS[1]) or '0')
local amount = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
if used + amount > limit then
    return {0, used}
end
local new_used = redis.call('INCRBY', KEYS[1], amount)
if new_used == amount then
    redis.call('EXPIRE', KEYS[1], ARGV[3])
end
return {1, new_used}
"""

# Lua：退还（原子，**不扣到负数**）。
# 为什么必须原子：并发退还 / 重复退还可能读到旧值后写回，导致「越退越多」——
# 额度就变成了可刷的正收益。用 Lua 在服务端一次性判定。
_REFUND_SCRIPT = """
local used = tonumber(redis.call('GET', KEYS[1]) or '0')
local amount = tonumber(ARGV[1])
local target = used - amount
if target < 0 then
    target = 0
end
redis.call('SET', KEYS[1], target, 'KEEPTTL')
return target
"""


class QuotaExceededError(AppException):
    """按用户配额耗尽（当前窗口内已用满），HTTP 429。

    刻意定义在 `quota.py`（而不是 `core/exceptions.py`）：配额是 P1-D 的内部概念，
    避免核心异常模块被业务细节污染。
    """

    status_code = 429
    default_message = "今日配额已用尽"
    error_code = "QUOTA_EXCEEDED"

    def __init__(
        self,
        message: str | None = None,
        *,
        retry_after: int = 60,
        used: int = 0,
        limit: int = 0,
    ) -> None:
        retry = max(1, int(retry_after))
        self.retry_after = retry
        """建议客户端等待的秒数（距当前窗口结束）；接线层应写入 ``Retry-After`` 响应头。"""
        self.used = int(used)
        self.limit = int(limit)
        super().__init__(
            message or self.default_message,
            details={
                "retry_after": retry,
                "used": self.used,
                "limit": self.limit,
                "remaining": 0,
            },
        )


class QuotaService:
    """按用户、按固定窗口（默认日）的 token 配额。

    语义（与 `config.quota` 对齐，默认 ``daily_token_limit=1_000_000``）：
    - ``daily_limit < 0`` → **关闭配额**（`-1` 为配置哨兵，直接放行且不触碰 Redis）；
    - ``daily_limit == 0`` → 严格：任何正数 tokens 都会超限；
    - ``tokens <= 0`` → 空操作；
    - 窗口按 epoch 对齐：``window_seconds=86400`` 即 **UTC 日** 边界重置。

    本类**不读取全局配置**：阈值 / 窗口 / Redis 客户端 / 时钟全部由接线层注入，
    单测因此不依赖 `get_settings()`，也便于测试边界值。
    """

    def __init__(
        self,
        *,
        redis_client: Any | None = None,
        daily_limit: int = DEFAULT_DAILY_TOKEN_LIMIT,
        window_seconds: int = _SECONDS_PER_DAY,
        clock: Callable[[], datetime] | None = None,
        key_prefix: str = _DEFAULT_KEY_PREFIX,
    ) -> None:
        self._redis = redis_client
        self._daily_limit = int(daily_limit)
        self._window_seconds = max(1, int(window_seconds))
        self._clock = clock or (lambda: datetime.now(UTC))
        self._key_prefix = key_prefix

    def _window(self, user_id: UUID) -> tuple[str, int]:
        """返回 ``(窗口 key, retry_after)``。

        日窗口 key 含 UTC 日期（便于运维排查），自定义窗口用窗口序号；
        ``retry_after`` 为距本窗口结束的秒数（向上取整，至少 1）。
        """
        now = self._clock()
        timestamp = now.timestamp()
        if self._window_seconds == _SECONDS_PER_DAY:
            suffix = now.astimezone(UTC).strftime("%Y-%m-%d")
        else:
            suffix = f"w{int(timestamp // self._window_seconds)}"
        window_end = (math.floor(timestamp / self._window_seconds) + 1) * self._window_seconds
        retry_after = max(1, math.ceil(window_end - timestamp))
        return f"{self._key_prefix}:{user_id}:{suffix}", retry_after

    async def check_and_consume(self, user_id: UUID, *, tokens: int) -> None:
        """判定并扣减配额；超限抛 ``QuotaExceededError``，Redis 故障时放行（fail-open）。"""
        amount = int(tokens)
        if amount <= 0:
            return
        if self._daily_limit < 0:
            return  # -1 = 配置哨兵：关闭配额，直接放行且不触碰 Redis
        if self._redis is None:
            logger.warning(
                "quota_redis_unavailable",
                user_id=str(user_id),
                error="未注入 Redis 客户端",
            )
            return

        redis_key, retry_after = self._window(user_id)
        try:
            raw = await self._redis.eval(
                _QUOTA_SCRIPT, 1, redis_key, amount, self._daily_limit, retry_after
            )
            allowed, used = int(raw[0]), int(raw[1])
        except Exception as exc:
            # fail-open：配额组件故障绝不能拖垮业务，放行并记 warning
            logger.warning("quota_redis_unavailable", user_id=str(user_id), error=str(exc))
            return

        if not allowed:
            raise QuotaExceededError(
                retry_after=retry_after,
                used=used,
                limit=self._daily_limit,
            )

    async def refund(self, user_id: UUID, *, tokens: int) -> None:
        """退还配额（任务结束后按**实际用量**校正预扣）。

        提交任务时按 `ESTIMATED_TOKENS_PER_TASK` 预扣——这是唯一能**事前拒绝**的位置；
        但预估必然不准，故执行完成后按实际用量退还差额（`预估 - 实际`）。

        语义：
        - 原子且**不扣到负数**（并发/重复退还不会把额度刷成正收益）；
        - ``tokens <= 0`` 空操作；``daily_limit < 0``（关闭配额）空操作；
        - Redis 故障时静默跳过（fail-open，与 `check_and_consume` 一致）。
        """
        amount = int(tokens)
        if amount <= 0:
            return
        if self._daily_limit < 0:
            return  # 关闭配额：从未扣减，自然无需退还
        if self._redis is None:
            logger.warning(
                "quota_redis_unavailable",
                user_id=str(user_id),
                error="未注入 Redis 客户端（退还跳过）",
            )
            return

        redis_key, _ = self._window(user_id)
        try:
            await self._redis.eval(_REFUND_SCRIPT, 1, redis_key, amount)
        except Exception as exc:
            logger.warning("quota_redis_unavailable", user_id=str(user_id), error=str(exc))
