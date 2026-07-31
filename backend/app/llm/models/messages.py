"""LLM 层统一数据模型。

定义跨适配器通用的消息、工具、响应结构，
使 OpenAI/GLM/Qwen 适配器返回一致的数据类型，上层无需关心具体厂商。

设计原则：
- Pydantic BaseModel，便于校验与序列化
- 字段命名与 OpenAI API 对齐，降低适配器转换成本
- 不可变配置（frozen=False 但约定不就地修改），避免跨模块共享时被误改
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class MessageRole(StrEnum):
    """消息角色，与 OpenAI Chat Completion 角色对齐。"""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"  # 工具调用结果回传


class ToolCall(BaseModel):
    """模型发起的一次工具调用请求。"""

    id: str = Field(..., description="工具调用唯一标识，回传结果时需匹配")
    name: str = Field(..., description="工具函数名")
    arguments: str = Field(..., description="工具参数（JSON 字符串），由调用方解析为 dict")


class Message(BaseModel):
    """聊天消息。"""

    role: MessageRole
    content: str | None = Field(default=None, description="消息文本内容")
    name: str | None = Field(default=None, description="发送者名称（可选）")
    # assistant 消息可能携带工具调用请求
    tool_calls: list[ToolCall] | None = None
    # role=tool 时，标识对应哪个 tool_call 的结果
    tool_call_id: str | None = None


class ToolSchema(BaseModel):
    """工具 Schema：描述一个可被模型调用的函数。

    parameters 字段为 JSON Schema（draft-07），描述函数参数结构。
    """

    name: str = Field(..., description="函数名")
    description: str = Field(..., description="函数用途说明，辅助模型决策")
    parameters: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}},
        description="参数的 JSON Schema",
    )


class Usage(BaseModel):
    """Token 用量统计，用于成本追踪。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class LLMResponse(BaseModel):
    """LLM 调用统一响应。

    无论哪个适配器，最终都转为此结构，上层无需感知厂商差异。
    """

    content: str | None = Field(default=None, description="模型生成的文本")
    role: MessageRole = MessageRole.ASSISTANT
    tool_calls: list[ToolCall] | None = Field(default=None, description="模型请求的工具调用列表")
    usage: Usage = Field(default_factory=Usage)
    finish_reason: str | None = Field(
        default=None, description="结束原因：stop/length/tool_calls/error"
    )
    model: str | None = Field(default=None, description="实际生成响应的模型名")
    raw: dict[str, Any] | None = Field(
        default=None,
        description="原始响应（调试用，生产环境不应依赖此字段）",
    )
