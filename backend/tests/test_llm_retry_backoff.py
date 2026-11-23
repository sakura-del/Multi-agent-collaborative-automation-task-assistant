"""LLM 重试指数退避（Issue #22 / 低并发共享池兼容）。

共享免费池（qiniu conatus-free 实测：并发≈1、10 连发成功率 3/10）只能靠
退避穿过 429；旧实现「立即重试」等于 1 秒内连撞 3 次墙。

⚠️ 铁律 #0：本机 Windows 不执行 pytest；本文件按静态检查交付。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest

from app.llm.base import BaseLLM
from app.llm.models import LLMResponse, Message


class _RateLimited(Exception):
    """模拟厂商 429：带 status_code 与可选 Retry-After。"""

    def __init__(self, retry_after: str | None = None) -> None:
        super().__init__("429 Too many concurrent requests (free_campaign_global_limit)")
        self.status_code = 429
        headers = {"retry-after": retry_after} if retry_after is not None else {}
        self.response = SimpleNamespace(headers=headers)


class _AlwaysFailingAdapter(BaseLLM):
    def __init__(self, exc: Exception, *, max_retries: int = 3) -> None:
        super().__init__("fake-model", provider="fake", max_retries=max_retries)
        self._exc = exc
        self.attempts = 0

    def _create_client(self) -> Any:  # pragma: no cover - 假适配器
        return None

    async def _raw_chat(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
        self.attempts += 1
        raise self._exc

    async def stream_chat(self, *args: Any, **kwargs: Any) -> AsyncIterator[str]:
        yield ""  # pragma: no cover

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]


@pytest.fixture()
def recorded_sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """拦截 asyncio.sleep，记录退避秒数（不真的等待）。"""
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return delays


def _patch_backoff(
    monkeypatch: pytest.MonkeyPatch, *, base: float, factor: float, cap: float
) -> None:
    def fake_get_settings() -> Any:
        return SimpleNamespace(
            llm=SimpleNamespace(
                retry_backoff_base=base,
                retry_backoff_factor=factor,
                retry_backoff_cap=cap,
            )
        )

    monkeypatch.setattr("app.core.config.get_settings", fake_get_settings)


@pytest.mark.asyncio
async def test_backoff_is_exponential(
    monkeypatch: pytest.MonkeyPatch, recorded_sleeps: list[float]
) -> None:
    _patch_backoff(monkeypatch, base=3.0, factor=2.0, cap=60.0)
    adapter = _AlwaysFailingAdapter(_RateLimited(), max_retries=3)

    with pytest.raises(_RateLimited):
        await adapter.chat([Message(role="user", content="hi")])

    assert adapter.attempts == 3
    assert recorded_sleeps == [3.0, 6.0], f"退避序列异常: {recorded_sleeps}"


@pytest.mark.asyncio
async def test_cap_limits_backoff(
    monkeypatch: pytest.MonkeyPatch, recorded_sleeps: list[float]
) -> None:
    _patch_backoff(monkeypatch, base=40.0, factor=2.0, cap=60.0)
    adapter = _AlwaysFailingAdapter(_RateLimited(), max_retries=3)

    with pytest.raises(_RateLimited):
        await adapter.chat([Message(role="user", content="hi")])

    assert recorded_sleeps == [40.0, 60.0], f"封顶未生效: {recorded_sleeps}"


@pytest.mark.asyncio
async def test_retry_after_header_wins(
    monkeypatch: pytest.MonkeyPatch, recorded_sleeps: list[float]
) -> None:
    _patch_backoff(monkeypatch, base=3.0, factor=2.0, cap=60.0)
    adapter = _AlwaysFailingAdapter(_RateLimited(retry_after="10"), max_retries=2)

    with pytest.raises(_RateLimited):
        await adapter.chat([Message(role="user", content="hi")])

    assert recorded_sleeps == [10.0], f"Retry-After 未生效: {recorded_sleeps}"


@pytest.mark.asyncio
async def test_non_retryable_error_has_no_sleep(
    monkeypatch: pytest.MonkeyPatch, recorded_sleeps: list[float]
) -> None:
    _patch_backoff(monkeypatch, base=3.0, factor=2.0, cap=60.0)

    class _AuthError(Exception):
        status_code = 401

    adapter = _AlwaysFailingAdapter(_AuthError("invalid api key"), max_retries=3)

    with pytest.raises(_AuthError):
        await adapter.chat([Message(role="user", content="hi")])

    assert adapter.attempts == 1
    assert recorded_sleeps == []
