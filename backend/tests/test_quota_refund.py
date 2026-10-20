"""配额退还（Phase 1 P1-D 收尾：实际用量校正）。

## 背景

任务提交时按 `ESTIMATED_TOKENS_PER_TASK`（100k）**预扣**配额——这是唯一能
事前拒绝的位置（超限直接 429，任务不创建）。但预估必然不准：

- 实际用量少于预估 → 多扣了用户额度（不公平，且会让用户当天额度提前耗尽）；
- 实际用量多于预估 → 少扣了（成本失控）。

因此任务结束后必须按**实际用量校正**：退还差额。

## 本文件锁定

`QuotaService.refund(user_id, tokens)`：
- 原子地减少已用量，且**不会减到负数**（并发/重复退还不能把额度刷成正收益）；
- fail-open：Redis 故障时静默跳过（与 `check_and_consume` 一致）；
- `daily_limit < 0`（关闭配额）时为空操作。
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.core.quota import QuotaService


class _FakeRedis:
    """最小 Redis 替身：按脚本内容区分「扣减（INCRBY）」与「退还（SET）」。

    真实实现里两者都是 Lua 原子脚本：扣减用 `INCRBY`、退还用 `SET target`。
    替身必须按脚本语义分别模拟，否则测不出「退还」的行为。
    """

    def __init__(self, *, initial: int = 0, error: Exception | None = None) -> None:
        self.value = initial
        self.error = error
        self.calls: list[tuple[Any, ...]] = []

    async def eval(self, script: str, numkeys: int, key: str, *args: Any) -> Any:
        self.calls.append((script, numkeys, key, *args))
        if self.error is not None:
            raise self.error
        amount = int(args[0])
        if "SET" in script:
            # 退还脚本：SET max(0, used - amount)，不扣到负数
            self.value = max(0, self.value - amount)
            return self.value
        # 扣减脚本：INCRBY
        self.value += amount
        return self.value


class TestRefund:
    @pytest.mark.asyncio
    async def test_refund_reduces_usage(self) -> None:
        redis = _FakeRedis(initial=100_000)
        service = QuotaService(redis_client=redis, daily_limit=1_000_000)

        await service.refund(uuid.uuid4(), tokens=60_000)

        assert redis.value == 40_000, "退还后已用量应减少"

    @pytest.mark.asyncio
    async def test_refund_never_goes_negative(self) -> None:
        """退还超过已用量时不能减成负数（否则可被用来刷额度）。"""
        redis = _FakeRedis(initial=10_000)
        service = QuotaService(redis_client=redis, daily_limit=1_000_000)

        await service.refund(uuid.uuid4(), tokens=50_000)

        assert redis.value == 0

    @pytest.mark.asyncio
    async def test_refund_is_noop_for_non_positive_amount(self) -> None:
        redis = _FakeRedis(initial=1000)
        service = QuotaService(redis_client=redis, daily_limit=1_000_000)

        await service.refund(uuid.uuid4(), tokens=0)
        await service.refund(uuid.uuid4(), tokens=-5)

        assert redis.calls == [], "非正数退还不应触碰 Redis"

    @pytest.mark.asyncio
    async def test_refund_fails_open_on_redis_error(self) -> None:
        """Redis 故障时静默跳过（与 check_and_consume 的 fail-open 一致）。"""
        redis = _FakeRedis(initial=1000, error=ConnectionError("redis down"))
        service = QuotaService(redis_client=redis, daily_limit=1_000_000)

        # 不应抛异常
        await service.refund(uuid.uuid4(), tokens=100)

    @pytest.mark.asyncio
    async def test_refund_is_noop_when_quota_disabled(self) -> None:
        redis = _FakeRedis(initial=1000)
        service = QuotaService(redis_client=redis, daily_limit=-1)

        await service.refund(uuid.uuid4(), tokens=100)

        assert redis.calls == [], "配额关闭时退还应为空操作"

    @pytest.mark.asyncio
    async def test_refund_is_noop_without_redis(self) -> None:
        service = QuotaService(redis_client=None, daily_limit=1_000_000)

        await service.refund(uuid.uuid4(), tokens=100)  # 不应抛异常
