"""LLM 抽象基类：统一推理接口。

阶段2任务1：定义 BaseLLM，统一 chat()/stream_chat()/embed()/function_call() 接口签名。

设计模式：模板方法
- BaseLLM.chat 负责重试、超时、日志、用量统计等通用逻辑
- 子类只需实现 _raw_chat（单次原始调用）与 stream_chat / embed
- function_call 复用 chat + tools 参数，无需子类重复实现

设计要点：
- 延迟初始化：客户端在首次调用时才创建，避免闲置占用资源（内存优化）
- 异步接口：所有方法 async，不阻塞事件循环（并行优化）
- tenacity 重试：对可恢复异常（超时/限流）自动重试，不可恢复异常直接抛出
- 用量追踪：每次调用记录 token 用量，供成本预算模块统计
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from app.core.logging import get_logger
from app.llm.models import LLMResponse, Message, ToolSchema, Usage

logger = get_logger(__name__)


class BaseLLM(ABC):
    """LLM 适配器抽象基类。

    子类需实现：
        _create_client() -> Any     延迟创建厂商 SDK 客户端
        _raw_chat(...) -> LLMResponse   单次原始调用（不含重试/日志）
        stream_chat(...) -> AsyncIterator[str]
        embed(...) -> list[list[float]]

    通用逻辑（重试/超时/日志/用量）由本基类的 chat() 统一处理。
    """

    def __init__(
        self,
        model: str,
        *,
        provider: str,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: int = 30,
        max_retries: int = 3,
        default_temperature: float = 0.7,
        default_max_tokens: int | None = None,
    ) -> None:
        self.model = model
        self.provider = provider
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self.max_retries = max_retries
        self.default_temperature = default_temperature
        self.default_max_tokens = default_max_tokens
        # 延迟初始化：客户端在首次调用时才创建
        self._client: Any = None
        self._initialized = False

    # ===== 客户端生命周期 =====

    def _ensure_client(self) -> Any:
        """延迟创建客户端，仅首次调用时初始化。"""
        if not self._initialized:
            self._client = self._create_client()
            self._initialized = True
            logger.debug(
                "llm_client_initialized",
                provider=self.provider,
                model=self.model,
            )
        return self._client

    @abstractmethod
    def _create_client(self) -> Any:
        """创建厂商 SDK 客户端实例。子类实现具体厂商的连接逻辑。"""

    # ===== 核心推理接口 =====

    @abstractmethod
    async def _raw_chat(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSchema] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """单次原始调用，子类实现具体厂商的请求构建与响应解析。

        不含重试/日志逻辑，由 chat() 统一包裹。
        """

    async def chat(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSchema] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """聊天补全：统一入口，含重试/超时/日志/用量统计。

        上层 Agent 应调用此方法，而非 _raw_chat。
        """
        # 确保客户端已初始化（延迟创建）
        self._ensure_client()

        temp = temperature if temperature is not None else self.default_temperature
        tokens = max_tokens if max_tokens is not None else self.default_max_tokens

        last_exc: Exception | None = None
        # 手动重试循环：便于区分可恢复与不可恢复异常
        for attempt in range(1, self.max_retries + 1):
            try:
                response = await self._raw_chat(
                    messages,
                    tools=tools,
                    temperature=temp,
                    max_tokens=tokens,
                    **kwargs,
                )
                # 记录用量与调用元信息，供成本追踪
                logger.info(
                    "llm_chat_ok",
                    provider=self.provider,
                    model=response.model or self.model,
                    attempt=attempt,
                    prompt_tokens=response.usage.prompt_tokens,
                    completion_tokens=response.usage.completion_tokens,
                    finish_reason=response.finish_reason,
                    has_tool_calls=bool(response.tool_calls),
                )
                return response
            except Exception as exc:
                last_exc = exc
                # 判断是否可重试：超时/限流类异常可重试，其余直接抛出
                if not self._is_retryable(exc) or attempt >= self.max_retries:
                    logger.error(
                        "llm_chat_failed",
                        provider=self.provider,
                        model=self.model,
                        attempt=attempt,
                        error=str(exc),
                    )
                    raise
                logger.warning(
                    "llm_chat_retry",
                    provider=self.provider,
                    model=self.model,
                    attempt=attempt,
                    error=str(exc),
                )

        # 理论不可达（循环内必 return 或 raise），保险起见
        assert last_exc is not None
        raise last_exc

    @abstractmethod
    async def stream_chat(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """流式聊天：逐 token 生成，适用于实时交互场景。

        返回异步迭代器，每次 yield 一个文本增量。
        """
        # yield 让此方法成为 async generator；子类需覆盖实现
        yield ""  # pragma: no cover

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """文本向量化：返回与输入等长的向量列表。"""

    # ===== Function Calling 便捷方法 =====

    async def function_call(
        self,
        messages: list[Message],
        tools: list[ToolSchema],
        *,
        temperature: float | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Function Calling 便捷入口。

        复用 chat + tools 参数，OpenAI/GLM/Qwen 均支持此格式。
        模型决定是否调用工具，响应中 tool_calls 非空即表示调用。
        """
        return await self.chat(
            messages,
            tools=tools,
            temperature=temperature,
            **kwargs,
        )

    # ===== 辅助 =====

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        """判断异常是否可重试。

        可重试：超时、连接错误、限流（429）。
        不可重试：鉴权失败（401）、参数错误（400）、内容违规。
        """
        # openai 库的异常类名含 APITimeoutError / RateLimitError / APIConnectionError
        name = type(exc).__name__.lower()
        return any(k in name for k in ("timeout", "ratelimit", "connection", "retry"))

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} provider={self.provider} model={self.model}>"
