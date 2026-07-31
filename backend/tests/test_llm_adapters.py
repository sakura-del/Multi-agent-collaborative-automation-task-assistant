"""LLM 适配器测试（OpenAI/GLM/Qwen）。

由于适配器实际调用需要真实 API Key，此处主要测试：
- 消息/工具格式转换逻辑（_message_to_dict / _tool_to_dict）
- 响应解析逻辑（_parse_response）
- 工厂函数 create_llm 创建正确适配器类型
- GLM/Qwen 适配器继承 OpenAIAdapter 行为
- parse_tool_arguments 容错
"""

from __future__ import annotations

from typing import Any

import pytest

from app.llm.adapters import GLMAdapter, OpenAIAdapter, QwenAdapter
from app.llm.adapters.openai import parse_tool_arguments
from app.llm.factory import create_llm
from app.llm.models import Message, MessageRole, ToolCall, ToolSchema


class TestMessageConversion:
    """OpenAI 适配器消息格式转换测试。"""

    def test_user_message_to_dict(self):
        """user 消息转换：role + content。"""
        msg = Message(role=MessageRole.USER, content="hello")
        result = OpenAIAdapter._message_to_dict(msg)
        assert result == {"role": "user", "content": "hello"}

    def test_system_message_to_dict(self):
        """system 消息转换。"""
        msg = Message(role=MessageRole.SYSTEM, content="you are helpful")
        result = OpenAIAdapter._message_to_dict(msg)
        assert result["role"] == "system"
        assert result["content"] == "you are helpful"

    def test_assistant_message_with_tool_calls(self):
        """assistant 消息携带 tool_calls 时正确转换。"""
        msg = Message(
            role=MessageRole.ASSISTANT,
            content=None,
            tool_calls=[
                ToolCall(id="call_1", name="search", arguments='{"q":"test"}'),
            ],
        )
        result = OpenAIAdapter._message_to_dict(msg)
        # content 为 None 时不应包含在 dict 中
        assert "content" not in result
        assert result["tool_calls"][0]["function"]["name"] == "search"
        assert result["tool_calls"][0]["function"]["arguments"] == '{"q":"test"}'

    def test_tool_message_with_tool_call_id(self):
        """tool 角色消息携带 tool_call_id。"""
        msg = Message(
            role=MessageRole.TOOL,
            content="result",
            tool_call_id="call_1",
            name="search",
        )
        result = OpenAIAdapter._message_to_dict(msg)
        assert result["role"] == "tool"
        assert result["tool_call_id"] == "call_1"
        assert result["name"] == "search"
        assert result["content"] == "result"


class TestToolConversion:
    """工具 Schema 转换测试。"""

    def test_tool_to_dict_basic(self):
        """基础工具转换：type=function + name/description/parameters。"""
        tool = ToolSchema(
            name="search",
            description="搜索",
            parameters={
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": ["q"],
            },
        )
        result = OpenAIAdapter._tool_to_dict(tool)
        assert result["type"] == "function"
        assert result["function"]["name"] == "search"
        assert result["function"]["description"] == "搜索"
        assert result["function"]["parameters"]["required"] == ["q"]


class TestResponseParsing:
    """响应解析测试，使用 mock 对象模拟 OpenAI 响应。"""

    def test_parse_text_response(self):
        """纯文本响应解析。"""
        adapter = OpenAIAdapter(model="gpt-4o-mini", api_key="sk-test")
        # 构造 mock 响应对象（模拟 openai SDK 返回结构）
        mock_response = _make_mock_response(
            content="hello",
            finish_reason="stop",
            model="gpt-4o-mini",
            prompt_tokens=10,
            completion_tokens=5,
        )
        result = adapter._parse_response(mock_response)
        assert result.content == "hello"
        assert result.finish_reason == "stop"
        assert result.model == "gpt-4o-mini"
        assert result.usage.prompt_tokens == 10
        assert result.usage.completion_tokens == 5
        assert result.tool_calls is None

    def test_parse_tool_call_response(self):
        """工具调用响应解析。"""
        adapter = OpenAIAdapter(model="gpt-4o-mini", api_key="sk-test")
        mock_response = _make_mock_response(
            content=None,
            finish_reason="tool_calls",
            tool_calls=[("call_1", "search", '{"q":"test"}')],
        )
        result = adapter._parse_response(mock_response)
        assert result.content is None
        assert result.finish_reason == "tool_calls"
        assert result.tool_calls is not None
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "search"
        assert result.tool_calls[0].arguments == '{"q":"test"}'


