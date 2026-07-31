"""LLM 适配器集合：OpenAI / GLM / Qwen。

每个适配器继承 BaseLLM，实现厂商特定的客户端创建与请求/响应转换逻辑。
GLM 与 Qwen 均提供 OpenAI 兼容接口，可复用 OpenAIAdapter 的核心实现，
仅 base_url 与默认模型不同，避免重复代码（DRY）。
"""

from __future__ import annotations

from app.llm.adapters.glm import GLMAdapter
from app.llm.adapters.openai import OpenAIAdapter
from app.llm.adapters.qwen import QwenAdapter

__all__ = ["GLMAdapter", "OpenAIAdapter", "QwenAdapter"]
