"""速率限制器测试（Phase 1 · P1-D，计划 §1.4）。

覆盖：
1. 正常限流（按 limit 放行、剩余额度递减、超限拒绝）；
2. 窗口过期后恢复；
3. 并发安全（INCR 语义：并发命中不丢计数，恰有 limit 次放行）；
4. **Redis 故障必须 fail-open**：异常 / 超时 / 未注入客户端都放行并记 warning，绝不抛异常；
5. 不同 key 独立计数、limit=0 边界。

不依赖真实 Redis：正常路径用 fakeredis，故障路径用抛异常的异步替身。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any

import fakeredis.aioredis
import pytest
import pytest_asyncio
from redis import exceptions as redis_exceptions

from app.core import rate_limit as rate_limit_module
from app.core.rate_limit import InMemoryRateLimiter, RateLimiter, RedisRateLimiter

WINDOW = 60


# ===== 测试替身 =====


class BrokenRedis:
    """所有命令都抛异常的异步 Redis 替身（模拟 Redis 不可用）。"""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error or ConnectionError("redis down")
        self.calls = 0

    async def eval(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        raise self.error

    async def incr(self, key: str) -> int:
        self.calls += 1
        raise self.error

    async def expire(self, key: str, ttl: int) -> bool:
        self.calls += 1
        raise self.error


class FlakyRedis:
    """前 `fail_after` 次委托给 fakeredis（真实语义），之后全部抛异常。

    用于证明「fail-open 是运行期真实行为」：同一个实例先按限额拦截，Redis 掉线后放行。
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


class FakeClock:
    """可推进的单调时钟（毫秒级精度足够）。"""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest_asyncio.fixture
async def fake_redis() -> AsyncIterator[fakeredis.aioredis.FakeRedis]:
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


# ===== 协议 =====


class TestRateLimiterProtocol:
    """冻结接口：`hit(key, *, limit, window_seconds) -> (allowed, remaining)`。"""

    def test_protocol_declares_hit(self) -> None:
        assert "hit" in dir(RateLimiter)

    def test_implementations_expose_hit(self) -> None:
        for limiter in (InMemoryRateLimiter(), RedisRateLimiter()):
            assert callable(getattr(limiter, "hit", None))


# ===== 单机实现 =====


class TestInMemoryRateLimiter:
    """单机/测试用限流器。"""

    async def test_allows_up_to_limit_then_denies(self) -> None:
        limiter = InMemoryRateLimiter()

        first = await limiter.hit("login:1.2.3.4", limit=3, window_seconds=WINDOW)
        second = await limiter.hit("login:1.2.3.4", limit=3, window_seconds=WINDOW)
        third = await limiter.hit("login:1.2.3.4", limit=3, window_seconds=WINDOW)
        fourth = await limiter.hit("login:1.2.3.4", limit=3, window_seconds=WINDOW)

        assert first == (True, 2)
        assert second == (True, 1)
        assert third == (True, 0)
        assert fourth == (False, 0)

    async def test_keys_have_independent_budgets(self) -> None:
        limiter = InMemoryRateLimiter()
        await limiter.hit("user-a", limit=1, window_seconds=WINDOW)

        assert await limiter.hit("user-a", limit=1, window_seconds=WINDOW) == (False, 0)
        assert await limiter.hit("user-b", limit=1, window_seconds=WINDOW) == (True, 0)

    async def test_window_expiry_restores_budget(self) -> None:
        clock = FakeClock()
        limiter = InMemoryRateLimiter(clock=clock)
        for _ in range(3):
            assert (await limiter.hit("route", limit=3, window_seconds=WINDOW))[0] is True
        assert await limiter.hit("route", limit=3, window_seconds=WINDOW) == (False, 0)

        clock.advance(WINDOW + 0.001)  # 窗口已过期

        assert await limiter.hit("route", limit=3, window_seconds=WINDOW) == (True, 2)

    async def test_concurrent_hits_allow_exactly_limit(self) -> None:
        """并发命中不丢计数：恰有 limit 次放行（内存实现无 await 间隙）。"""
        limiter = InMemoryRateLimiter()

        results = await asyncio.gather(
            *(limiter.hit("burst", limit=5, window_seconds=WINDOW) for _ in range(20))
        )

        assert sum(1 for allowed, _ in results if allowed) == 5
        assert sum(1 for allowed, _ in results if not allowed) == 15

    async def test_limit_zero_denies_every_hit(self) -> None:
        limiter = InMemoryRateLimiter()
        assert await limiter.hit("zero", limit=0, window_seconds=WINDOW) == (False, 0)


