"""OpenAI 适配器：基于 openai SDK 实现 GPT-4 / GPT-4o 等模型接入。

设计要点：
- 复用 BaseLLM 的重试/超时/日志/用量统计逻辑，仅实现 _raw_chat / stream_chat / embed
- 消息转换：统一 Message → OpenAI dict 格式
- 响应转换：OpenAI ChatCompletion → 统一 LLMResponse
- 异步调用：使用 AsyncOpenAI 客户端，不阻塞事件循环
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from openai import AsyncOpenAI

from app.llm.base import BaseLLM
from app.llm.models import LLMResponse, Message, MessageRole, ToolCall, Usage


class OpenAIAdapter(BaseLLM):
    """OpenAI 官方适配器，支持 GPT-4 / GPT-4o / GPT-4o-mini 等模型。

    GLM/Qwen 适配器复用此实现，仅 base_url 与默认模型不同。
    """

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: int = 30,
        max_retries: int = 3,
        default_temperature: float = 0.7,
        default_max_tokens: int | None = None,
        embedding_model: str = "text-embedding-3-small",
    ) -> None:
        super().__init__(
            model,
            provider="openai",
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            default_temperature=default_temperature,
            default_max_tokens=default_max_tokens,
        )
        # 嵌入模型独立配置，与聊天模型解耦
        self.embedding_model = embedding_model

    def _create_client(self) -> AsyncOpenAI:
        """创建 AsyncOpenAI 异步客户端。"""
        return AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout,
            max_retries=0,  # 重试由 BaseLLM.chat 统一管理，避免双层重试
        )

    # ===== 消息/工具格式转换 =====

    @staticmethod
    def _message_to_dict(msg: Message) -> dict[str, Any]:
        """统一 Message → OpenAI 消息 dict。

        OpenAI 字段约定：
        - role=tool 必须带 tool_call_id
        - role=assistant 且发起工具调用时携带 tool_calls
        - content 为 None 时需省略（OpenAI 不接受 None content）
        """
        item: dict[str, Any] = {"role": msg.role.value}
        if msg.content is not None:
            item["content"] = msg.content
        if msg.name is not None:
            item["name"] = msg.name
        if msg.tool_call_id is not None:
            item["tool_call_id"] = msg.tool_call_id
        if msg.tool_calls:
            # 转换为 OpenAI 工具调用格式
            item["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                }
                for tc in msg.tool_calls
            ]
        return item

    @staticmethod
    def _tool_to_dict(tool: Any) -> dict[str, Any]:
        """ToolSchema → OpenAI tools 数组项格式。"""
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }

    # ===== 核心调用实现 =====

    async def _raw_chat(
        self,
        messages: list[Message],
        *,
        tools: list[Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """单次调用 OpenAI Chat Completions。"""
        client: AsyncOpenAI = self._ensure_client()  # type: ignore[assignment]
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [self._message_to_dict(m) for m in messages],
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = [self._tool_to_dict(t) for t in tools]
        # 透传额外参数（如 top_p、stop 等），避免接口扩展时频繁改签名
        payload.update(kwargs)

        response = await client.chat.completions.create(**payload)
        return self._parse_response(response)

    def _parse_response(self, response: Any) -> LLMResponse:
        """将 OpenAI ChatCompletion 响应转换为统一 LLMResponse。"""
        choice = response.choices[0]
        msg = choice.message
        # 解析工具调用（若有）
        tool_calls: list[ToolCall] | None = None
        if msg.tool_calls:
            tool_calls = [
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=tc.function.arguments,
                )
                for tc in msg.tool_calls
            ]

        usage = Usage()
        if response.usage:
            usage = Usage(
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
            )

        return LLMResponse(
            content=msg.content,
            role=MessageRole.ASSISTANT,
            tool_calls=tool_calls,
            usage=usage,
            finish_reason=choice.finish_reason,
            model=response.model,
        )

    async def stream_chat(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """流式聊天：逐 chunk yield 文本增量。

        注意：流式模式下不返回用量统计，如需统计请用 chat()。
        """
        client: AsyncOpenAI = self._ensure_client()  # type: ignore[assignment]
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [self._message_to_dict(m) for m in messages],
            "stream": True,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        payload.update(kwargs)

        stream = await client.chat.completions.create(**payload)
        async for chunk in stream:
            # 跳过空 chunk（如首个 chunk 仅含 role）
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """文本向量化：调用 OpenAI Embeddings 接口。"""
        if not texts:
            return []
        client: AsyncOpenAI = self._ensure_client()  # type: ignore[assignment]
        # 批量调用：OpenAI 单次最多 2048 条，此处不做分片，由上层控制批量大小
        response = await client.embeddings.create(
            input=texts,
            model=self.embedding_model,
        )
        # 按 index 排序保证顺序与输入一致
        sorted_data = sorted(response.data, key=lambda x: x.index)
        return [item.embedding for item in sorted_data]


# 便于直接 from app.llm.adapters.openai import parse_tool_arguments
def parse_tool_arguments(arguments: str) -> dict[str, Any]:
    """解析工具调用 arguments（JSON 字符串）为 dict。

    模型偶尔会返回空字符串或非法 JSON，此处容错处理。
    """
    if not arguments:
        return {}
    try:
        result = json.loads(arguments)
        return result if isinstance(result, dict) else {"value": result}
    except json.JSONDecodeError:
        # 容错：返回原始字符串供调用方诊断
        return {"_raw": arguments}
