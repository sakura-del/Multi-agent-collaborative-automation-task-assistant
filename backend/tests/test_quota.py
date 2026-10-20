"""按用户配额测试（Phase 1 · P1-D，计划 §1.4）。

覆盖：
1. 正常扣减、边界（刚好用尽允许 / 再多 1 token 拒绝且**不扣减**）；
2. 按日窗口重置（注入时钟跨日）；
3. 并发扣减原子性（INCRBY 语义）；
4. **Redis 故障 fail-open**：异常 / 未注入客户端都放行并记 warning，绝不抛非配额异常；
5. `QuotaExceededError` 契约：继承 `AppException`、429、带 `retry_after` 与剩余额度。

不依赖真实 Redis：正常路径用 fakeredis，故障路径用抛异常的异步替身。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import fakeredis.aioredis
import pytest
import pytest_asyncio
from redis import exceptions as redis_exceptions

from app.core import quota as quota_module
from app.core.exceptions import AppException
from app.core.quota import DEFAULT_DAILY_TOKEN_LIMIT, QuotaExceededError, QuotaService

USER_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")
DAY_ONE = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)
DAY_TWO = datetime(2026, 3, 2, 0, 0, 1, tzinfo=UTC)


# ===== 测试替身 =====


class BrokenRedis:
    """所有命令都抛异常的异步 Redis 替身（模拟 Redis 不可用）。"""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error or ConnectionError("redis down")
        self.calls = 0

    async def eval(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        raise self.error

    async def incrby(self, key: str, amount: int) -> int:
        self.calls += 1
        raise self.error


class FlakyRedis:
    """前 `fail_after` 次委托给 fakeredis（真实语义），之后全部抛异常。

    用于证明「fail-open 是运行期真实行为」：同一个实例先按配额拦截，Redis 掉线后放行。
    """

    def __init__(self, backing: Any, *, fail_after: int) -> None:
        self._backing = backing
        self._fail_after = fail_after
        self.calls = 0

    async def eval(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        if self.calls > self._fail_after:
            raise redis_exceptions.ConnectionError("redis went away")
        return await self._backing.eval(*args, **kwargs)


class RecordingLogger:
    """记录 structlog 事件，用于断言 fail-open warning。"""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def warning(self, event: str, **kwargs: Any) -> None:
        self.events.append((event, kwargs))

    @property
    def event_names(self) -> list[str]:
        return [event for event, _ in self.events]


class MutableClock:
    """可设置的时钟（配额窗口按墙上时间对齐，需 datetime）。"""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest_asyncio.fixture
async def fake_redis() -> AsyncIterator[fakeredis.aioredis.FakeRedis]:
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


def _service(
    client: Any,
    *,
    daily_limit: int = 1000,
    clock: MutableClock | None = None,
) -> QuotaService:
    return QuotaService(
        redis_client=client,
        daily_limit=daily_limit,
        clock=clock or MutableClock(DAY_ONE),
    )


# ===== 异常契约 =====


class TestQuotaExceededError:
    """429 异常契约（定义在 quota.py，不改 exceptions.py）。"""

    def test_inherits_app_exception(self) -> None:
        assert issubclass(QuotaExceededError, AppException)

    def test_status_code_is_429(self) -> None:
        error = QuotaExceededError(retry_after=30, used=1000, limit=1000)
        assert error.status_code == 429
        assert error.error_code == "QUOTA_EXCEEDED"
        assert error.message

    def test_carries_retry_after_and_remaining(self) -> None:
        error = QuotaExceededError(retry_after=42, used=1000, limit=1000)
        assert error.retry_after == 42
        assert error.details["retry_after"] == 42
        assert error.details["used"] == 1000
        assert error.details["limit"] == 1000
        assert error.details["remaining"] == 0

    def test_constructible_without_arguments(self) -> None:
        """保持 AppException 的通用构造兼容（全局 handler 可安全处理）。"""
        error = QuotaExceededError()
        assert error.status_code == 429
        assert error.retry_after >= 1


# ===== 扣减与边界 =====


class TestQuotaConsumption:
    """按日窗口扣减。"""

    async def test_consumes_until_exact_limit(self, fake_redis: Any) -> None:
        service = _service(fake_redis, daily_limit=1000)

        await service.check_and_consume(USER_ID, tokens=400)
        await service.check_and_consume(USER_ID, tokens=600)  # 刚好用尽，不应抛

        assert await fake_redis.get(f"quota:{USER_ID}:2026-03-01") == "1000"

    async def test_exceeding_limit_raises_and_does_not_consume(self, fake_redis: Any) -> None:
        service = _service(fake_redis, daily_limit=1000)
        await service.check_and_consume(USER_ID, tokens=1000)

        with pytest.raises(QuotaExceededError) as exc_info:
            await service.check_and_consume(USER_ID, tokens=1)

        error = exc_info.value
        assert error.details["used"] == 1000
        assert error.details["limit"] == 1000
        assert 1 <= error.retry_after <= 86400
        # 被拒绝的请求不得扣减
        assert await fake_redis.get(f"quota:{USER_ID}:2026-03-01") == "1000"

    async def test_single_request_over_limit_raises(self, fake_redis: Any) -> None:
        service = _service(fake_redis, daily_limit=100)

        with pytest.raises(QuotaExceededError):
            await service.check_and_consume(USER_ID, tokens=101)

        assert await fake_redis.get(f"quota:{USER_ID}:2026-03-01") is None

    async def test_zero_tokens_is_noop(self, fake_redis: Any) -> None:
        service = _service(fake_redis, daily_limit=100)

        await service.check_and_consume(USER_ID, tokens=0)

        assert await fake_redis.get(f"quota:{USER_ID}:2026-03-01") is None

    async def test_users_have_independent_quotas(self, fake_redis: Any) -> None:
        other_user = uuid.uuid4()
        service = _service(fake_redis, daily_limit=100)
        await service.check_and_consume(USER_ID, tokens=100)

        await service.check_and_consume(other_user, tokens=100)  # 另一个用户仍可用

        with pytest.raises(QuotaExceededError):
            await service.check_and_consume(USER_ID, tokens=1)

    async def test_key_contains_user_id_and_date(self, fake_redis: Any) -> None:
        service = _service(fake_redis, daily_limit=100)
        await service.check_and_consume(USER_ID, tokens=10)

        assert await fake_redis.get(f"quota:{USER_ID}:2026-03-01") == "10"

    async def test_window_ttl_is_bounded_by_daily_window(self, fake_redis: Any) -> None:
        service = _service(fake_redis, daily_limit=100)
        await service.check_and_consume(USER_ID, tokens=10)

        ttl = await fake_redis.ttl(f"quota:{USER_ID}:2026-03-01")
        assert 0 < ttl <= 86400

    async def test_retry_after_counts_down_to_next_utc_day(self, fake_redis: Any) -> None:
        """23:00 UTC 时用尽配额 → retry_after ≈ 1 小时（Retry-After 头语义）。"""
        clock = MutableClock(datetime(2026, 3, 1, 23, 0, tzinfo=UTC))
        service = _service(fake_redis, daily_limit=10, clock=clock)
        await service.check_and_consume(USER_ID, tokens=10)

        with pytest.raises(QuotaExceededError) as exc_info:
            await service.check_and_consume(USER_ID, tokens=1)

        assert 3500 <= exc_info.value.retry_after <= 3600


# ===== 按日重置 =====


class TestDailyReset:
    """跨日窗口重置。"""

    async def test_new_day_restores_full_quota(self, fake_redis: Any) -> None:
        clock = MutableClock(DAY_ONE)
        service = _service(fake_redis, daily_limit=100, clock=clock)
        await service.check_and_consume(USER_ID, tokens=100)
        with pytest.raises(QuotaExceededError):
            await service.check_and_consume(USER_ID, tokens=1)

        clock.now = DAY_TWO

        await service.check_and_consume(USER_ID, tokens=100)  # 新窗口满额可用
        assert await fake_redis.get(f"quota:{USER_ID}:2026-03-02") == "100"

    async def test_previous_day_counter_is_untouched(self, fake_redis: Any) -> None:
        clock = MutableClock(DAY_ONE)
        service = _service(fake_redis, daily_limit=100, clock=clock)
        await service.check_and_consume(USER_ID, tokens=30)

        clock.now = DAY_TWO
        await service.check_and_consume(USER_ID, tokens=10)

        assert await fake_redis.get(f"quota:{USER_ID}:2026-03-01") == "30"
        assert await fake_redis.get(f"quota:{USER_ID}:2026-03-02") == "10"


# ===== 并发原子性 =====


class TestQuotaConcurrency:
    """INCRBY 原子性：并发扣减不超卖。"""

    async def test_concurrent_consumptions_never_oversell(self, fake_redis: Any) -> None:
        service = _service(fake_redis, daily_limit=1000)

        async def consume() -> bool:
            try:
                await service.check_and_consume(USER_ID, tokens=100)
            except QuotaExceededError:
                return False
            return True

        results = await asyncio.gather(*(consume() for _ in range(20)))

        assert sum(results) == 10  # 1000 tokens / 100 = 恰好 10 次成功
        assert await fake_redis.get(f"quota:{USER_ID}:2026-03-01") == "1000"


# ===== fail-open：Redis 故障必须放行 =====


class TestQuotaFailOpen:
    """配额组件故障绝不能拖垮任务：放行 + 一条 warning，不抛异常。"""

    async def test_connection_error_fails_open(self, monkeypatch: pytest.MonkeyPatch) -> None:
        logger = RecordingLogger()
        monkeypatch.setattr(quota_module, "logger", logger)
        redis = BrokenRedis(ConnectionError("connection refused"))
        service = _service(redis, daily_limit=10)

        await service.check_and_consume(USER_ID, tokens=999)  # 不抛异常

        assert redis.calls == 1
        assert service is not None
        assert "quota_redis_unavailable" in logger.event_names

    async def test_timeout_fails_open(self) -> None:
        service = _service(BrokenRedis(TimeoutError("redis timeout")), daily_limit=10)
        await service.check_and_consume(USER_ID, tokens=999)

    async def test_no_redis_client_fails_open_with_warning(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        logger = RecordingLogger()
        monkeypatch.setattr(quota_module, "logger", logger)
        service = _service(None, daily_limit=10)

        await service.check_and_consume(USER_ID, tokens=999)

        assert "quota_redis_unavailable" in logger.event_names

    async def test_warning_payload_contains_user_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        logger = RecordingLogger()
        monkeypatch.setattr(quota_module, "logger", logger)
        service = _service(BrokenRedis(), daily_limit=10)

        await service.check_and_consume(USER_ID, tokens=5)

        _, payload = next(event for event in logger.events if event[0] == "quota_redis_unavailable")
        assert payload["user_id"] == str(USER_ID)
        assert "error" in payload

    async def test_repeated_failures_keep_allowing(self) -> None:
        service = _service(BrokenRedis(), daily_limit=1)

        for _ in range(5):
            await service.check_and_consume(USER_ID, tokens=10)

    async def test_redis_library_error_fails_open(self) -> None:
        """真实 redis-py 异常类型（RedisError 家族）也必须 fail-open。"""
        service = _service(
            BrokenRedis(redis_exceptions.ConnectionError("connection lost")), daily_limit=10
        )
        await service.check_and_consume(USER_ID, tokens=999)

    async def test_mid_flight_failure_fails_open(self, fake_redis: Any) -> None:
        """运行期掉线：先正常扣减到上限，Redis 随后故障 → 超配额请求也放行（不是假设）。"""
        flaky = FlakyRedis(fake_redis, fail_after=1)
        service = _service(flaky, daily_limit=100)
        await service.check_and_consume(USER_ID, tokens=100)  # 正常路径：用尽配额

        # 下一次本应抛 QuotaExceededError，但 Redis 已掉线 → fail-open 放行
        await service.check_and_consume(USER_ID, tokens=100)

        assert flaky.calls == 2
        assert await fake_redis.get(f"quota:{USER_ID}:2026-03-01") == "100"  # 未被扣减


# ===== 配置驱动的边界行为 =====


class TestQuotaConfiguration:
    """阈值/窗口可注入；负值表示关闭配额（配置哨兵 -1）。"""

    def test_default_daily_limit_is_exposed(self) -> None:
        assert isinstance(DEFAULT_DAILY_TOKEN_LIMIT, int)
        assert DEFAULT_DAILY_TOKEN_LIMIT > 0

    async def test_negative_limit_disables_quota(self) -> None:
        redis = BrokenRedis()
        service = _service(redis, daily_limit=-1)
        # 关闭配额：放行且不触碰 Redis（即使 Redis 不可用也不报错）
        await service.check_and_consume(USER_ID, tokens=10_000_000)
        assert redis.calls == 0

    async def test_zero_limit_rejects_positive_consumption(self, fake_redis: Any) -> None:
        """0 是严格语义（不是关闭）：配置默认是 1_000_000，关闭请用 -1。"""
        service = _service(fake_redis, daily_limit=0)

        with pytest.raises(QuotaExceededError):
            await service.check_and_consume(USER_ID, tokens=1)

        assert await fake_redis.get(f"quota:{USER_ID}:2026-03-01") is None

    async def test_custom_window_seconds_is_honoured(self, fake_redis: Any) -> None:
        clock = MutableClock(DAY_ONE)
        service = QuotaService(
            redis_client=fake_redis, daily_limit=100, window_seconds=3600, clock=clock
        )
        await service.check_and_consume(USER_ID, tokens=100)

        clock.now = datetime(2026, 3, 1, 10, 0, 1, tzinfo=UTC)  # 同一小时窗口内
        with pytest.raises(QuotaExceededError):
            await service.check_and_consume(USER_ID, tokens=1)

        clock.now = datetime(2026, 3, 1, 11, 0, 1, tzinfo=UTC)  # 进入下一窗口
        await service.check_and_consume(USER_ID, tokens=100)