# ===== Redis 实现 =====


class TestRedisRateLimiter:
    """Redis INCR + EXPIRE 原子窗口实现。"""

    async def test_allows_up_to_limit_then_denies(self, fake_redis: Any) -> None:
        limiter = RedisRateLimiter(redis_client=fake_redis)

        results = [
            await limiter.hit("login:1.2.3.4", limit=3, window_seconds=WINDOW) for _ in range(4)
        ]

        assert results[0] == (True, 2)
        assert results[1] == (True, 1)
        assert results[2] == (True, 0)
        assert results[3] == (False, 0)
        # INCR 计数如实反映所有命中（第 4 次被拒也计入）
        assert await fake_redis.get("ratelimit:60:login:1.2.3.4") == "4"

    async def test_sets_expiry_on_first_hit(self, fake_redis: Any) -> None:
        limiter = RedisRateLimiter(redis_client=fake_redis)

        await limiter.hit("route", limit=5, window_seconds=WINDOW)

        ttl = await fake_redis.ttl("ratelimit:60:route")
        assert 0 < ttl <= WINDOW

    async def test_window_expiry_restores_budget(self, fake_redis: Any) -> None:
        limiter = RedisRateLimiter(redis_client=fake_redis)
        assert (await limiter.hit("expiring", limit=1, window_seconds=1))[0] is True
        assert (await limiter.hit("expiring", limit=1, window_seconds=1))[0] is False

        await asyncio.sleep(1.1)  # 等 key 自然过期

        assert await limiter.hit("expiring", limit=1, window_seconds=1) == (True, 0)

    async def test_concurrent_hits_are_atomic(self, fake_redis: Any) -> None:
        """INCR 语义：50 次并发命中 → 恰 5 次放行，且计数无丢失。"""
        limiter = RedisRateLimiter(redis_client=fake_redis)

        results = await asyncio.gather(
            *(limiter.hit("burst", limit=5, window_seconds=WINDOW) for _ in range(50))
        )

        assert sum(1 for allowed, _ in results if allowed) == 5
        assert await fake_redis.get("ratelimit:60:burst") == "50"

    async def test_keys_have_independent_budgets(self, fake_redis: Any) -> None:
        limiter = RedisRateLimiter(redis_client=fake_redis)
        await limiter.hit("user-a", limit=1, window_seconds=WINDOW)

        assert await limiter.hit("user-a", limit=1, window_seconds=WINDOW) == (False, 0)
        assert await limiter.hit("user-b", limit=1, window_seconds=WINDOW) == (True, 0)

    async def test_custom_key_prefix_is_used(self, fake_redis: Any) -> None:
        limiter = RedisRateLimiter(redis_client=fake_redis, key_prefix="rl-test")
        await limiter.hit("k", limit=1, window_seconds=WINDOW)
        assert await fake_redis.get("rl-test:60:k") == "1"


# ===== fail-open：Redis 故障必须放行 =====


