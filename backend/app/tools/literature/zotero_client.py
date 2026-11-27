"""Zotero Web API 客户端（Phase B Task B3）。

只实现同步需要的两个接口：

- ``GET /{users|groups}/{library_id}/items``：拉取条目（普通条目 + 附件条目）；
- ``GET /{users|groups}/{library_id}/items/{item_key}/file``：流式下载附件。

## 为什么是 Web API 而不是本地 API

`scripts/zotero_export.py` 用的是 **Zotero Local API**（`http://localhost:23119`，
要求本机 Zotero 客户端运行且开启「允许其他应用连接」）。服务端场景无法依赖
用户本机客户端，因此必须走 **Zotero Web API v3**（`https://api.zotero.org`）：
用户在自己的 Zotero 账号里生成 API Key，服务端以该 Key 代表用户访问其库。

## 安全约定

- API Key 只出现在请求头，**不写日志、不进异常消息**；
- 附件先看 ``Content-Length``，再边读边计数，任一时刻超过 30MB 立即中止 ——
  避免把超大文件整个读进内存，也避免为恶意大文件无限占用带宽；
- 下载链接可能 302 到对象存储（`follow_redirects=True`），失败一律转成可读的
  :class:`ZoteroClientError`，不泄漏上游响应体。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

#: Zotero Web API 基地址（与 Local API 的 127.0.0.1:23119 不是同一个服务）。
DEFAULT_BASE_URL = "https://api.zotero.org"

#: 当前使用的 API 版本；必须显式声明，Zotero 默认只保证向后兼容少量旧版本。
API_VERSION = "3"

#: 单附件上限 30MB（与知识库上传限制对齐）。
MAX_ATTACHMENT_BYTES = 30 * 1024 * 1024

#: Web API 单页最多 100 条，超过会被忽略（这里也做夹紧）。
MAX_ITEMS_PER_PAGE = 100


class ZoteroClientError(Exception):
    """Zotero 上游调用失败（网络异常 / 非 2xx / 响应格式不合法）。"""


class ZoteroAuthError(ZoteroClientError):
    """API Key 无效或无权访问该库（401/403）。"""


class ZoteroAttachmentTooLarge(ZoteroClientError):
    """附件超过单文件上限（30MB）。"""


@dataclass(frozen=True)
class DownloadedAttachment:
    """下载完成的附件（调用方负责按上限使用内存）。"""

    item_key: str
    content: bytes
    content_type: str | None = None


class ZoteroClient:
    """Zotero Web API 的最小异步客户端。

    用法::

        async with ZoteroClient(api_key) as client:
            items = await client.fetch_items(
                library_type="user", library_id="1234567", limit=50
            )
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        key = (api_key or "").strip()
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={
                "Zotero-API-Version": API_VERSION,
                "Zotero-API-Key": key,
                "Accept": "application/json",
            },
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        )

    async def __aenter__(self) -> ZoteroClient:
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    # ===== 公开接口 =====

    async def fetch_items(
        self,
        *,
        library_type: str,
        library_id: str,
        collection_key: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """拉取条目列表（普通条目与附件条目混在一起，由调用方按 itemType 分类）。"""
        params: dict[str, str | int] = {
            "limit": max(1, min(int(limit), MAX_ITEMS_PER_PAGE)),
        }
        if collection_key:
            params["collection"] = str(collection_key).strip()

        response = await self._get(
            f"{self._library_path(library_type, library_id)}/items", params=params
        )
        payload = response.json()
        if not isinstance(payload, list):
            raise ZoteroClientError("Zotero 返回的条目列表格式不合法")
        return [item for item in payload if isinstance(item, dict)]

    async def download_attachment(
        self,
        *,
        library_type: str,
        library_id: str,
        item_key: str,
    ) -> DownloadedAttachment | None:
        """流式下载附件；文件不存在（404）返回 ``None``，超限抛对应异常。"""
        path = f"{self._library_path(library_type, library_id)}" f"/items/{item_key}/file"
        try:
            async with self._client.stream("GET", path) as response:
                if response.status_code == 404:
                    return None
                if response.status_code != 200:
                    self._raise_for_status(response.status_code)

                declared_length = response.headers.get("content-length")
                if declared_length is not None:
                    declared_size = self._parse_length(declared_length)
                    if declared_size is not None and declared_size > MAX_ATTACHMENT_BYTES:
                        raise ZoteroAttachmentTooLarge(self._too_large_message())

                buffer = bytearray()
                async for chunk in response.aiter_bytes():
                    buffer.extend(chunk)
                    if len(buffer) > MAX_ATTACHMENT_BYTES:
                        raise ZoteroAttachmentTooLarge(self._too_large_message())
                content_type = response.headers.get("content-type")
        except httpx.HTTPError as exc:
            raise ZoteroClientError("无法连接 Zotero 服务，请稍后重试") from exc

        return DownloadedAttachment(
            item_key=item_key,
            content=bytes(buffer),
            content_type=content_type,
        )

    # ===== 内部实现 =====

    @staticmethod
    def _library_path(library_type: str, library_id: str) -> str:
        """``user`` → ``/users/{id}``；``group`` → ``/groups/{id}``。"""
        segment = "groups" if str(library_type).strip().lower() == "group" else "users"
        return f"/{segment}/{str(library_id).strip()}"

    async def _get(self, path: str, *, params: dict[str, str | int]) -> httpx.Response:
        try:
            response = await self._client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise ZoteroClientError("无法连接 Zotero 服务，请稍后重试") from exc
        self._raise_for_status(response.status_code)
        return response

    @staticmethod
    def _raise_for_status(status_code: int) -> None:
        if status_code in (401, 403):
            raise ZoteroAuthError("Zotero API Key 无效或没有该库的访问权限")
        if status_code == 404:
            raise ZoteroClientError("Zotero 资源不存在（请检查用户 ID / collection_key）")
        if status_code == 429:
            raise ZoteroClientError("Zotero 请求过于频繁，请稍后重试")
        if status_code >= 400:
            raise ZoteroClientError(f"Zotero 上游返回 HTTP {status_code}")

    @staticmethod
    def _parse_length(raw: str) -> int | None:
        try:
            return int(raw.strip())
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _too_large_message() -> str:
        return f"Zotero 附件超过 {MAX_ATTACHMENT_BYTES // (1024 * 1024)}MB 上限，已跳过"
