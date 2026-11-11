"""Embedding API 的进程内 60 秒滑动窗口双限流。"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any


@dataclass
class UsageRecord:
    """一次 HTTP 尝试在窗口中的用量记录。"""

    at: float
    tokens: int


class SlidingWindowRateLimiter:
    """同时限制请求数与 token 数；时钟可注入以便无等待测试。"""

    def __init__(self, rpm: int, tpm: int, *, clock: Any = None) -> None:
        if rpm <= 0 or tpm <= 0:
            raise ValueError("RPM/TPM 必须为正数")
        self._rpm = int(rpm)
        self._tpm = int(tpm)
        self._now = time.monotonic if clock is None else clock.monotonic
        self._sleep = time.sleep if clock is None else clock.sleep
        self._events: deque[UsageRecord] = deque()
        self._used_tokens = 0
        self._lock = threading.Lock()

    def acquire(self, tokens: int) -> UsageRecord:
        """记录一次 HTTP 尝试；超出任一预算时等待到窗口允许。"""
        tokens = int(tokens)
        if tokens <= 0:
            raise ValueError("token 预估值必须为正数")
        if tokens > self._tpm:
            raise ValueError(f"单请求预估 {tokens} tokens 超过 TPM={self._tpm}")

        with self._lock:
            while True:
                now = float(self._now())
                self._expire(now)
                if len(self._events) < self._rpm and self._used_tokens + tokens <= self._tpm:
                    record = UsageRecord(at=now, tokens=tokens)
                    self._events.append(record)
                    self._used_tokens += tokens
                    return record

                wait = 0.0
                if len(self._events) >= self._rpm:
                    wait = max(wait, self._events[0].at + 60.0 - now)

                excess = self._used_tokens + tokens - self._tpm
                if excess > 0:
                    freed = 0
                    for event in self._events:
                        freed += event.tokens
                        wait = max(wait, event.at + 60.0 - now)
                        if freed >= excess:
                            break

                self._sleep(max(0.0, wait))

    def record_actual(self, record: UsageRecord, actual_tokens: int) -> None:
        """用上游实际 total_tokens 修正仍活跃的预估记录。"""
        actual_tokens = int(actual_tokens)
        if actual_tokens < 0:
            raise ValueError("实际 token 数不能为负数")
        with self._lock:
            for event in self._events:
                if event is record:
                    self._used_tokens += actual_tokens - event.tokens
                    event.tokens = actual_tokens
                    return

    def _expire(self, now: float) -> None:
        cutoff = now - 60.0
        while self._events and self._events[0].at <= cutoff:
            expired = self._events.popleft()
            self._used_tokens -= expired.tokens