class TestAdapterFactory:
    """工厂函数测试。"""

    def test_create_openai_adapter(self):
        """工厂函数创建 OpenAI 适配器。"""
        llm = create_llm("openai", "gpt-4o", api_key="sk-test")
        assert isinstance(llm, OpenAIAdapter)
        assert llm.model == "gpt-4o"
        assert llm.provider == "openai"

    def test_create_glm_adapter(self):
        """工厂函数创建 GLM 适配器。"""
        llm = create_llm("glm", "glm-4-flash", api_key="glm-test")
        assert isinstance(llm, GLMAdapter)
        assert llm.model == "glm-4-flash"
        assert llm.provider == "glm"

    def test_create_qwen_adapter(self):
        """工厂函数创建 Qwen 适配器。"""
        llm = create_llm("qwen", "qwen-turbo", api_key="qwen-test")
        assert isinstance(llm, QwenAdapter)
        assert llm.model == "qwen-turbo"
        assert llm.provider == "qwen"

    def test_create_unsupported_provider_raises(self):
        """不支持的 provider 抛出 LLMException。"""
        from app.core.exceptions import LLMException

        with pytest.raises(LLMException) as exc_info:
            create_llm("anthropic", "claude-3")
        assert "anthropic" in str(exc_info.value)

    def test_glm_inherits_openai_adapter(self):
        """GLM 适配器继承 OpenAIAdapter（复用 OpenAI 兼容接口）。"""
        assert issubclass(GLMAdapter, OpenAIAdapter)

    def test_qwen_inherits_openai_adapter(self):
        """Qwen 适配器继承 OpenAIAdapter。"""
        assert issubclass(QwenAdapter, OpenAIAdapter)

    def test_glm_default_base_url(self):
        """GLM 适配器默认 base_url 为智谱接口。"""
        llm = GLMAdapter(model="glm-4", api_key="test")
        assert "bigmodel" in llm.base_url

    def test_qwen_default_base_url(self):
        """Qwen 适配器默认 base_url 为 DashScope 接口。"""
        llm = QwenAdapter(model="qwen-turbo", api_key="test")
        assert "dashscope" in llm.base_url


class TestParseToolArguments:
    """parse_tool_arguments 容错测试。"""

    def test_valid_json(self):
        """合法 JSON 解析为 dict。"""
        result = parse_tool_arguments('{"q":"test","n":3}')
        assert result == {"q": "test", "n": 3}

    def test_empty_string(self):
        """空字符串返回空 dict。"""
        result = parse_tool_arguments("")
        assert result == {}

    def test_invalid_json_returns_raw(self):
        """非法 JSON 容错返回 _raw 字段。"""
        result = parse_tool_arguments("not a json")
        assert "_raw" in result
        assert result["_raw"] == "not a json"

    def test_array_json_wraps_value(self):
        """数组 JSON 包装为 value 字段。"""
        result = parse_tool_arguments("[1,2,3]")
        assert result == {"value": [1, 2, 3]}


# ===== 测试辅助 =====


def _make_mock_response(
    content: str | None,
    finish_reason: str = "stop",
    model: str = "mock",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    tool_calls: list[tuple[str, str, str]] | None = None,
) -> Any:
    """构造模拟 OpenAI ChatCompletion 响应对象。

    使用 SimpleNamespace 模拟 SDK 返回的属性访问结构。
    """

    class _Func:
        def __init__(self, name: str, arguments: str) -> None:
            self.name = name
            self.arguments = arguments

    class _ToolCall:
        def __init__(self, tid: str, name: str, args: str) -> None:
            self.id = tid
            self.type = "function"
            self.function = _Func(name, args)

    class _Message:
        def __init__(self) -> None:
            self.content = content
            self.tool_calls = (
                [_ToolCall(tid, name, args) for tid, name, args in tool_calls]
                if tool_calls
                else None
            )

    class _Choice:
        def __init__(self) -> None:
            self.message = _Message()
            self.finish_reason = finish_reason

    class _Usage:
        def __init__(self) -> None:
            self.prompt_tokens = prompt_tokens
            self.completion_tokens = completion_tokens

    class _Response:
        def __init__(self) -> None:
            self.choices = [_Choice()]
            self.usage = _Usage()
            self.model = model

    return _Response()
