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

import asyncio
import ipaddress
import socket
import time
from collections import OrderedDict
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    ForbiddenException,
    LLMException,
    NotFoundException,
    ValidationException,
)
from app.core.logging import get_logger
from app.db.models.llm_credential import LLMCredential
from app.llm.factory import create_llm
from app.security.credential_cipher import CredentialCipher
from app.security.llm_credential_service import LLMCredentialService

logger = get_logger(__name__)

#: DNS 复核超时（秒）。域名解析卡住不该把任务启动拖死。
_DNS_TIMEOUT_SECONDS = 5.0

#: 适配器缓存默认 TTL（秒）。
DEFAULT_CACHE_TTL_SECONDS = 300

#: 缓存条目上限（防大量凭据把内存撑爆）。
DEFAULT_MAX_CACHE_ENTRIES = 128


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any(
        (
            ip.is_private,
            ip.is_loopback,
            ip.is_link_local,
            ip.is_multicast,
            ip.is_reserved,
            ip.is_unspecified,
        )
    )


async def ensure_public_endpoint(base_url: str) -> None:
    """解析域名并拒绝任何内网/环回/链路本地地址。

    只在 `custom` 厂商上调用：预置厂商的 `base_url` 由服务端写死，
    不存在「用户把它指向内网」的路径。
    """
    host = (urlsplit(base_url).hostname or "").strip()
    if not host:
        raise ValidationException("自定义端点缺少主机名")

    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, None, type=socket.SOCK_STREAM),
            timeout=_DNS_TIMEOUT_SECONDS,
        )
    except (socket.gaierror, TimeoutError) as exc:
        raise LLMException(f"自定义端点的域名无法解析：{host}") from exc

    for info in infos:
        address = str(info[4][0]).split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:  # pragma: no cover - getaddrinfo 理论上只返回合法地址
            continue
        if _is_blocked_ip(ip):
            logger.warning("byok_endpoint_blocked", host=host, resolved=address)
            raise ValidationException("自定义端点解析到内网地址，已拒绝")


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
    ) -> None:
        self.credential_id = credential_id
        self.provider = provider
        self.model = model
        self.base_url = base_url
        self._llm = llm

    @property
    def is_byok(self) -> bool:
        return True

    async def chat(self, messages: Any, **kwargs: Any) -> Any:
        return await self._llm.chat(messages, **kwargs)

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
    ) -> None:
        self._cipher_impl = cipher
        self._ttl = max(1, int(ttl_seconds))
        self._max_entries = max(1, int(max_entries))
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

        # 自定义端点：创建期只拦了 IP 字面量，域名解析必须在这里再查一次
        if record.provider == "custom" and not settings.llm.byok_allow_private_base_url:
            await ensure_public_endpoint(record.base_url)

        llm = create_llm(
            record.provider,
            record.model,
            api_key=api_key,
            base_url=record.base_url,
            timeout=settings.llm.request_timeout,
            max_retries=settings.llm.max_retries,
        )
        client = BYOKClient(
            credential_id=record.id,
            provider=record.provider,
            model=record.model,
            base_url=record.base_url,
            llm=llm,
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
