"""arXiv Atom 检索适配器与受限 PDF 下载（Plan 3 Task 1）。

安全与稳定性约定：
- 只接受 HTTPS 且 host 属于 `arxiv.org` / `export.arxiv.org` 的下载地址；
- 手动跟随重定向（最多 3 跳），每跳都重新校验 host；
- 流式写入 `DocumentStorage`，由存储层执行 30MB 上限与 `%PDF-` 签名校验；
- 超时 / 连接失败 / 5xx → `UpstreamTemporaryError`（允许离线回退）；
  4xx / 非法响应 / 非法 URL → `UpstreamError` / `ValidationException`（不得回退）。
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from collections.abc import AsyncIterator
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
from pydantic import HttpUrl
from pydantic import ValidationError as PydanticValidationError

from app.core.exceptions import (
    UpstreamError,
    UpstreamTemporaryError,
    ValidationException,
)
from app.core.logging import get_logger
from app.knowledge.services.document_storage import DocumentStorage, StoredDocument
from app.tools.literature.models import LiteratureRecord, LiteratureSearch

logger = get_logger(__name__)

ATOM_NS = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"
_READ_CHUNK_SIZE = 1024 * 1024
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


def _normalize_text(value: str | None) -> str:
    """折叠空白：Atom 文本常含换行与连续空格。"""
    return " ".join((value or "").split())


class ArxivClient:
    """arXiv API 适配器（可注入 httpx.AsyncClient 以便测试）。"""

    BASE_URL = "https://export.arxiv.org/api/query"
    ALLOWED_DOWNLOAD_HOSTS = frozenset({"arxiv.org", "export.arxiv.org"})
    MAX_DOWNLOAD_BYTES = 30 * 1024 * 1024
    MAX_REDIRECTS = 3
    TIMEOUT = httpx.Timeout(connect=20.0, read=120.0, write=20.0, pool=20.0)

    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._owns_client = client is None

    @property
    def client(self) -> httpx.AsyncClient:
        """惰性创建内部客户端（注入的客户端由调用方负责关闭）。"""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.TIMEOUT, follow_redirects=False)
            self._owns_client = True
        return self._client

    async def aclose(self) -> None:
        """仅关闭内部创建的客户端。"""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    # ===== 检索 =====

    async def search(self, search: LiteratureSearch) -> list[LiteratureRecord]:
        """检索 arXiv 并按请求的日期区间本地过滤。"""
        params = {
            "search_query": f"all:{search.query}",
            "start": "0",
            "max_results": str(search.max_results),
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
        try:
            response = await self.client.get(self.BASE_URL, params=params, timeout=self.TIMEOUT)
        except httpx.TimeoutException as exc:
            raise UpstreamTemporaryError("arXiv 检索超时") from exc
        except httpx.TransportError as exc:
            raise UpstreamTemporaryError("arXiv 连接失败") from exc

        if response.status_code >= 500:
            raise UpstreamTemporaryError(f"arXiv 返回 {response.status_code}")
        if response.status_code >= 400:
            raise UpstreamError(f"arXiv 返回 {response.status_code}")

        records = self._parse_feed(response.text)
        return self._filter_dates(records, search)

    @staticmethod
    def _parse_feed(xml_text: str) -> list[LiteratureRecord]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            raise ValidationException("arXiv 响应不是合法 XML") from exc
        if not root.tag.endswith("feed"):
            raise ValidationException("arXiv 响应缺少 Atom feed")

        records: list[LiteratureRecord] = []
        for entry in root.findall(f"{ATOM_NS}entry"):
            records.append(ArxivClient._parse_entry(entry))
        return records

    @staticmethod
    def _parse_entry(entry: ET.Element) -> LiteratureRecord:
        raw_id = _normalize_text(entry.findtext(f"{ATOM_NS}id"))
        title = _normalize_text(entry.findtext(f"{ATOM_NS}title"))
        published_text = _normalize_text(entry.findtext(f"{ATOM_NS}published"))
        source_url: str | None = None
        pdf_url: str | None = None
        for link in entry.findall(f"{ATOM_NS}link"):
            href = link.get("href")
            if not href:
                continue
            if link.get("title") == "pdf" or link.get("type") == "application/pdf":
                pdf_url = pdf_url or href
            elif link.get("rel") == "alternate":
                source_url = source_url or href

        if not raw_id or not title or not published_text or not source_url or not pdf_url:
            raise ValidationException("arXiv 条目缺少必需字段")

        # 显式转换为契约类型：published_at 需要 datetime，链接字段需要 HttpUrl
        try:
            published = datetime.fromisoformat(published_text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValidationException("arXiv 条目的 published 时间格式非法") from exc

        source_id = raw_id.rsplit("/abs/", 1)[-1].strip() or raw_id.rsplit("/", 1)[-1]
        authors = [
            _normalize_text(name.text)
            for name in entry.findall(f"{ATOM_NS}author/{ATOM_NS}name")
            if _normalize_text(name.text)
        ]
        categories = [
            term
            for category in entry.findall(f"{ATOM_NS}category")
            if (term := (category.get("term") or "").strip())
        ]
        try:
            return LiteratureRecord(
                source_id=source_id,
                title=title,
                authors=authors,
                abstract=_normalize_text(entry.findtext(f"{ATOM_NS}summary")),
                published_at=published,
                source_url=HttpUrl(source_url),
                pdf_url=HttpUrl(pdf_url),
                categories=categories,
            )
        except PydanticValidationError as exc:
            raise ValidationException("arXiv 条目字段不符合 schema") from exc

    @staticmethod
    def _filter_dates(
        records: list[LiteratureRecord],
        search: LiteratureSearch,
    ) -> list[LiteratureRecord]:
        filtered = []
        for record in records:
            published: date = record.published_at.date()
            if search.date_from is not None and published < search.date_from:
                continue
            if search.date_to is not None and published > search.date_to:
                continue
            filtered.append(record)
        return filtered

    # ===== 下载 =====

    async def download_pdf(
        self,
        record: LiteratureRecord,
        storage: DocumentStorage,
        user_id: UUID,
    ) -> StoredDocument:
        """把 arXiv PDF 流式写入受控存储；每跳重定向都重新校验 host。"""
        url = str(record.pdf_url)
        for _hop in range(self.MAX_REDIRECTS + 1):
            self._ensure_allowed_download_url(url)
            try:
                async with self.client.stream(
                    "GET", url, follow_redirects=False, timeout=self.TIMEOUT
                ) as response:
                    if response.status_code in _REDIRECT_STATUSES:
                        location = response.headers.get("location")
                        if not location:
                            raise UpstreamError("arXiv 重定向缺少 Location")
                        url = str(httpx.URL(url).join(location))
                        continue
                    if response.status_code >= 500:
                        raise UpstreamTemporaryError(f"arXiv 下载返回 {response.status_code}")
                    if response.status_code >= 400:
                        raise UpstreamError(f"arXiv 下载返回 {response.status_code}")

                    declared = response.headers.get("content-length")
                    if declared is not None and declared.isdigit():
                        if int(declared) > self.MAX_DOWNLOAD_BYTES:
                            raise ValidationException("PDF 超过 30 MB 限制")

                    upload = _StreamingUpload(
                        f"{record.source_id}.pdf",
                        response.aiter_bytes(_READ_CHUNK_SIZE),
                    )
                    return await storage.save(user_id, upload)  # type: ignore[arg-type]
            except httpx.TimeoutException as exc:
                raise UpstreamTemporaryError("arXiv 下载超时") from exc
            except httpx.TransportError as exc:
                raise UpstreamTemporaryError("arXiv 下载连接失败") from exc

        raise ValidationException("arXiv 重定向次数超过上限")

    def _ensure_allowed_download_url(self, url: str) -> None:
        parsed = httpx.URL(url)
        if parsed.scheme != "https" or parsed.host not in self.ALLOWED_DOWNLOAD_HOSTS:
            raise ValidationException("仅允许下载 HTTPS arxiv.org 域名下的 PDF")


class _StreamingUpload:
    """把 httpx 字节流适配为 `DocumentStorage.save()` 需要的最小上传接口。"""

    def __init__(self, filename: str, chunks: AsyncIterator[bytes]) -> None:
        self.filename = filename
        self._iterator = chunks.__aiter__()
        self._buffer = b""
        self._exhausted = False

    async def read(self, size: int = -1) -> bytes:
        target = size if size and size > 0 else _READ_CHUNK_SIZE
        while len(self._buffer) < target and not self._exhausted:
            try:
                self._buffer += await self._iterator.__anext__()
            except StopAsyncIteration:
                self._exhausted = True
                break
        data, self._buffer = self._buffer[:target], self._buffer[target:]
        return data


# ===== 离线回退 =====


def load_offline_manifest(manifest_path: Path) -> list[LiteratureRecord]:
    """读取冻结离线清单；只校验文献字段，清单附加元数据（local_path/sha256）忽略。"""
    path = Path(manifest_path)
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationException("离线清单不可读或不是合法 JSON") from exc

    items = payload.get("records") if isinstance(payload, dict) else payload
    if not isinstance(items, list) or not items:
        raise ValidationException("离线清单缺少 records 列表")

    known_fields = set(LiteratureRecord.model_fields)
    records: list[LiteratureRecord] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValidationException("离线清单记录必须是对象")
        subset = {key: value for key, value in item.items() if key in known_fields}
        try:
            records.append(LiteratureRecord.model_validate(subset))
        except PydanticValidationError as exc:
            raise ValidationException("离线清单记录不符合文献 schema") from exc
    return records


async def search_with_offline_fallback(
    client: ArxivClient,
    search: LiteratureSearch,
    manifest_path: Path,
) -> list[LiteratureRecord]:
    """在线检索；仅 `UpstreamTemporaryError` 时回退到冻结清单。

    认证失败、非法查询、schema/格式错误都会直接上抛，绝不静默回退。
    """
    try:
        return await client.search(search)
    except UpstreamTemporaryError:
        logger.warning("arxiv_offline_fallback", query=search.query)
        return load_offline_manifest(manifest_path)
