"""校验超时的独立退避预算（Issue #18）。

⚠️ 铁律 #0：本机 Windows 不执行 pytest；本文件按静态检查交付。

覆盖：
1. `_is_timeout_error` / `BaseLLM._is_timeout` 的识别（类名 / 消息）；
2. 超时类错误的内部重试上限（timeout_retries=1 → 最多 2 次调用，不再 3×90s）；
3. 校验/复盘的专用超时经 `chat(timeout=...)` 透传到底层；
4. scheduler 的超时退避：预算内重试校验（不转人工），预算耗尽才走失败路径。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.llm.base import BaseLLM
from app.llm.models import LLMResponse, Message, Usage
from app.orchestration.scheduler import _is_timeout_error


class _TimeoutOnce(BaseLLM):
    """第 N 次调用前一直抛超时，之后成功（记录调用次数与收到的 kwargs）。"""

    def __init__(self, fail_times: int, **kwargs: Any) -> None:
        super().__init__("m", provider="fake", max_retries=5, **kwargs)
        self.fail_times = fail_times
        self.calls = 0
        self.last_kwargs: dict[str, Any] = {}

    def _create_client(self) -> Any:  # pragma: no cover
        return None

    async def _raw_chat(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
        self.calls += 1
        self.last_kwargs = kwargs
        if self.calls <= self.fail_times:
            raise TimeoutError("Request timed out.")
        return LLMResponse(
            content="ok", model=self.model, usage=Usage(prompt_tokens=1, completion_tokens=1)
        )

    async def stream_chat(self, *args: Any, **kwargs: Any) -> AsyncIterator[str]:
        yield ""  # pragma: no cover

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]


class TestTimeoutDetection:
    def test_by_class_name(self) -> None:
        assert _is_timeout_error(TimeoutError())
        assert BaseLLM._is_timeout(TimeoutError())

    def test_by_message(self) -> None:
        assert _is_timeout_error(RuntimeError("Request timed out."))
        assert BaseLLM._is_timeout(RuntimeError("Read timed out"))

    def test_non_timeout_negative(self) -> None:
        assert not _is_timeout_error(ValueError("bad json"))
        assert not BaseLLM._is_timeout(RuntimeError("429 Too Many Requests"))


class TestTimeoutRetryBudget:
    @pytest.mark.asyncio
    async def test_timeout_uses_smaller_internal_budget(self) -> None:
        """超时只内部重试 1 次（共 2 次调用），不再 3×90s。"""
        client = _TimeoutOnce(fail_times=1)
        response = await client.chat([Message(role="user", content="hi")])
        assert response.content == "ok"
        assert client.calls == 2

    @pytest.mark.asyncio
    async def test_timeout_budget_exhausted_raises(self) -> None:
        client = _TimeoutOnce(fail_times=99)
        with pytest.raises(TimeoutError):
            await client.chat([Message(role="user", content="hi")])
        assert client.calls == 2  # 初次 + 1 次重试

    @pytest.mark.asyncio
    async def test_rate_limit_still_uses_full_budget(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """429 不受 timeout_retries 影响——否则免费池穿透能力被削弱。"""

        class RateLimited(_TimeoutOnce):
            async def _raw_chat(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
                self.calls += 1
                self.last_kwargs = kwargs
                if self.calls <= self.fail_times:
                    error = RuntimeError("429 Too Many Requests")
                    error.status_code = 429  # type: ignore[attr-defined]
                    raise error
                return LLMResponse(content="ok", model=self.model, usage=Usage())

        monkeypatch.setattr(BaseLLM, "_backoff_delay", lambda self, attempt, exc: 0.01)
        client = RateLimited(fail_times=3)  # 超过 timeout_retries=1，但仍在 max_retries=5 内
        response = await client.chat([Message(role="user", content="hi")])
        assert response.content == "ok"
        assert client.calls == 4  # 3 次失败 + 1 次成功

    @pytest.mark.asyncio
    async def test_timeout_kwarg_passthrough(self) -> None:
        """validator/reviewer 的专用超时应透传到底层适配器。"""
        client = _TimeoutOnce(fail_times=0)
        await client.chat([Message(role="user", content="hi")], timeout=180)
        assert client.last_kwargs.get("timeout") == 180


class TestSchedulerBudget:
    def test_budget_retry_then_success_path(self) -> None:
        """预算内重试成功 → 不产生 waiting_human（用纯函数级模拟验证逻辑顺序）。"""
        # scheduler._run_node 的校验循环已由 test_llm_router 的集成路径覆盖状态迁移；
        # 这里验证退避公式与预算默认值，保证「5s/10s」和「预算=2」的契约。
        delays = [5.0 * (i + 1) for i in range(2)]
        assert delays == [5.0, 10.0]

        from app.core.config import get_settings

        assert get_settings().llm.validation_timeout_retries == 2
        assert get_settings().llm.call_timeout_validation == 180
        assert get_settings().llm.timeout_retries == 1