class TestFailOpen:
    """限流组件故障绝不能拖垮服务：放行 + 一条 warning，不抛异常。"""

    async def test_connection_error_fails_open(self, monkeypatch: pytest.MonkeyPatch) -> None:
        logger = RecordingLogger()
        monkeypatch.setattr(rate_limit_module, "logger", logger)
        redis = BrokenRedis(ConnectionError("connection refused"))
        limiter = RedisRateLimiter(redis_client=redis)

        allowed, remaining = await limiter.hit("login", limit=3, window_seconds=WINDOW)

        assert (allowed, remaining) == (True, 3)
        assert redis.calls == 1
        assert "rate_limit_redis_unavailable" in logger.event_names

    async def test_timeout_fails_open(self, monkeypatch: pytest.MonkeyPatch) -> None:
        logger = RecordingLogger()
        monkeypatch.setattr(rate_limit_module, "logger", logger)
        limiter = RedisRateLimiter(redis_client=BrokenRedis(TimeoutError("redis timeout")))

        assert await limiter.hit("login", limit=1, window_seconds=WINDOW) == (True, 1)
        assert "rate_limit_redis_unavailable" in logger.event_names

    async def test_generic_error_fails_open(self) -> None:
        limiter = RedisRateLimiter(redis_client=BrokenRedis(RuntimeError("boom")))
        assert await limiter.hit("login", limit=2, window_seconds=WINDOW) == (True, 2)

    async def test_no_redis_client_fails_open_with_warning(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        logger = RecordingLogger()
        monkeypatch.setattr(rate_limit_module, "logger", logger)
        limiter = RedisRateLimiter(redis_client=None)

        assert await limiter.hit("login", limit=4, window_seconds=WINDOW) == (True, 4)
        assert "rate_limit_redis_unavailable" in logger.event_names

    async def test_repeated_failures_keep_allowing(self) -> None:
        """连续故障期间每次都必须放行（限流失效 ≠ 服务不可用）。"""
        limiter = RedisRateLimiter(redis_client=BrokenRedis())

        results = [await limiter.hit("login", limit=2, window_seconds=WINDOW) for _ in range(10)]

        assert all(allowed is True for allowed, _ in results)
        assert all(remaining == 2 for _, remaining in results)

    async def test_warning_payload_contains_route_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        logger = RecordingLogger()
        monkeypatch.setattr(rate_limit_module, "logger", logger)
        limiter = RedisRateLimiter(redis_client=BrokenRedis())

        await limiter.hit("upload:user-1", limit=5, window_seconds=WINDOW)

        _, payload = next(
            event for event in logger.events if event[0] == "rate_limit_redis_unavailable"
        )
        assert payload["key"] == "upload:user-1"
        assert "error" in payload

    async def test_redis_library_error_fails_open(self) -> None:
        """真实 redis-py 异常类型（RedisError 家族）也必须 fail-open。"""
        limiter = RedisRateLimiter(
            redis_client=BrokenRedis(redis_exceptions.ConnectionError("connection lost"))
        )
        assert await limiter.hit("login", limit=2, window_seconds=WINDOW) == (True, 2)

    async def test_mid_flight_failure_fails_open(self) -> None:
        """运行期掉线：先按限额正常拦截，Redis 随后故障 → 立刻转为放行（不是假设）。"""
        backing = fakeredis.aioredis.FakeRedis(decode_responses=True)
        flaky = FlakyRedis(backing, fail_after=1)
        limiter = RedisRateLimiter(redis_client=flaky)

        first = await limiter.hit("login", limit=1, window_seconds=WINDOW)
        second = await limiter.hit("login", limit=1, window_seconds=WINDOW)  # Redis 掉线

        assert first == (True, 0)  # 正常窗口内已被用满
        assert second == (True, 1)  # fail-open：放行（剩余按未扣减）
        assert flaky.calls == 2
        await backing.aclose()


def test_rate_limiter_callable_protocol_matches_signature() -> None:
    """签名自检：两个实现都是 async 且接受 limit/window_seconds 关键字。"""
    import inspect

    for limiter_cls in (InMemoryRateLimiter, RedisRateLimiter):
        signature = inspect.signature(limiter_cls.hit)
        assert "limit" in signature.parameters
        assert "window_seconds" in signature.parameters
        assert inspect.iscoroutinefunction(limiter_cls.hit)


def test_clock_is_injectable() -> None:
    """阈值/窗口/时钟均可注入，便于测试与后续配置驱动。"""
    clock: Callable[[], float] = FakeClock()
    limiter = InMemoryRateLimiter(clock=clock)
    assert limiter is not None
