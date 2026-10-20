"""Semantic Scholar 检索源（Phase 1 P1-B / 计划 §1.2）。

端点：`GET https://api.semanticscholar.org/graph/v1/paper/search`。
免费档约 1 req/s（路线图 §6）→ 内部串行节流 + 短 TTL 缓存；配置了 API key 时带 `x-api-key` 头。
错误语义与 arxiv 适配器一致：超时/连接失败/5xx/429 重试耗尽 → `UpstreamTemporaryError`；
非 404 4xx → `UpstreamError`；非法 JSON / 缺必需字段 → `ValidationException`。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import HttpUrl
from pydantic import ValidationError as PydanticValidationError

from app.core.exceptions import UpstreamError, UpstreamTemporaryError, ValidationException
from app.core.logging import get_logger
from app.tools.literature.models import LiteratureRecord, LiteratureSearch
from app.tools.literature.source_registry import (
    RequestThrottle,
    SearchCache,
    search_cache_key,
)

logger = get_logger(__name__)

#: 免费档约 1 req/s（路线图 §6）
SEMANTIC_SCHOLAR_MIN_INTERVAL_SECONDS = 1.0
SEMANTIC_SCHOLAR_CACHE_TTL_SECONDS = 60.0
MAX_RETRY_AFTER_SECONDS = 2.0
_DEFAULT_RETRY_AFTER_SECONDS = 1.0
_DEFAULT_TIMEOUT = httpx.Timeout(20.0)
_MAX_TITLE_LENGTH = 500
_MAX_SOURCE_ID_LENGTH = 64
_MAX_LIMIT = 100
_DOI_PREFIXES = (
    "https://doi.org/",
    "http://doi.org/",
    "https://dx.doi.org/",
    "http://dx.doi.org/",
    "doi:",
)


async def _wait(seconds: float) -> None:
    """重试等待入口：独立函数便于测试替换，测试不产生真实 sleep。"""
    import asyncio

    await asyncio.sleep(seconds)


class SemanticScholarSource:
    """Semantic Scholar Graph API 检索适配器（实现 `LiteratureSource` 协议）。"""

    SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
    FIELDS = (
        "title,abstract,authors,year,publicationDate,externalIds,url,openAccessPdf,fieldsOfStudy"
    )
    MIN_INTERVAL_SECONDS = SEMANTIC_SCHOLAR_MIN_INTERVAL_SECONDS
    CACHE_TTL_SECONDS = SEMANTIC_SCHOLAR_CACHE_TTL_SECONDS

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        api_key: str = "",
        timeout: httpx.Timeout | None = None,
        max_retries: int = 1,
        throttle: RequestThrottle | None = None,
        cache: SearchCache | None = None,
    ) -> None:
        self._client = client
        self._owns_client = client is None
        self._api_key = api_key.strip()
        self._timeout = timeout or _DEFAULT_TIMEOUT
        self._max_retries = max(0, max_retries)
        self._throttle = throttle or RequestThrottle(self.MIN_INTERVAL_SECONDS)
        self._cache = cache or SearchCache(self.CACHE_TTL_SECONDS)

    @property
    def client(self) -> httpx.AsyncClient:
        """惰性创建内部客户端（注入的客户端由调用方负责关闭）。"""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout, follow_redirects=True)
            self._owns_client = True
        return self._client

    async def aclose(self) -> None:
        """仅关闭内部创建的客户端；注入的客户端由调用方管理。"""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def search(self, search: LiteratureSearch) -> list[LiteratureRecord]:
        if not search.query.strip():
            return []
        key = search_cache_key(search)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        await self._throttle.wait()
        response = await self._get(self._search_params(search))
        payload = _parse_json_object(response)
        data = payload.get("data")
        if not isinstance(data, list):
            raise ValidationException("Semantic Scholar 检索响应缺少 data 列表")

        records = [self._parse_paper(item) for item in data]
        records = _filter_by_date_range(records, search)[: search.max_results]
        self._cache.set(key, records)
        return list(records)

    # ===== 内部实现 =====

    @property
    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "User-Agent": "maa-literature-review/0.1"}
        if self._api_key:
            headers["x-api-key"] = self._api_key
        return headers

    def _search_params(self, search: LiteratureSearch) -> dict[str, str]:
        return {
            "query": search.query,
            "limit": str(min(search.max_results, _MAX_LIMIT)),
            "fields": self.FIELDS,
        }

    async def _get(self, params: dict[str, str]) -> httpx.Response:
        """发 GET：429 按 `Retry-After` 有界重试（超阈值立即降级）；网络异常转临时错误。"""
        for attempt in range(self._max_retries + 1):
            try:
                response = await self.client.get(
                    self.SEARCH_URL,
                    params=params,
                    headers=self._headers,
                    timeout=self._timeout,
                )
            except httpx.TimeoutException as exc:
                raise UpstreamTemporaryError("Semantic Scholar 请求超时（search）") from exc
            except httpx.TransportError as exc:
                raise UpstreamTemporaryError("Semantic Scholar 连接失败（search）") from exc

            if response.status_code != 429:
                self._ensure_success(response)
                return response
            delay = _retry_after_seconds(response)
            if delay > MAX_RETRY_AFTER_SECONDS:
                # R3：上游冷却时间超出任务路径预算 → 不等待、不重试
                raise UpstreamTemporaryError(
                    f"Semantic Scholar 返回 429 且 Retry-After={delay:g}s 超过任务路径上限 "
                    f"{MAX_RETRY_AFTER_SECONDS:g}s"
                )
            if attempt >= self._max_retries:
                raise UpstreamTemporaryError(
                    f"Semantic Scholar 返回 429（重试 {self._max_retries} 次后仍限流）"
                )
            logger.warning(
                "semantic_scholar_rate_limited",
                attempt=attempt + 1,
                retry_after=delay,
            )
            await _wait(delay)

        raise UpstreamTemporaryError("Semantic Scholar 请求重试耗尽")

    @staticmethod
    def _ensure_success(response: httpx.Response) -> None:
        if response.status_code >= 500:
            raise UpstreamTemporaryError(f"Semantic Scholar 返回 {response.status_code}")
        if response.status_code >= 400:
            raise UpstreamError(f"Semantic Scholar 返回 {response.status_code}")

    def _parse_paper(self, item: Any) -> LiteratureRecord:
        """单条 Graph API paper → `LiteratureRecord`；缺必需字段 → ValidationException。"""
        if not isinstance(item, dict):
            raise ValidationException("Semantic Scholar 检索结果条目必须是对象")

        paper_id = _text(item.get("paperId")).strip()
        if not paper_id:
            raise ValidationException("Semantic Scholar 检索结果缺少 paperId")
        title = _text(item.get("title")).strip()
        if not title:
            raise ValidationException("Semantic Scholar 检索结果缺少标题")
        published_at = _published_at(item)

        doi = _normalize_doi(_text(_external_ids(item).get("DOI")))
        paper_url = _text(item.get("url")).strip() or (
            f"https://www.semanticscholar.org/paper/{paper_id}"
        )
        source_url = f"https://doi.org/{doi}" if doi else paper_url
        open_access = item.get("openAccessPdf")
        pdf_url = ""
        if isinstance(open_access, dict):
            pdf_url = _text(open_access.get("url")).strip()

        try:
            return LiteratureRecord(
                source_id=f"s2:{paper_id}"[:_MAX_SOURCE_ID_LENGTH],
                title=title[:_MAX_TITLE_LENGTH],
                authors=_authors(item.get("authors")),
                abstract=_text(item.get("abstract")).strip(),
                published_at=published_at,
                source_url=HttpUrl(source_url),
                pdf_url=HttpUrl(pdf_url or source_url),
                categories=_categories(item.get("fieldsOfStudy")),
            )
        except PydanticValidationError as exc:
            raise ValidationException(
                "Semantic Scholar 检索结果字段不符合 LiteratureRecord 契约"
            ) from exc


# ===== 归一化辅助 =====


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _external_ids(item: dict[str, Any]) -> dict[str, Any]:
    value = item.get("externalIds")
    return value if isinstance(value, dict) else {}


def _normalize_doi(doi: str) -> str | None:
    value = (doi or "").strip().lower()
    for prefix in _DOI_PREFIXES:
        if value.startswith(prefix):
            value = value[len(prefix) :].strip()
            break
    return value or None


def _published_at(item: dict[str, Any]) -> datetime:
    raw_date = _text(item.get("publicationDate")).strip()
    if raw_date:
        try:
            return datetime.fromisoformat(raw_date).replace(tzinfo=UTC)
        except ValueError as exc:
            raise ValidationException(f"Semantic Scholar 出版日期非法：{raw_date}") from exc
    year = item.get("year")
    if isinstance(year, int) and not isinstance(year, bool) and 1000 <= year <= 2999:
        return datetime(year, 1, 1, tzinfo=UTC)
    raise ValidationException("Semantic Scholar 检索结果缺少可解析的出版日期")


def _authors(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    authors: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name")).strip()
        if name:
            authors.append(name)
    return authors


def _categories(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()][:10]


def _filter_by_date_range(
    records: list[LiteratureRecord], search: LiteratureSearch
) -> list[LiteratureRecord]:
    filtered: list[LiteratureRecord] = []
    for record in records:
        published = record.published_at.date()
        if search.date_from is not None and published < search.date_from:
            continue
        if search.date_to is not None and published > search.date_to:
            continue
        filtered.append(record)
    return filtered


def _parse_json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        payload: Any = response.json()
    except ValueError as exc:
        raise ValidationException("Semantic Scholar 检索响应不是合法 JSON") from exc
    if not isinstance(payload, dict):
        raise ValidationException("Semantic Scholar 检索响应不是 JSON 对象")
    return payload


def _retry_after_seconds(response: httpx.Response) -> float:
    raw = response.headers.get("retry-after", "").strip()
    try:
        seconds = float(raw)
    except ValueError:
        return _DEFAULT_RETRY_AFTER_SECONDS
    return max(0.0, seconds)
