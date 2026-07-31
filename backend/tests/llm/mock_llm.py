"""LLM 层测试用 Mock 工具。

提供 MockLLM 类，模拟 BaseLLM 行为供单元测试使用，
无需真实 API Key 即可验证适配器逻辑、FC 循环、降级机制等。

支持场景：
- 预设响应队列：按调用顺序返回不同响应（模拟多轮 FC）
- 异常注入：指定轮次抛出指定异常（模拟超时/降级）
- 流式输出：模拟 stream_chat 的 token 增量
- 调用记录：保存所有调用参数与次数，供断言验证
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from app.llm.base import BaseLLM
from app.llm.models import LLMResponse, Message, MessageRole, ToolCall, Usage


class MockLLM(BaseLLM):
    """测试用 Mock LLM。

    用法：
        mock = MockLLM(responses=[resp1, resp2])
        # 第一次调用返回 resp1，第二次返回 resp2
        result = await mock.chat(messages)
    """

    def __init__(
        self,
        responses: list[LLMResponse] | None = None,
        *,
        model: str = "mock-model",
        provider: str = "mock",
        raise_on_call: Exception | None = None,
        raise_on_attempts: list[Exception] | None = None,
        stream_chunks: list[str] | None = None,
        embed_vectors: list[list[float]] | None = None,
        # 兼容 create_llm 工厂函数透传的标准 BaseLLM 参数（无需使用，但需接收）
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: int = 1,
        max_retries: int = 1,
        default_temperature: float = 0.7,
        default_max_tokens: int | None = None,
        **extra: Any,
    ) -> None:
        super().__init__(
            model=model,
            provider=provider,
            api_key=api_key or "mock-key",
            base_url=base_url or "http://mock",
            timeout=timeout,
            max_retries=max_retries,
            default_temperature=default_temperature,
            default_max_tokens=default_max_tokens,
        )
        # 响应队列：每次 _raw_chat 弹出一个
        self._responses = list(responses) if responses else []
        self._raise_on_call = raise_on_call
        # 按调用次数抛异常：[第1次异常, 第2次异常, ...]
        self._raise_on_attempts = raise_on_attempts or []
        self._stream_chunks = stream_chunks or []
        self._embed_vectors = embed_vectors or []
        # 调用记录：供测试断言
        self.call_count = 0
        self.call_history: list[dict[str, Any]] = []

    def _create_client(self) -> Any:
        return "mock-client"

    async def _raw_chat(
        self,
        messages: list[Message],
        *,
        tools: list[Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        self.call_count += 1
        self.call_history.append(
            {
                "messages": messages,
                "tools": tools,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "kwargs": kwargs,
            }
        )
        # 按调用次数抛异常（优先级高于 raise_on_call）
        if self.call_count <= len(self._raise_on_attempts):
            raise self._raise_on_attempts[self.call_count - 1]
        if self._raise_on_call is not None:
            raise self._raise_on_call
        # 队列空时返回默认响应
        if not self._responses:
            return LLMResponse(
                content="mock response",
                usage=Usage(prompt_tokens=5, completion_tokens=3),
                finish_reason="stop",
                model=self.model,
            )
        return self._responses.pop(0)

    async def stream_chat(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        for chunk in self._stream_chunks:
            yield chunk

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not self._embed_vectors:
            # 默认返回固定维度向量
            return [[0.1, 0.2, 0.3] for _ in texts]
        return self._embed_vectors[: len(texts)]


def make_tool_call_response(
    tool_calls: list[tuple[str, str, str]],
    *,
    content: str | None = None,
) -> LLMResponse:
    """构造带工具调用的 LLMResponse，简化测试代码。

    参数：
        tool_calls: [(id, name, arguments_json), ...]
    """
    return LLMResponse(
        content=content,
        tool_calls=[
            ToolCall(id=tid, name=name, arguments=args)
            for tid, name, args in tool_calls
        ],
        finish_reason="tool_calls",
        usage=Usage(prompt_tokens=10, completion_tokens=5),
    )


def make_text_response(text: str) -> LLMResponse:
    """构造纯文本响应。"""
    return LLMResponse(
        content=text,
        finish_reason="stop",
        usage=Usage(prompt_tokens=5, completion_tokens=len(text.split())),
    )
