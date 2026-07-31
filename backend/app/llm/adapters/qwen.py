"""Qwen 适配器（阿里通义千问）。

Qwen 通过 DashScope 兼容模式（compatible-mode/v1）提供 OpenAI 兼容接口，
复用 OpenAIAdapter 的请求/响应逻辑，仅覆写 provider 与默认 base_url。
"""

from __future__ import annotations

from app.llm.adapters.openai import OpenAIAdapter


class QwenAdapter(OpenAIAdapter):
    """通义千问 Qwen 系列适配器，支持 qwen-turbo / qwen-plus / qwen-max 等模型。"""

    def __init__(
        self,
        model: str = "qwen-turbo",
        *,
        api_key: str | None = None,
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        timeout: int = 30,
        max_retries: int = 3,
        default_temperature: float = 0.7,
        default_max_tokens: int | None = None,
        embedding_model: str = "text-embedding-v3",
    ) -> None:
        super().__init__(
            model=model,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
            default_temperature=default_temperature,
            default_max_tokens=default_max_tokens,
            embedding_model=embedding_model,
        )
        self.provider = "qwen"
