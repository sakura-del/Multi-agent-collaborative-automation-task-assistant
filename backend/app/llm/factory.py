"""LLM 工厂与热切换机制。

提供两项核心能力：
1. create_llm(provider, model, ...) 工厂函数：按 provider 创建对应适配器实例
2. LLMManager 管理器：运行时切换模型 + 自动降级（主模型失败→备用模型）

降级策略：
- 配置 fallback_chain，主模型抛异常后按顺序尝试备用模型
- 仅对可重试异常（超时/限流/连接错误）触发降级，不可恢复异常（鉴权/参数错误）直接抛出
- 降级决策记录日志，便于后续分析模型稳定性

设计原则：
- 工厂模式：上层不感知具体适配器类，只通过 provider 名创建
- 延迟实例化：适配器在首次调用时才创建 SDK 客户端
- 单例缓存：同一 (provider, model) 仅创建一次实例，避免重复初始化
"""

from __future__ import annotations

from typing import Any

from app.core.exceptions import LLMException
from app.core.logging import get_logger
from app.llm.adapters import GLMAdapter, OpenAIAdapter, QwenAdapter
from app.llm.base import BaseLLM
from app.llm.models import LLMResponse, Message, ToolSchema

logger = get_logger(__name__)

# 厂商 → 适配器类映射表，新增厂商只需在此注册
_ADAPTER_REGISTRY: dict[str, type[BaseLLM]] = {
    "openai": OpenAIAdapter,
    "glm": GLMAdapter,
    "qwen": QwenAdapter,
    # DeepSeek 使用 OpenAI 兼容协议，直接复用 OpenAIAdapter（base_url 指向 api.deepseek.com）
    "deepseek": OpenAIAdapter,
}

# 确定性适配器：仅用于离线演练 / 冒烟 / 回归（`LLM__DEFAULT_PROVIDER=deterministic`）。
# 延迟注册避免 llm 包导入链在模块初始化期引入额外依赖。
_DETERMINISTIC_PROVIDER = "deterministic"


def create_llm(
    provider: str,
    model: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout: int = 30,
    max_retries: int = 3,
    default_temperature: float = 0.7,
    default_max_tokens: int | None = None,
    **extra: Any,
) -> BaseLLM:
    """工厂函数：按 provider 创建对应适配器实例。

    参数：
        provider: 厂商标识（openai/glm/qwen）
        model: 模型名（如 gpt-4o-mini / glm-4-flash / qwen-turbo）
        api_key: API 密钥，未提供时由适配器从配置读取
        base_url: 自定义 API 端点
        extra: 透传给适配器的额外参数（如 embedding_model）

    异常：
        LLMException: provider 不在注册表中
    """
    adapter_cls = _ADAPTER_REGISTRY.get(provider)
    if adapter_cls is None and provider == _DETERMINISTIC_PROVIDER:
        # 惰性导入：只在显式选用确定性 provider 时才加载（生产默认路径无额外开销）
        from app.llm.deterministic import DeterministicLLM

        return DeterministicLLM(model=model or "deterministic")
    if adapter_cls is None:
        raise LLMException(
            f"不支持的 LLM provider: {provider}",
            details={
                "provider": provider,
                "supported": list(_ADAPTER_REGISTRY.keys()),
            },
        )
    return adapter_cls(
        model=model,
        api_key=api_key,
        base_url=base_url,
        timeout=timeout,
        max_retries=max_retries,
        default_temperature=default_temperature,
        default_max_tokens=default_max_tokens,
        **extra,
    )


def register_adapter(provider: str, adapter_cls: type[BaseLLM]) -> None:
    """注册自定义适配器，供扩展使用（如新增 Anthropic/Claude 适配器）。"""
    _ADAPTER_REGISTRY[provider] = adapter_cls
    logger.info("adapter_registered", provider=provider)


