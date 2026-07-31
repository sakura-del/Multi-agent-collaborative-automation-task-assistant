"""Function Calling 封装。

提供四项核心能力：
1. Python 函数签名 → JSON Schema 自动转换（schema_builder）
2. 工具注册表：注册可调用工具，供模型选择
3. 多轮工具调用循环：模型→工具→结果→模型 直至不再调用工具
4. 工具执行器：解析参数、调用函数、返回结果

设计原则：
- 工具注册与调用解耦：注册时只保存元信息，调用时才执行
- 类型安全：基于 Python type hints 生成 JSON Schema
- 容错：参数解析失败/工具异常不中断循环，回传错误信息给模型让其自愈
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable
from typing import Any, get_origin, get_type_hints

from app.core.exceptions import LLMException
from app.core.logging import get_logger
from app.llm.base import BaseLLM
from app.llm.models import LLMResponse, Message, MessageRole, ToolCall, ToolSchema

logger = get_logger(__name__)

# 工具执行函数类型：同步或异步均可
ToolFunc = Callable[..., Any] | Callable[..., Awaitable[Any]]


# ===== 1. Python 函数签名 → JSON Schema 自动转换 =====


# Python 类型 → JSON Schema 类型映射
_TYPE_MAP: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def python_type_to_json_schema(py_type: type) -> dict[str, Any]:
    """将 Python 类型注解转换为 JSON Schema 类型描述。"""
    # 处理 Optional[X] / Union[X, None]：取非 None 的部分
    origin = get_origin(py_type)
    if origin is None:
        # 简单类型直接查表
        return {"type": _TYPE_MAP.get(py_type, "string")}

    # 处理 list[X] / List[X]
    if origin in (list, list):
        import typing as t

        args = getattr(py_type, "__args__", ())
        items = python_type_to_json_schema(args[0]) if args else {}
        return {"type": "array", "items": items}

    # 处理 dict[K, V]
    if origin in (dict, dict):
        return {"type": "object"}

    # 处理 Optional / Union（取第一个非 None 类型）
    import typing as t

    if origin is t.Union:  # type: ignore[attr-defined]
        args = [a for a in getattr(py_type, "__args__", ()) if a is not type(None)]
        if args:
            return python_type_to_json_schema(args[0])
    return {"type": "string"}


def function_to_tool_schema(
    func: ToolFunc,
    *,
    name: str | None = None,
    description: str | None = None,
) -> ToolSchema:
    """将 Python 函数转换为 ToolSchema。

    通过函数签名与 docstring 自动提取：
    - name: 函数名（可覆盖）
    - description: docstring 首行（可覆盖）
    - parameters: 基于 type hints 与默认值生成 JSON Schema
    """
    func_name = name or func.__name__
    # 描述优先取显式参数，否则取 docstring 首行
    desc = description
    if desc is None and func.__doc__:
        desc = inspect.cleandoc(func.__doc__).split("\n\n", 1)[0].strip()
    if desc is None:
        desc = f"工具 {func_name}"

    sig = inspect.signature(func)
    try:
        hints = get_type_hints(func)
    except Exception:
        # 类型注解解析失败时回退为 string
        hints = {}

    properties: dict[str, Any] = {}
    required: list[str] = []
    for param_name, param in sig.parameters.items():
        # 跳过 *args / **kwargs
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        py_type = hints.get(param_name, str)
        properties[param_name] = python_type_to_json_schema(py_type)
        # 无默认值的参数为必填
        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    return ToolSchema(
        name=func_name,
        description=desc,
        parameters={
            "type": "object",
            "properties": properties,
            "required": required,
        },
    )


# ===== 2. 工具注册表 =====


class ToolRegistry:
    """工具注册中心：管理可被 LLM 调用的工具。

    注册时同时保存 ToolSchema 与执行函数，调用时按名称查找。
    """

    def __init__(self) -> None:
        # 双 dict 同步维护：schema 供模型选择，funcs 供执行器调用
        self._schemas: dict[str, ToolSchema] = {}
        self._funcs: dict[str, ToolFunc] = {}

    def register(
        self,
        func: ToolFunc,
        *,
        name: str | None = None,
        description: str | None = None,
        override: bool = False,
    ) -> ToolSchema:
        """注册工具函数，返回生成的 ToolSchema。

        参数：
            override: 同名工具是否覆盖，默认 False（重名抛异常避免误覆盖）
        """
        schema = function_to_tool_schema(func, name=name, description=description)
        if schema.name in self._schemas and not override:
            raise LLMException(
                f"工具 {schema.name} 已注册，如需覆盖请设 override=True",
                details={"tool_name": schema.name},
            )
        self._schemas[schema.name] = schema
        self._funcs[schema.name] = func
        logger.info("tool_registered", tool_name=schema.name)
        return schema

    def unregister(self, name: str) -> bool:
        """注销工具，返回是否成功。"""
        existed = name in self._schemas
        self._schemas.pop(name, None)
        self._funcs.pop(name, None)
        return existed

    def get_schemas(self) -> list[ToolSchema]:
        """返回所有工具 Schema，供 LLM tools 参数使用。"""
        return list(self._schemas.values())

    def get_func(self, name: str) -> ToolFunc:
        """按名称获取工具执行函数，不存在抛异常。"""
        if name not in self._funcs:
            raise LLMException(f"工具 {name} 未注册", details={"tool_name": name})
        return self._funcs[name]

    def __contains__(self, name: str) -> bool:
        return name in self._schemas

    def __len__(self) -> int:
        return len(self._schemas)


# ===== 3. 工具执行器 =====


async def execute_tool_call(
    tool_call: ToolCall,
    registry: ToolRegistry,
) -> Message:
    """执行单次工具调用，返回 tool 角色消息供回传给模型。

    容错策略：
    - 参数解析失败：回传错误信息，让模型自愈重试
    - 工具执行异常：捕获并回传异常信息，不中断循环
    """
    func = registry.get_func(tool_call.name)
    # 解析参数 JSON
    try:
        args: dict[str, Any] = json.loads(tool_call.arguments) if tool_call.arguments else {}
    except json.JSONDecodeError as exc:
        logger.warning(
            "tool_args_parse_failed",
            tool_name=tool_call.name,
            arguments=tool_call.arguments,
            error=str(exc),
        )
        return Message(
            role=MessageRole.TOOL,
            content=json.dumps(
                {"error": f"参数 JSON 解析失败: {exc}", "raw_arguments": tool_call.arguments},
                ensure_ascii=False,
            ),
            tool_call_id=tool_call.id,
            name=tool_call.name,
        )

    # 调用工具函数（兼容同步/异步）
    try:
        result = func(**args)
        if inspect.isawaitable(result):
            result = await result
    except Exception as exc:
        # 捕获工具异常，回传给模型让其修正（自愈机制的基础）
        logger.warning(
            "tool_exec_failed",
            tool_name=tool_call.name,
            error=str(exc),
        )
        result = {"error": f"工具执行失败: {exc}"}

    # 结果序列化为字符串（dict/list 转 JSON，其余 str() 兜底）
    if isinstance(result, dict | list):
        content = json.dumps(result, ensure_ascii=False, default=str)
    elif isinstance(result, str):
        content = result
    else:
        content = str(result)

    return Message(
        role=MessageRole.TOOL,
        content=content,
        tool_call_id=tool_call.id,
        name=tool_call.name,
    )


# ===== 4. 多轮工具调用循环 =====


async def run_function_call_loop(
    llm: BaseLLM,
    messages: list[Message],
    registry: ToolRegistry,
    *,
    max_iterations: int = 10,
    temperature: float | None = None,
) -> LLMResponse:
    """多轮 Function Calling 循环。

    流程：
        1. 调用 LLM（携带 tools）
        2. 若响应包含 tool_calls：执行所有工具，结果以 tool 消息追加到 messages
        3. 再次调用 LLM（携带工具结果），回到步骤 2
        4. 若 LLM 不再调用工具（finish_reason=stop）或达到上限，返回最终响应

    参数：
        max_iterations: 最大工具调用轮数，防止无限循环（默认 10）
    """
    tools = registry.get_schemas()
    if not tools:
        # 无工具注册时直接普通对话
        return await llm.chat(messages, temperature=temperature)

    current_messages = list(messages)
    for iteration in range(1, max_iterations + 1):
        response = await llm.chat(
            current_messages,
            tools=tools,
            temperature=temperature,
        )

        # 无工具调用 → 循环结束，返回最终文本响应
        if not response.tool_calls:
            logger.info(
                "fc_loop_completed",
                iterations=iteration,
                finish_reason=response.finish_reason,
            )
            return response

        # 追加 assistant 消息（含 tool_calls）以保持上下文连贯
        current_messages.append(
            Message(
                role=MessageRole.ASSISTANT,
                content=response.content,
                tool_calls=response.tool_calls,
            )
        )

        # 执行所有工具调用，结果作为 tool 消息追加
        for tool_call in response.tool_calls:
            tool_msg = await execute_tool_call(tool_call, registry)
            current_messages.append(tool_msg)

        logger.info(
            "fc_loop_iteration",
            iteration=iteration,
            tool_calls_count=len(response.tool_calls),
            tool_names=[tc.name for tc in response.tool_calls],
        )

    # 达到最大轮数仍未结束，抛异常避免无限循环
    raise LLMException(
        f"Function Calling 超过最大轮数 {max_iterations}",
        details={"max_iterations": max_iterations},
    )
