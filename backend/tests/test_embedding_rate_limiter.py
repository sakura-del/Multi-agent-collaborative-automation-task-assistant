"""Embedding API 滑动窗口双限流测试（不等待真实时间）。"""

from __future__ import annotations

import pytest

from app.knowledge.services.embedding_rate_limiter import SlidingWindowRateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_rpm_waits_for_oldest_event() -> None:
    clock = FakeClock()
    limiter = SlidingWindowRateLimiter(2, 1000, clock=clock)

    limiter.acquire(10)
    limiter.acquire(10)
    record = limiter.acquire(10)

    assert clock.sleeps == [60.0]
    assert record.tokens == 10


def test_tpm_waits_even_when_rpm_is_available() -> None:
    clock = FakeClock()
    limiter = SlidingWindowRateLimiter(100, 100, clock=clock)

    limiter.acquire(80)
    limiter.acquire(30)

    assert clock.sleeps == [60.0]


def test_actual_usage_replaces_estimate() -> None:
    clock = FakeClock()
    limiter = SlidingWindowRateLimiter(10, 100, clock=clock)
    record = limiter.acquire(80)

    limiter.record_actual(record, 20)
    limiter.acquire(80)

    assert clock.sleeps == []


def test_oversized_single_request_is_rejected() -> None:
    with pytest.raises(ValueError, match="TPM"):
        SlidingWindowRateLimiter(10, 100).acquire(101)


def test_zero_or_negative_tokens_are_rejected() -> None:
    limiter = SlidingWindowRateLimiter(10, 100)

    with pytest.raises(ValueError, match="token"):
        limiter.acquire(0)
    with pytest.raises(ValueError, match="token"):
        limiter.acquire(-1)
