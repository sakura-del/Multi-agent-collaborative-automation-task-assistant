"""Function Calling 封装测试。

覆盖：
- python_type_to_json_schema：Python 类型 → JSON Schema 转换
- function_to_tool_schema：函数签名 → ToolSchema 转换
- ToolRegistry：注册/查询/注销/重名处理
- execute_tool_call：参数解析/异常容错/同步异步函数
- run_function_call_loop：单轮/多轮/最大轮数限制
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.core.exceptions import LLMException
from app.llm.function_calling import (
    ToolRegistry,
    execute_tool_call,
    function_to_tool_schema,
    python_type_to_json_schema,
    run_function_call_loop,
)
from app.llm.models import Message, MessageRole, ToolCall
from tests.llm.mock_llm import MockLLM, make_text_response, make_tool_call_response

# ===== 1. 类型转换测试 =====


class TestPythonTypeToSchema:
    """Python 类型 → JSON Schema 转换测试。"""

    def test_str_type(self):
        assert python_type_to_json_schema(str) == {"type": "string"}

    def test_int_type(self):
        assert python_type_to_json_schema(int) == {"type": "integer"}

    def test_float_type(self):
        assert python_type_to_json_schema(float) == {"type": "number"}

    def test_bool_type(self):
        assert python_type_to_json_schema(bool) == {"type": "boolean"}

    def test_list_type(self):
        """list[str] → array with items=string。"""

        result = python_type_to_json_schema(list[str])
        assert result["type"] == "array"
        assert result["items"] == {"type": "string"}

    def test_dict_type(self):
        assert python_type_to_json_schema(dict) == {"type": "object"}


# ===== 2. 函数签名 → ToolSchema 转换测试 =====


class TestFunctionToToolSchema:
    """函数签名转 ToolSchema 测试。"""

    def test_basic_function(self):
        """基础函数：name + description + parameters。"""

        def search(query: str, top_k: int = 5) -> list[str]:
            """搜索文档。"""
            return []

        schema = function_to_tool_schema(search)
        assert schema.name == "search"
        assert "搜索" in schema.description
        assert schema.parameters["type"] == "object"
        assert "query" in schema.parameters["properties"]
        assert "top_k" in schema.parameters["properties"]
        # query 无默认值 → 必填；top_k 有默认值 → 非必填
        assert "query" in schema.parameters["required"]
        assert "top_k" not in schema.parameters["required"]

    def test_function_without_docstring(self):
        """无 docstring 的函数使用默认描述。"""

        def no_doc(x: str) -> str:
            return x

        schema = function_to_tool_schema(no_doc)
        assert schema.description == "工具 no_doc"

    def test_custom_name_and_description(self):
        """显式指定 name 与 description 覆盖默认值。"""

        def my_func(x: str) -> str:
            """原始文档"""
            return x

        schema = function_to_tool_schema(my_func, name="custom_name", description="custom desc")
        assert schema.name == "custom_name"
        assert schema.description == "custom desc"

    def test_function_with_var_args_skipped(self):
        """*args / **kwargs 不进入 parameters。"""

        def func(a: str, *args: Any, **kwargs: Any) -> None:
            return None

        schema = function_to_tool_schema(func)
        assert "a" in schema.parameters["properties"]
        # *args 与 **kwargs 不应出现
        assert "args" not in schema.parameters["properties"]
        assert "kwargs" not in schema.parameters["properties"]


# ===== 3. ToolRegistry 测试 =====


class TestToolRegistry:
    """工具注册中心测试。"""

    def test_register_and_get_schemas(self):
        """注册后可获取 Schema。"""
        registry = ToolRegistry()

        def add(a: int, b: int) -> int:
            """加法"""
            return a + b

        registry.register(add)
        schemas = registry.get_schemas()
        assert len(schemas) == 1
        assert schemas[0].name == "add"

    def test_register_duplicate_raises(self):
        """重名注册抛异常（未设 override）。"""
        registry = ToolRegistry()

        def f1(x: str) -> str:
            return x

        def f2(x: str) -> str:
            return x

        # 通过 name= 强制同名，触发重名检测
        registry.register(f1, name="same_name")
        with pytest.raises(LLMException):
            registry.register(f2, name="same_name")

    def test_register_with_override(self):
        """override=True 允许覆盖同名工具。"""
        registry = ToolRegistry()

        def f1(x: str) -> str:
            return "v1"

        def f2(x: str) -> str:
            return "v2"

        registry.register(f1, name="same_name")
        registry.register(f2, name="same_name", override=True)
        assert len(registry) == 1  # 仍是1个工具（被覆盖）

    def test_unregister(self):
        """注销工具。"""
        registry = ToolRegistry()

        def f(x: str) -> str:
            return x

        registry.register(f)
        assert "f" in registry
        assert registry.unregister("f") is True
        assert "f" not in registry
        # 再次注销返回 False
        assert registry.unregister("f") is False

    def test_get_func_not_found_raises(self):
        """获取未注册工具抛异常。"""
        registry = ToolRegistry()
        with pytest.raises(LLMException):
            registry.get_func("nonexistent")


# ===== 4. execute_tool_call 测试 =====


class TestExecuteToolCall:
    """工具执行器测试。"""

    @pytest.mark.asyncio
    async def test_execute_sync_function(self):
        """执行同步函数工具。"""
        registry = ToolRegistry()

        def add(a: int, b: int) -> int:
            return a + b

        registry.register(add)
        call = ToolCall(id="1", name="add", arguments='{"a":1,"b":2}')
        result = await execute_tool_call(call, registry)
        assert result.role == MessageRole.TOOL
        assert result.content == "3"
        assert result.tool_call_id == "1"
        assert result.name == "add"

    @pytest.mark.asyncio
    async def test_execute_async_function(self):
        """执行异步函数工具。"""
        registry = ToolRegistry()

        async def fetch(url: str) -> dict[str, str]:
            return {"status": "ok", "url": url}

        registry.register(fetch)
        call = ToolCall(id="1", name="fetch", arguments='{"url":"http://x"}')
        result = await execute_tool_call(call, registry)
        # dict 结果序列化为 JSON
        parsed = json.loads(result.content)
        assert parsed["status"] == "ok"

    @pytest.mark.asyncio
    async def test_invalid_arguments_returns_error(self):
        """参数 JSON 解析失败时回传错误信息（不抛异常）。"""
        registry = ToolRegistry()

        def f(x: str) -> str:
            return x

        registry.register(f)
        call = ToolCall(id="1", name="f", arguments="not a json")
        result = await execute_tool_call(call, registry)
        parsed = json.loads(result.content)
        assert "error" in parsed
        assert "raw_arguments" in parsed

    @pytest.mark.asyncio
    async def test_function_exception_returns_error(self):
        """工具函数抛异常时回传错误信息（不中断循环）。"""
        registry = ToolRegistry()

        def boom(x: str) -> str:
            raise RuntimeError("boom!")

        registry.register(boom)
        call = ToolCall(id="1", name="boom", arguments='{"x":"a"}')
        result = await execute_tool_call(call, registry)
        parsed = json.loads(result.content)
        assert "error" in parsed
        assert "boom" in parsed["error"]

    @pytest.mark.asyncio
    async def test_empty_arguments(self):
        """空 arguments 视为无参调用。"""
        registry = ToolRegistry()

        def get_time() -> str:
            return "2026-01-01"

        registry.register(get_time)
        call = ToolCall(id="1", name="get_time", arguments="")
        result = await execute_tool_call(call, registry)
        assert result.content == "2026-01-01"


# ===== 5. run_function_call_loop 测试 =====


class TestFunctionCallLoop:
    """多轮 Function Calling 循环测试。"""

    @pytest.mark.asyncio
    async def test_no_tools_direct_chat(self):
        """无工具注册时直接普通对话。"""
        mock = MockLLM(responses=[make_text_response("answer")])
        registry = ToolRegistry()
        result = await run_function_call_loop(
            mock,
            [Message(role=MessageRole.USER, content="q")],
            registry,
        )
        assert result.content == "answer"
        assert mock.call_count == 1

    @pytest.mark.asyncio
    async def test_single_tool_call(self):
        """单轮工具调用：模型调用1次工具后给出最终答案。"""
        registry = ToolRegistry()

        def get_weather(city: str) -> str:
            """获取天气"""
            return f"{city}: 晴"

        registry.register(get_weather)

        # 第1轮：模型请求调用工具
        # 第2轮：模型基于工具结果给出最终文本
        mock = MockLLM(
            responses=[
                make_tool_call_response([("call_1", "get_weather", '{"city":"北京"}')]),
                make_text_response("北京今天天气晴朗"),
            ]
        )
        result = await run_function_call_loop(
            mock,
            [Message(role=MessageRole.USER, content="北京天气？")],
            registry,
        )
        assert result.content == "北京今天天气晴朗"
        # 应调用 LLM 2 次
        assert mock.call_count == 2

    @pytest.mark.asyncio
    async def test_multi_turn_tool_calls(self):
        """多轮工具调用：模型连续调用2个工具后给出最终答案。"""
        registry = ToolRegistry()

        def get_name() -> str:
            return "Alice"

        def get_greeting(name: str) -> str:
            return f"Hello {name}"

        registry.register(get_name)
        registry.register(get_greeting)

        # 3轮：调用get_name → 调用get_greeting → 最终回答
        mock = MockLLM(
            responses=[
                make_tool_call_response([("c1", "get_name", "{}")]),
                make_tool_call_response([("c2", "get_greeting", '{"name":"Alice"}')]),
                make_text_response("问候完成"),
            ]
        )
        result = await run_function_call_loop(
            mock,
            [Message(role=MessageRole.USER, content="问候流程")],
            registry,
        )
        assert result.content == "问候完成"
        assert mock.call_count == 3

    @pytest.mark.asyncio
    async def test_max_iterations_exceeded(self):
        """达到最大轮数仍调用工具时抛异常。"""
        registry = ToolRegistry()

        def loop_tool() -> str:
            return "loop"

        registry.register(loop_tool)

        # 每轮都调用工具，永不停止
        mock = MockLLM(
            responses=[make_tool_call_response([("c1", "loop_tool", "{}")]) for _ in range(15)]
        )
        with pytest.raises(LLMException) as exc_info:
            await run_function_call_loop(
                mock,
                [Message(role=MessageRole.USER, content="loop")],
                registry,
                max_iterations=3,
            )
        assert "最大轮数" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_message_history_accumulation(self):
        """工具调用后 messages 正确累积（assistant + tool 消息）。"""
        registry = ToolRegistry()

        def echo(text: str) -> str:
            return text

        registry.register(echo)

        mock = MockLLM(
            responses=[
                make_tool_call_response([("c1", "echo", '{"text":"hi"}')]),
                make_text_response("done"),
            ]
        )
        await run_function_call_loop(
            mock,
            [Message(role=MessageRole.USER, content="echo hi")],
            registry,
        )
        # 第2次调用 LLM 时，messages 应包含：原始user + assistant(tool_calls) + tool(result)
        second_call_messages = mock.call_history[1]["messages"]
        assert len(second_call_messages) == 3
        assert second_call_messages[0].role == MessageRole.USER
        assert second_call_messages[1].role == MessageRole.ASSISTANT
        assert second_call_messages[1].tool_calls is not None
        assert second_call_messages[2].role == MessageRole.TOOL
        assert second_call_messages[2].tool_call_id == "c1"
