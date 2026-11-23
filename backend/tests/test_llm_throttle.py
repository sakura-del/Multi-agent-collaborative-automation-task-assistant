"""全局 LLM 调用闸门（app/llm/throttle.py）回归测试。

⚠️ 铁律 #0：本机 Windows **不执行** pytest；本文件按静态检查交付
（black/ruff/mypy），实际执行须在专用测试机进行。

覆盖点：
1. 上限生效：limit=2 时 6 个并发 chat 的原始请求峰值不超过 2；
2. 跨实例共享：不同适配器实例共用同一个进程级闸门；
3. 完全串行：limit=1 时峰值为 1；
4. 配置变化即时生效（信号量按新上限重建）；
5. 异常不泄漏槽位：失败调用后后续调用仍可正常获取槽位。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest

from app.llm import throttle
from app.llm.base import BaseLLM
from app.llm.models import LLMResponse, Message, Usage


class _FakeAdapter(BaseLLM):
    """记录并发峰值的假适配器。

    `_raw_chat` 用 sleep 模拟上游耗时；计数器挂在类上，
    因此**多个实例**共享同一份观测值，可验证「跨实例共享闸门」。
    """

    active = 0
    peak = 0

    def __init__(self, *, delay: float = 0.03, fail_first: bool = False) -> None:
        super().__init__("fake-model", provider="fake", max_retries=1)
        self._delay = delay
        self._fail_first = fail_first

    def _create_client(self) -> Any:  # pragma: no cover - 假适配器
        return None

    async def _raw_chat(
        self,
        messages: list[Message],
        *,
        tools: Any = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        type(self).active += 1
        type(self).peak = max(type(self).peak, type(self).active)
        try:
            await asyncio.sleep(self._delay)
            if self._fail_first:
                self._fail_first = False
                raise RuntimeError("simulated permanent failure")
            return LLMResponse(content="ok", model="fake-model", usage=Usage())
        finally:
            type(self).active -= 1

    async def stream_chat(self, *args: Any, **kwargs: Any) -> AsyncIterator[str]:
        yield ""  # pragma: no cover - 未使用

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]


def _patch_limit(monkeypatch: pytest.MonkeyPatch, limit: int) -> None:
    """把 get_settings().llm.max_concurrency 固定为 limit。"""

    def fake_get_settings() -> Any:
        return SimpleNamespace(llm=SimpleNamespace(max_concurrency=limit))

    monkeypatch.setattr("app.core.config.get_settings", fake_get_settings)


@pytest.fixture(autouse=True)
def _clean_throttle() -> Any:
    """每个用例前清空按事件循环缓存的信号量，避免用例间串扰。"""
    throttle.reset_for_tests()
    yield
    throttle.reset_for_tests()
    _FakeAdapter.active = 0
    _FakeAdapter.peak = 0


@pytest.mark.asyncio
async def test_global_limit_caps_cross_instance_peak(monkeypatch: pytest.MonkeyPatch) -> None:
    """limit=2：6 个并发请求（分布在 3 个实例上）峰值不超过 2。"""
    _patch_limit(monkeypatch, 2)
    adapters = [_FakeAdapter() for _ in range(3)]

    results = await asyncio.gather(
        *(
            adapter.chat([Message(role="user", content="hi")])
            for adapter in adapters
            for _ in range(2)
        )
    )

    assert len(results) == 6
    assert all(result.content == "ok" for result in results)
    assert _FakeAdapter.peak <= 2, f"peak={_FakeAdapter.peak} 超过全局上限 2"


@pytest.mark.asyncio
async def test_limit_one_serializes(monkeypatch: pytest.MonkeyPatch) -> None:
    """limit=1：并发发起也只能串行执行（峰值为 1）。"""
    _patch_limit(monkeypatch, 1)
    adapter = _FakeAdapter()

    await asyncio.gather(*(adapter.chat([Message(role="user", content="hi")]) for _ in range(4)))

    assert _FakeAdapter.peak == 1


@pytest.mark.asyncio
async def test_limit_change_rebuilds_semaphore(monkeypatch: pytest.MonkeyPatch) -> None:
    """先 limit=3 再改 1：新上限立即生效（信号量按新值重建）。"""
    _patch_limit(monkeypatch, 3)
    adapter = _FakeAdapter()
    await adapter.chat([Message(role="user", content="first")])
    assert _FakeAdapter.peak == 1  # 单请求无所谓上限

    _patch_limit(monkeypatch, 1)
    _FakeAdapter.peak = 0
    await asyncio.gather(*(adapter.chat([Message(role="user", content="hi")]) for _ in range(4)))
    assert _FakeAdapter.peak == 1


@pytest.mark.asyncio
async def test_failure_does_not_leak_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    """失败调用必须释放槽位：limit=1 时后续调用不被饿死。"""
    _patch_limit(monkeypatch, 1)
    failing = _FakeAdapter(fail_first=True)
    healthy = _FakeAdapter()

    with pytest.raises(RuntimeError):
        await failing.chat([Message(role="user", content="boom")])

    # 若槽位泄漏，这里会在 3s 内拿不到槽位
    result = await asyncio.wait_for(
        healthy.chat([Message(role="user", content="still works")]), timeout=3.0
    )
    assert result.content == "ok"


class TestPerGroupLimits:
    """路由感知并发（Issue #22）：不同并发组各自限流，互不挤占。

    本地 vLLM 可 4 并发、共享免费池只能 1——单一全局闸只能取最小值。
    """

    @pytest.mark.asyncio
    async def test_groups_have_independent_semaphores(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        throttle.reset_for_tests()

        def fake_get_settings() -> Any:
            return SimpleNamespace(
                llm=SimpleNamespace(
                    max_concurrency=1,
                    routing_targets={"local": {"max_concurrency": 3}},
                )
            )

        monkeypatch.setattr("app.core.config.get_settings", fake_get_settings)

        entered: list[str] = []

        async def hold(group: str, seconds: float = 0.05) -> None:
            async with throttle.llm_slot(group=group):
                entered.append(group)
                await asyncio.sleep(seconds)

        # 默认组上限 1：两个并发只能顺序进入；local 组上限 3：可以并发
        start = asyncio.get_running_loop().time()
        await asyncio.gather(hold("default"), hold("default"))
        serial_time = asyncio.get_running_loop().time() - start
        assert serial_time >= 0.09, f"默认组未串行: {serial_time:.3f}s"

        start = asyncio.get_running_loop().time()
        await asyncio.gather(hold("local"), hold("local"))
        parallel_time = asyncio.get_running_loop().time() - start
        assert parallel_time < 0.09, f"local 组未并发: {parallel_time:.3f}s"
        throttle.reset_for_tests()