class LLMManager:
    """LLM 管理器：运行时模型切换 + 自动降级。

    用法：
        manager = LLMManager(settings.llm)
        manager.create_default(openai_api_key="sk-xxx", glm_api_key="xxx")
        response = await manager.chat(messages)  # 自动降级

    设计：
    - 主模型 + 降级链：主模型失败按顺序尝试备用模型
    - 实例缓存：同 key 复用实例，避免重复创建
    - 线程安全：实例创建使用 dict 操作（Python GIL 保证原子性），
      后续如需多线程可加 asyncio.Lock
    """

    def __init__(self, llm_config: Any | None = None) -> None:
        # 适配器实例缓存：(provider, model) → BaseLLM
        self._instances: dict[tuple[str, str], BaseLLM] = {}
        # 主模型标识
        self._primary: tuple[str, str] | None = None
        # 降级链：[(provider, model), ...]
        self._fallback_chain: list[tuple[str, str]] = []
        # 厂商 API Key 与 base_url（创建实例时注入）
        self._api_keys: dict[str, str | None] = {}
        self._base_urls: dict[str, str] = {}
        # 全局超时与重试
        self._timeout: int = 30
        self._max_retries: int = 3
        if llm_config is not None:
            self._apply_config(llm_config)

    def _apply_config(self, config: Any) -> None:
        """从 LLMConfig 读取默认参数。"""
        self._timeout = getattr(config, "request_timeout", 30)
        self._max_retries = getattr(config, "max_retries", 3)
        # 读取各厂商 API Key 与 base_url
        for provider in ("openai", "glm", "qwen", "deepseek"):
            self._api_keys[provider] = getattr(config, f"{provider}_api_key", None)
            self._base_urls[provider] = getattr(config, f"{provider}_base_url", "")
        # 设置主模型
        primary_provider = getattr(config, "default_provider", "openai")
        primary_model = getattr(config, "default_model", "gpt-4o-mini")
        self._primary = (primary_provider, primary_model)
        # 设置降级链
        self._fallback_chain = [
            (item.provider, item.model)
            for item in getattr(config, "fallback_chain", [])
            if (item.provider, item.model) != (primary_provider, primary_model)
        ]

    def create_default(
        self,
        *,
        openai_api_key: str | None = None,
        glm_api_key: str | None = None,
        qwen_api_key: str | None = None,
    ) -> None:
        """显式注入 API Key 并初始化主模型与降级链。

        优先使用参数传入的 Key，其次使用配置中的 Key。
        """
        # 合并 Key：参数优先于配置
        if openai_api_key is not None:
            self._api_keys["openai"] = openai_api_key
        if glm_api_key is not None:
            self._api_keys["glm"] = glm_api_key
        if qwen_api_key is not None:
            self._api_keys["qwen"] = qwen_api_key

        # 预创建主模型实例（延迟初始化的客户端仍按需创建）
        if self._primary is not None:
            self._get_or_create(*self._primary)

    def _get_or_create(self, provider: str, model: str) -> BaseLLM:
        """获取或创建适配器实例（缓存复用）。"""
        key = (provider, model)
        if key not in self._instances:
            self._instances[key] = create_llm(
                provider,
                model,
                api_key=self._api_keys.get(provider),
                base_url=self._base_urls.get(provider) or None,
                timeout=self._timeout,
                max_retries=self._max_retries,
            )
        return self._instances[key]

    def switch_primary(self, provider: str, model: str) -> None:
        """运行时切换主模型，无需重启服务。

        切换后降级链中移除新主模型，避免重复尝试。
        """
        self._primary = (provider, model)
        # 从降级链中移除新主模型（若存在）
        self._fallback_chain = [
            (p, m) for p, m in self._fallback_chain if (p, m) != (provider, model)
        ]
        logger.info("primary_switched", provider=provider, model=model)

    def get_primary(self) -> BaseLLM:
        """获取当前主模型实例。"""
        if self._primary is None:
            raise LLMException("未配置主模型，请先调用 create_default 或 switch_primary")
        return self._get_or_create(*self._primary)

    async def chat(
        self,
        messages: list[Message],
        *,
        tools: list[ToolSchema] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """聊天调用：主模型失败自动降级到备用模型。

        降级触发条件：
        - 主模型抛出可重试异常（超时/限流/连接错误）且重试耗尽
        - 不可恢复异常（鉴权失败等）直接抛出，不降级
        """
        if self._primary is None:
            raise LLMException("未配置主模型，请先调用 create_default")

        # 尝试顺序：主模型 → 降级链
        candidates: list[tuple[str, str]] = [self._primary, *self._fallback_chain]
        last_exc: Exception | None = None

        for idx, (provider, model) in enumerate(candidates):
            try:
                llm = self._get_or_create(provider, model)
                response = await llm.chat(
                    messages,
                    tools=tools,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs,
                )
                if idx > 0:
                    # 降级成功，记录日志供运维分析
                    logger.warning(
                        "llm_fallback_succeeded",
                        primary_provider=candidates[0][0],
                        primary_model=candidates[0][1],
                        fallback_provider=provider,
                        fallback_model=model,
                    )
                return response
            except Exception as exc:
                last_exc = exc
                # 不可恢复异常直接抛出，不降级（如 401 鉴权失败降级也无意义）
                if not BaseLLM._is_retryable(exc):
                    raise
                # 可恢复异常：记录后尝试下一个降级模型
                if idx < len(candidates) - 1:
                    logger.warning(
                        "llm_fallback_triggered",
                        failed_provider=provider,
                        failed_model=model,
                        next_provider=candidates[idx + 1][0],
                        next_model=candidates[idx + 1][1],
                        error=str(exc),
                    )

        # 所有候选模型均失败
        assert last_exc is not None
        raise LLMException(
            f"所有 LLM 模型均调用失败，最后错误: {last_exc}",
            details={
                "candidates": candidates,
                "last_error": str(last_exc),
            },
        ) from last_exc

    async def stream_chat(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ):
        """流式聊天：仅使用主模型，不降级（流式降级会导致输出中断体验差）。"""
        llm = self.get_primary()
        async for chunk in llm.stream_chat(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        ):
            yield chunk


# 全局单例（按需创建，测试时直接实例化 LLMManager 替代）
_global_manager: LLMManager | None = None


def get_llm_manager() -> LLMManager:
    """获取全局 LLM 管理器单例。

    首次调用时从 settings.llm 读取配置初始化。
    """
    global _global_manager
    if _global_manager is None:
        from app.core.config import get_settings

        _global_manager = LLMManager(get_settings().llm)
    return _global_manager
