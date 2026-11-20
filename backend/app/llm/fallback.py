"""BYOK → 平台模型的显式回退（Issue #28）。

## 为什么做成客户端包装而不是在调用点 if/else

任务执行链路里 LLM 调用散布在 planner/executor/validator/reviewer 多个角色，
没有一个统一的「调用失败」汇聚点；而回退必须**粘住**——第一次 401 之后
整条任务都改走平台，否则每个节点都会再撞一次坏 key（还各自触发凭据失败计数）。
把语义封进客户端包装，调用方完全无感。

## 触发口径（刻意收窄）

只有 **401 / 402 / 403**（key 无效、欠费、无权限）才回退：

- 429 限流**不回退**——限流是抖动，静默改走平台等于用户 key 一抖动就烧平台的钱；
- 超时 / 5xx 不回退——可能是上游抖动，重试机制会处理。

## 回退的副作用（由 `on_fallback` 回调负责，本模块不做）

- 任务标注 `actual_provider` / `actual_model`（否则界面还显示快照里的用户模型，
  用户以为仍在花自己的钱）；
- 平台侧计费：篇数照扣 + token 按回退时刻基线预扣（结算只退平台实际消耗）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

#: 视为「凭据不可用」的 HTTP 状态码。429（限流）与 5xx（上游抖动）刻意不在内。
CREDENTIAL_UNUSABLE_STATUSES = frozenset({401, 402, 403})


def is_credential_unusable(exc: BaseException) -> bool:
    """按异常携带的 `status_code` 判断是否属于凭据不可用。

    适配器不翻译异常（openai SDK 原样上抛，异常自带 `status_code`），
    所以这里也只认这个属性——比按异常类名判断更能覆盖不同厂商。
    """
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and status in CREDENTIAL_UNUSABLE_STATUSES


class PlatformFallbackClient:
    """包装 BYOK 客户端：凭据不可用时改走平台模型（粘住本次任务）。"""

    def __init__(
        self,
        inner: Any,
        *,
        platform: Callable[[], Any],
        on_fallback: Callable[[str], Awaitable[None]],
        enabled: bool = True,
    ) -> None:
        self._inner = inner
        self._platform_factory = platform
        self._on_fallback = on_fallback
        self._enabled = enabled
        self.fell_back = False

    # ---- 透传的元数据（日志用）----

    @property
    def model(self) -> str:
        return getattr(self._inner, "model", "(unknown)")

    @property
    def provider(self) -> str:
        return getattr(self._inner, "provider", "(unknown)")

    def _resolve_platform(self, reason: str) -> Any:
        """先确认平台可用再落标注：平台没配置时不能把任务标成「已回退」。"""
        client = self._platform_factory()
        if client is None:
            raise LLMUnavailableError(f"平台模型未配置，无法回退（{reason}）")
        return client

    def _should_fallback(self, exc: BaseException) -> bool:
        return self._enabled and not self.fell_back and is_credential_unusable(exc)

    async def _switch(self, exc: BaseException) -> Any:
        reason = f"凭据不可用（HTTP {getattr(exc, 'status_code', '?')}）"
        platform = self._resolve_platform(reason)
        await self._on_fallback(reason)
        self._inner = platform
        self.fell_back = True
        logger.warning(
            "byok_fallback_to_platform",
            provider=self.provider,
            model=self.model,
            reason=reason,
        )
        return self._inner

    # ---- 协议方法 ----

    async def chat(self, messages: Any, **kwargs: Any) -> Any:
        try:
            return await self._inner.chat(messages, **kwargs)
        except Exception as exc:
            if not self._should_fallback(exc):
                raise
            platform = await self._switch(exc)
            return await platform.chat(messages, **kwargs)

    async def stream_chat(self, messages: Any, **kwargs: Any) -> Any:
        """流式版本：首个事件前失败同样回退（适配器是惰性请求，错误在迭代时冒出）。"""
        stream = self._inner.stream_chat(messages, **kwargs)
        try:
            first = await stream.__anext__()
        except StopAsyncIteration:
            return
        except Exception as exc:
            if not self._should_fallback(exc):
                raise
            platform = await self._switch(exc)
            stream = platform.stream_chat(messages, **kwargs)
            first = await stream.__anext__()
        yield first
        async for event in stream:
            yield event

    def __getattr__(self, name: str) -> Any:
        # aclose 等生命周期方法透传给当前内部客户端
        return getattr(self._inner, name)


class LLMUnavailableError(RuntimeError):
    """平台模型也拿不到（未配置主模型）：回退链路到此为止，只能失败。"""
