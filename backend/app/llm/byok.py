"""用户自带凭据的运行时解析（Issue #15 · BYOK P0-2）。

## 职责

把「数据库里的一条 `llm_credential`」变成「一个可调用的 LLM 客户端」：

1. 校验归属与状态（越权 404、停用 403）；
2. 解密 API Key（**只在内存里，绝不写日志**）；
3. 自定义端点做**运行时 DNS 复核**（创建期只能拦 IP 字面量）；
4. 按 `(凭据, key_version, model, base_url)` 缓存适配器，换 key 自动失效。

## 为什么这个客户端**没有降级链**

平台模型失败可以降级到别的平台模型，因为花的是平台的钱、且用户已同意；
BYOK 失败若静默回退到平台模型，等于**在用户不知情时替平台烧钱**，
同时让用户误以为一直在用自己的 key。所以这里刻意只包一个模型：
失败就抛，由任务层把可读原因告诉用户。用户想回退，必须显式开启（P1）。

## 缓存与失效

- 缓存的是**适配器实例**（内含 AsyncOpenAI 客户端），不是密文；
- 命中判据包含 `key_version`，换 key 后必然重建；
- `model` / `base_url` 也进 key，改名换模型不需要额外失效逻辑；
- TTL 兜底（默认 300s），避免长期持有已删除凭据的客户端。

## 残余风险

DNS 复核发生在**建客户端时**，`evil.com` 仍可在复核后改解析（DNS rebinding）。
P0 接受该窗口（缓存 5 分钟）；P1 改为连接时校验 IP（自定义 transport）。
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from openai import AuthenticationError, PermissionDeniedError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ForbiddenException, NotFoundException, ValidationException
from app.core.logging import get_logger
from app.db.models.llm_credential import LLMCredential
from app.llm.endpoint_guard import PinnedDNSAsyncHTTPTransport, resolve_public_ips
from app.llm.factory import create_llm
from app.security.credential_cipher import CredentialCipher
from app.security.llm_credential_service import LLMCredentialService

logger = get_logger(__name__)

#: 适配器缓存默认 TTL（秒）。
DEFAULT_CACHE_TTL_SECONDS = 300

#: 缓存条目上限（防大量凭据把内存撑爆）。
DEFAULT_MAX_CACHE_ENTRIES = 128


async def ensure_public_endpoint(base_url: str) -> None:
    """解析域名并拒绝任何内网/环回/链路本地地址（P0 的快速失败检查）。

    真正的防护在每个请求的 `PinnedDNSAsyncHTTPTransport` 里：它校验后**只连
    已校验的 IP**，消灭「检查完再改解析」的窗口。这里保留一次预检，是为了
    任务启动时就能给出可读错误，而不是等第一次 LLM 调用才失败。
    """
    host = (urlsplit(base_url).hostname or "").strip()
    if not host:
        raise ValidationException("自定义端点缺少主机名")
    await resolve_public_ips(host, allow_private=False)


class BYOKClient:
    """单个用户凭据的 LLM 客户端（**无降级链**）。

    形状与 `FallbackChatClient` 对齐（`chat` / `stream_chat` / `model` / `provider`），
    让 Agent 不需要知道自己拿到的是平台模型还是用户模型。
    """

    def __init__(
        self,
        *,
        credential_id: UUID,
        provider: str,
        model: str,
        base_url: str,
        llm: Any,
        on_auth_failure: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self.credential_id = credential_id
        self.provider = provider
        self.model = model
        self.base_url = base_url
        self._llm = llm
        self._on_auth_failure = on_auth_failure

    @property
    def is_byok(self) -> bool:
        return True

    async def chat(self, messages: Any, **kwargs: Any) -> Any:
        try:
            return await self._llm.chat(messages, **kwargs)
        except (AuthenticationError, PermissionDeniedError) as exc:
            # 401/403 才计数：超时/5xx 是上游抖动，不该把凭据打成无效。
            # 计数失败不能掩盖原始错误 —— 用户首先要看到的是「key 被拒」。
            if self._on_auth_failure is not None:
                try:
                    await self._on_auth_failure(f"{type(exc).__name__}: {exc}"[:500])
                except Exception:  # pragma: no cover - 记账失败不影响主流程
                    logger.warning(
                        "byok_failure_record_failed", credential_id=str(self.credential_id)
                    )
            raise

    def stream_chat(self, messages: Any, **kwargs: Any) -> Any:
        return self._llm.stream_chat(messages, **kwargs)

    def __repr__(self) -> str:
        return f"<BYOKClient provider={self.provider} model={self.model}>"


class CredentialLLMResolver:
    """按 `(user_id, credential_id)` 解析出可调用客户端，并做短期缓存。"""

    def __init__(
        self,
        *,
        cipher: CredentialCipher | None = None,
        ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
        max_entries: int = DEFAULT_MAX_CACHE_ENTRIES,
        session_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._cipher_impl = cipher
        self._ttl = max(1, int(ttl_seconds))
        self._max_entries = max(1, int(max_entries))
        # 失败计数要写库；可注入便于测试，默认用全局会话工厂
        self._session_factory = session_factory
        # key: (credential_id, key_version, model, base_url) → (expires_at, client)
        self._cache: OrderedDict[tuple[Any, ...], tuple[float, BYOKClient]] = OrderedDict()

    @property
    def cipher(self) -> CredentialCipher:
        if self._cipher_impl is None:
            self._cipher_impl = CredentialCipher.from_settings()
        return self._cipher_impl

    def clear_cache(self) -> None:
        self._cache.clear()

    async def _load(
        self, session: AsyncSession, user_id: UUID, credential_id: UUID
    ) -> LLMCredential:
        result = await session.execute(
            select(LLMCredential).where(
                LLMCredential.id == credential_id,
                LLMCredential.user_id == user_id,
            )
        )
        record = result.scalar_one_or_none()
        if record is None:
            # 与 HTTP 层同语义：不区分「不存在」与「不属于你」
            raise NotFoundException("凭据不存在")
        return record

    def _cached(self, cache_key: tuple[Any, ...]) -> BYOKClient | None:
        entry = self._cache.get(cache_key)
        if entry is None:
            return None
        expires_at, client = entry
        if expires_at <= time.monotonic():
            del self._cache[cache_key]
            return None
        self._cache.move_to_end(cache_key)
        return client

    def _store(self, cache_key: tuple[Any, ...], client: BYOKClient) -> None:
        self._cache[cache_key] = (time.monotonic() + self._ttl, client)
        self._cache.move_to_end(cache_key)
        while len(self._cache) > self._max_entries:
            self._cache.popitem(last=False)

    async def resolve(
        self, session: AsyncSession, user_id: UUID, credential_id: UUID
    ) -> BYOKClient:
        record = await self._load(session, user_id, credential_id)

        if record.status != "active":
            raise ForbiddenException("该模型凭据已停用，请在设置中启用或更换")

        cache_key = (record.id, record.key_version, record.model, record.base_url)
        cached = self._cached(cache_key)
        if cached is not None:
            return cached

        api_key = LLMCredentialService(cipher=self.cipher).decrypt_api_key(record)

        # 自定义端点：启动时先给可读错误；真正的防护在每次请求的 pin 传输层
        if record.provider == "custom" and not settings.llm.byok_allow_private_base_url:
            await ensure_public_endpoint(record.base_url)

        extra: dict[str, Any] = {}
        if record.provider == "custom":
            # DNS pinning：校验与连接用同一个 IP，堵住 rebinding 窗口
            extra["http_client"] = httpx.AsyncClient(
                transport=PinnedDNSAsyncHTTPTransport(
                    allow_private=settings.llm.byok_allow_private_base_url
                ),
                timeout=settings.llm.request_timeout,
            )
        llm = create_llm(
            record.provider,
            record.model,
            api_key=api_key,
            base_url=record.base_url,
            timeout=settings.llm.request_timeout,
            max_retries=settings.llm.max_retries,
            **extra,
        )

        async def _record_auth_failure(error: str) -> None:
            factory = self._session_factory
            if factory is None:
                from app.db.session import AsyncSessionLocal

                factory = AsyncSessionLocal
            async with factory() as failure_session:
                await LLMCredentialService().record_failure(
                    failure_session,
                    user_id=record.user_id,
                    credential_id=record.id,
                    error=error,
                )

        client = BYOKClient(
            credential_id=record.id,
            provider=record.provider,
            model=record.model,
            base_url=record.base_url,
            llm=llm,
            on_auth_failure=_record_auth_failure,
        )
        self._store(cache_key, client)
        logger.info(
            "byok_client_resolved",
            credential_id=str(record.id),
            user_id=str(user_id),
            provider=record.provider,
            model=record.model,
        )
        return client


#: 进程级默认解析器：任务执行层直接用，复用适配器缓存。
_default_resolver: CredentialLLMResolver | None = None


def get_credential_resolver() -> CredentialLLMResolver:
    """取默认解析器单例（首次调用时创建，密钥在首次 resolve 时才读）。"""
    global _default_resolver
    if _default_resolver is None:
        _default_resolver = CredentialLLMResolver()
    return _default_resolver
