"""底层大模型层：LLM 抽象接口、多模型适配器、Function Calling、Prompt 管理。

模块组织：
- base: BaseLLM 抽象基类，统一 chat/stream_chat/embed/function_call 接口
- models: 跨适配器统一数据模型（Message/ToolSchema/LLMResponse 等）
- adapters: OpenAI/GLM/Qwen 适配器实现
- function_calling: Function Calling 封装（Schema 转换 + 多轮调用循环）
- factory: LLM 工厂与热切换管理器（自动降级）
- prompts: Prompt 模板管理（Jinja2 + 版本管理 + A/B 测试）
"""

from __future__ import annotations

from app.llm.base import BaseLLM
from app.llm.factory import LLMManager, create_llm, get_llm_manager
from app.llm.function_calling import (
    ToolRegistry,
    execute_tool_call,
    function_to_tool_schema,
    run_function_call_loop,
)
from app.llm.models import (
    LLMResponse,
    Message,
    MessageRole,
    ToolCall,
    ToolSchema,
    Usage,
)
from app.llm.prompts import PromptManager, PromptTemplate, get_prompt_manager

__all__ = [
    # 抽象基类
    "BaseLLM",
    # 数据模型
    "LLMResponse",
    "Message",
    "MessageRole",
    "ToolCall",
    "ToolSchema",
    "Usage",
    # 工厂与热切换
    "LLMManager",
    "create_llm",
    "get_llm_manager",
    # Function Calling
    "ToolRegistry",
    "execute_tool_call",
    "function_to_tool_schema",
    "run_function_call_loop",
    # Prompt 管理
    "PromptManager",
    "PromptTemplate",
    "get_prompt_manager",
]
