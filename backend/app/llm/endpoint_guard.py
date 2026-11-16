"""BYOK 出站请求的 SSRF 防护与 DNS pinning（Issue #15 P1）。

## 为什么「创建期校验」不够

创建时只能检查 URL 字面量（`https://127.0.0.1`）。域名可以随时改解析：

```
https://relay.example.com  ->  第一次解析 203.0.113.9（通过）
                           ->  连接时解析 127.0.0.1（内网）
```

## 这里的做法：解析一次，然后**只连这个 IP**

`PinnedDNSAsyncHTTPTransport` 在每次请求时：

1. 解析 host 的全部 A/AAAA；
2. 任一地址是内网/环回/链路本地/保留地址 → 拒绝（除非 `allow_private`）；
3. 把 URL 的 host 换成**已校验的那个 IP**，保留原始 `Host` 头，
   并设置 `sni_hostname` 扩展（TLS 证书校验仍按域名）。

于是「校验的地址」和「连接的地址」是同一个，解析与连接之间没有窗口，
DNS rebinding 失效。代价是每次请求多一次解析（无缓存），换来确定性。

## 与 OpenAI SDK 的集成

`AsyncOpenAI(http_client=...)` 接受自定义 `httpx.AsyncClient`；适配器新增
`http_client` 参数把它透传进来。只有 `custom` 厂商需要 pin —— 预置厂商的
端点由服务端写死，不存在「用户把它指向内网」的路径。
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import Any

import httpx

from app.core.exceptions import LLMException, ValidationException

#: DNS 解析超时（秒）。
_DNS_TIMEOUT_SECONDS = 5.0


def _as_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """内网/环回/链路本地/保留/组播/未指定 —— 一律拒绝。"""
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


async def resolve_public_ips(host: str, *, allow_private: bool = False) -> list[str]:
    """解析 host 并校验全部地址；返回可用 IP 列表（字符串）。"""
    literal = _as_ip(host)
    if literal is not None:
        if is_blocked_ip(literal) and not allow_private:
            raise ValidationException("自定义端点不能指向内网、环回或链路本地地址")
        return [str(literal)]

    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, None, type=socket.SOCK_STREAM),
            timeout=_DNS_TIMEOUT_SECONDS,
        )
    except (socket.gaierror, TimeoutError) as exc:
        raise LLMException(f"自定义端点的域名无法解析：{host}") from exc

    addresses: list[str] = []
    for info in infos:
        address = str(info[4][0]).split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:  # pragma: no cover - getaddrinfo 理论上只返回合法地址
            continue
        if is_blocked_ip(ip) and not allow_private:
            raise ValidationException("自定义端点解析到内网地址，已拒绝")
        if address not in addresses:
            addresses.append(address)

    if not addresses:
        raise LLMException(f"自定义端点的域名没有可用地址：{host}")
    return addresses


def _pick_address(addresses: list[str]) -> str:
    """优先 IPv4：部分容器环境 IPv6 出口不可用，而 AAAA 常排在前面。"""
    for address in addresses:
        if ":" not in address:
            return address
    return addresses[0]


class PinnedDNSAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    """在连接前解析并固定 IP 的 httpx 传输层。"""

    def __init__(self, *, allow_private: bool = False, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._allow_private = allow_private

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if not host:  # pragma: no cover - httpx 不会构造无 host 的请求
            return await super().handle_async_request(request)

        addresses = await resolve_public_ips(host, allow_private=self._allow_private)
        pinned_host = _pick_address(addresses)

        # host 已经是 IP 且无需改写时，直接交给父类（避免无意义的 URL 重建）
        if pinned_host == host:
            return await super().handle_async_request(request)

        headers = request.headers.copy()
        # Host 头保留原始域名（含端口），否则虚拟主机/网关会 404
        if "host" not in headers:
            netloc = host if ":" not in host else f"[{host}]"
            default_port = 443 if request.url.scheme == "https" else 80
            if request.url.port and request.url.port != default_port:
                netloc = f"{netloc}:{request.url.port}"
            headers["host"] = netloc

        extensions = dict(request.extensions)
        if request.url.scheme == "https":
            # 证书校验与 SNI 仍按域名，连接走已校验的 IP
            extensions.setdefault("sni_hostname", host)

        pinned = httpx.Request(
            method=request.method,
            url=request.url.copy_with(host=pinned_host),
            headers=headers,
            stream=request.stream,
            extensions=extensions,
        )
        return await super().handle_async_request(pinned)
