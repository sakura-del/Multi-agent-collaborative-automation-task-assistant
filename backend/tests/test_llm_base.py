"""BaseLLM 抽象基类测试。

覆盖：
- 延迟初始化：客户端首次调用才创建
- 重试机制：可恢复异常重试，不可恢复异常直接抛出
- 用量日志：成功调用记录 token 用量
- function_call 便捷方法复用 chat
"""

from __future__ import annotations

import pytest

from app.llm.base import BaseLLM
from app.llm.models import Message, MessageRole
from tests.llm.mock_llm import MockLLM, make_text_response


class TestBaseLLM:
    """BaseLLM 抽象基类行为测试。"""

    @pytest.mark.asyncio
    async def test_lazy_client_initialization(self):
        """客户端在首次调用时才创建（延迟初始化）。"""
        mock = MockLLM(responses=[make_text_response("hello")])
        # 构造后客户端未创建
        assert mock._initialized is False
        assert mock._client is None
        # 首次调用触发创建
        await mock.chat([Message(role=MessageRole.USER, content="hi")])
        assert mock._initialized is True
        assert mock._client == "mock-client"

    @pytest.mark.asyncio
    async def test_chat_returns_response(self):
        """chat 返回预设响应。"""
        expected = make_text_response("test answer")
        mock = MockLLM(responses=[expected])
        result = await mock.chat([Message(role=MessageRole.USER, content="q")])
        assert result.content == "test answer"
        assert result.finish_reason == "stop"

    @pytest.mark.asyncio
    async def test_chat_records_usage_in_log(self):
        """成功调用记录 token 用量（通过 call_history 验证）。"""
        mock = MockLLM(responses=[make_text_response("ok")])
        await mock.chat([Message(role=MessageRole.USER, content="q")])
        assert mock.call_count == 1
        assert mock.call_history[0]["messages"][0].content == "q"

    @pytest.mark.asyncio
    async def test_retry_on_recoverable_exception(self):
        """可恢复异常（超时类）应触发重试。"""
        # 第1次抛超时异常，第2次成功
        timeout_exc = type("APITimeoutError", (Exception,), {})("timeout")
        mock = MockLLM(
            responses=[make_text_response("ok after retry")],
            raise_on_attempts=[timeout_exc],
            max_retries=3,
        )
        result = await mock.chat([Message(role=MessageRole.USER, content="q")])
        # 重试后成功
        assert result.content == "ok after retry"
        assert mock.call_count == 2

    @pytest.mark.asyncio
    async def test_no_retry_on_non_recoverable_exception(self):
        """不可恢复异常（鉴权失败）不重试，直接抛出。"""
        auth_exc = type("AuthenticationError", (Exception,), {})("401 unauthorized")
        mock = MockLLM(
            responses=[make_text_response("never reached")],
            raise_on_call=auth_exc,
            max_retries=3,
        )
        with pytest.raises(Exception) as exc_info:
            await mock.chat([Message(role=MessageRole.USER, content="q")])
        # 仅调用一次，未重试
        assert mock.call_count == 1
        assert "401" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_retry_exhausted_raises(self):
        """重试耗尽后抛出最后异常。"""
        timeout_exc = type("APITimeoutError", (Exception,), {})("persistent timeout")
        mock = MockLLM(
            responses=[],
            raise_on_call=timeout_exc,
            max_retries=2,
        )
        with pytest.raises(Exception) as exc_info:
            await mock.chat([Message(role=MessageRole.USER, content="q")])
        # 重试2次均失败
        assert mock.call_count == 2
        assert "persistent timeout" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_function_call_reuses_chat(self):
        """function_call 复用 chat + tools 参数。"""
        mock = MockLLM(responses=[make_text_response("done")])
        from app.llm.models import ToolSchema

        tools = [
            ToolSchema(
                name="search",
                description="搜索",
                parameters={"type": "object", "properties": {}},
            )
        ]
        result = await mock.function_call(
            [Message(role=MessageRole.USER, content="do search")],
            tools,
        )
        assert result.content == "done"
        # 验证 tools 透传给 _raw_chat
        assert mock.call_history[0]["tools"] == tools

    def test_is_retryable_timeout(self):
        """超时类异常可重试。"""
        exc = type("APITimeoutError", (Exception,), {})()
        assert BaseLLM._is_retryable(exc) is True  # type: ignore[arg-type]

    def test_is_retryable_rate_limit(self):
        """限流类异常可重试。"""
        exc = type("RateLimitError", (Exception,), {})()
        assert BaseLLM._is_retryable(exc) is True  # type: ignore[arg-type]

    def test_is_retryable_auth_error(self):
        """鉴权异常不可重试。"""
        exc = type("AuthenticationError", (Exception,), {})()
        assert BaseLLM._is_retryable(exc) is False  # type: ignore[arg-type]

    def test_repr(self):
        """__repr__ 包含 provider 与 model。"""
        mock = MockLLM(model="gpt-4", provider="openai")
        repr_str = repr(mock)
        assert "openai" in repr_str
        assert "gpt-4" in repr_str
