"""CrossRef 元数据适配器（Issue #3 T1）。

只取元数据、不取全文（版权约束）。错误分类与 `arxiv.py` 保持一致：
- 超时 / 连接失败 / 5xx / 429 重试耗尽或 `Retry-After` 超阈值 → `UpstreamTemporaryError`
  （调用方可降级为「未核验」）；
- 非 404 的 4xx → `UpstreamError`（请求本身有问题，不得降级）；
- 404 → `None`（DOI 不存在）；
- 响应不是合法 JSON / 缺必需字段 → `ValidationException`。

429 处理遵循计划 §2.9 裁决 R3：`Retry-After` 超过 `MAX_RETRY_AFTER_SECONDS`（2s）时
**不在任务路径内等待**，直接视为该源不可用并抛 `UpstreamTemporaryError`；
只有 `Retry-After ≤ 2s` 才做短暂重试——核验是任务旁路能力，不能为等上游冷却挂住用户任务。

礼貌池约定：请求头 `User-Agent: {user_agent} (mailto:{mailto})`（mailto 为空时只发 user_agent）。
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from pydantic import HttpUrl
from pydantic import ValidationError as PydanticValidationError

from app.core.exceptions import UpstreamError, UpstreamTemporaryError, ValidationException
from app.core.logging import get_logger
from app.tools.literature.models import MetadataRecord

logger = get_logger(__name__)

DEFAULT_USER_AGENT = "maa-literature-review/0.1"
DEFAULT_TIMEOUT = httpx.Timeout(20.0)
MAX_RETRY_AFTER_SECONDS = 2.0
"""任务路径内的 `Retry-After` 等待硬上限（秒）：超过即视为该源不可用，不等待、不重试（§2.9 R3）。"""
_DEFAULT_RETRY_AFTER_SECONDS = 1.0
"""缺失/非法 `Retry-After` 时的默认等待时长，必须不大于上面的上限。"""
_MAX_TITLE_LENGTH = 1000
_DOI_PREFIXES = (
    "https://doi.org/",
    "http://doi.org/",
    "https://dx.doi.org/",
    "http://dx.doi.org/",
)


def normalize_doi(doi: str) -> str:
    """归一化 DOI：去首尾空白、转小写、去掉 `https://doi.org/` 等 URL 前缀。

    返回空字符串表示没有可用 DOI（调用方据此跳过请求）。
    """
    value = (doi or "").strip().lower()
    if value.startswith("doi:"):
        value = value[len("doi:") :].strip()
    for prefix in _DOI_PREFIXES:
        if value.startswith(prefix):
            value = value[len(prefix) :].strip()
            break
    return value


async def _wait(seconds: float) -> None:
    """重试等待入口：独立函数便于测试替换，保证测试不产生真实等待。"""
    await asyncio.sleep(seconds)


def _retry_after_seconds(response: httpx.Response) -> float:
    """解析 `Retry-After`（秒）；缺失/非法 → 默认等待；负值按 0 处理。

    这里只做解析，不做上限截断：是否等待由调用方按 R3 预算裁决。
    """
    raw = response.headers.get("retry-after", "").strip()
    try:
        seconds = float(raw)
    except ValueError:
        return _DEFAULT_RETRY_AFTER_SECONDS
    return max(0.0, seconds)


def _parse_json_object(response: httpx.Response, *, operation: str) -> dict[str, Any]:
    """解析 JSON 对象；非法 JSON 或顶层不是对象 → `ValidationException`。"""
    try:
        payload: Any = response.json()
    except ValueError as exc:
        raise ValidationException(f"CrossRef 响应不是合法 JSON（{operation}）") from exc
    if not isinstance(payload, dict):
        raise ValidationException(f"CrossRef 响应不是 JSON 对象（{operation}）")
    return payload


def _as_text(value: Any) -> str:
    """只接受字符串，其余类型视为空文本（上游字段类型不稳定时保持健壮）。"""
    return value if isinstance(value, str) else ""


def _first_text(value: Any) -> str | None:
    """CrossRef 的 title / container-title 是字符串列表：取第一个非空项并折叠空白。"""
    if isinstance(value, str):
        text = " ".join(value.split())
        return text or None
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and (text := " ".join(item.split())):
                return text
    return None


def _parse_authors(value: Any) -> list[str]:
    """解析 Crossref 作者：`given` + `family` 拼接；机构作者取 `name`。"""
    if not isinstance(value, list):
        return []
    authors: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        given = _as_text(item.get("given")).strip()
        family = _as_text(item.get("family")).strip()
        literal = _as_text(item.get("name")).strip()
        if given and family:
            authors.append(f"{given} {family}")
        elif family:
            authors.append(family)
        elif literal:
            authors.append(literal)
    return authors


def _extract_year(value: Any) -> int | None:
    """从 `issued.date-parts[0][0]` 提取年份；缺失或越界 → None。"""
    if not isinstance(value, dict):
        return None
    parts = value.get("date-parts")
    if not isinstance(parts, list) or not parts:
        return None
    first = parts[0]
    if not isinstance(first, list) or not first:
        return None
    year = first[0]
    if isinstance(year, bool) or not isinstance(year, int):
        return None
    return year if 1000 <= year <= 2999 else None


def _as_non_negative_int(value: Any) -> int | None:
    """非负整数（如被引次数），其余类型/负值 → None。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def _source_url(doi: str | None, raw_url: Any) -> HttpUrl | None:
    """优先用 DOI 规范链接，其次用响应里的 URL 字段；非法 URL → None。"""
    candidate = f"https://doi.org/{doi}" if doi else _as_text(raw_url).strip()
    if not candidate:
        return None
    try:
        return HttpUrl(candidate)
    except PydanticValidationError:
        return None


class CrossrefClient:
    """CrossRef REST API 适配器（可注入 httpx.AsyncClient 以便测试）。"""

    BASE_URL = "https://api.crossref.org"

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        mailto: str = "",
        user_agent: str = "maa-literature-review/0.1",
        timeout: httpx.Timeout | None = None,
        max_retries: int = 1,
    ) -> None:
        self._client = client
        self._owns_client = client is None
        self._mailto = mailto.strip()
        self._user_agent = user_agent.strip() or DEFAULT_USER_AGENT
        self._timeout = timeout or DEFAULT_TIMEOUT
        self._max_retries = max(0, max_retries)

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

    # ===== 对外接口 =====

    async def lookup_doi(self, doi: str) -> MetadataRecord | None:
        """GET /works/{doi}；404 → None；其余 4xx → UpstreamError。"""
        normalized = normalize_doi(doi)
        if not normalized:
            return None
        response = await self._get(f"/works/{normalized}", operation="lookup_doi")
        if response.status_code == 404:
            return None
        self._ensure_success(response, operation="lookup_doi")
        payload = _parse_json_object(response, operation="lookup_doi")
        message = payload.get("message")
        if not isinstance(message, dict):
            raise ValidationException("CrossRef 响应缺少 message 对象（lookup_doi）")
        return self._parse_work(message, fallback_doi=normalized)

    async def search_by_title(
        self, title: str, *, rows: int = 3, author: str | None = None
    ) -> list[MetadataRecord]:
        """GET /works?query.bibliographic=...；无命中 → []（不是异常）。"""
        params = {
            "query.bibliographic": title,
            "rows": str(rows),
        }
        if author:
            params["query.author"] = author
        response = await self._get(
            "/works",
            params=params,
            operation="search_by_title",
        )
        self._ensure_success(response, operation="search_by_title")
        payload = _parse_json_object(response, operation="search_by_title")
        message = payload.get("message")
        if not isinstance(message, dict):
            raise ValidationException("CrossRef 响应缺少 message 对象（search_by_title）")
        items = message.get("items")
        if not isinstance(items, list):
            raise ValidationException("CrossRef 检索响应缺少 items 列表")
        records: list[MetadataRecord] = []
        for item in items:
            if not isinstance(item, dict):
                raise ValidationException("CrossRef 检索结果条目必须是对象")
            records.append(self._parse_work(item))
        return records

    # ===== 内部实现 =====

    @property
    def _headers(self) -> dict[str, str]:
        agent = self._user_agent
        if self._mailto:
            agent = f"{self._user_agent} (mailto:{self._mailto})"
        return {"User-Agent": agent, "Accept": "application/json"}

    async def _get(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
        operation: str,
    ) -> httpx.Response:
        """发 GET 请求：429 按 `Retry-After` 有界重试（超阈值立即降级）；网络异常转临时错误。"""
        url = f"{self.BASE_URL}{path}"
        for attempt in range(self._max_retries + 1):
            try:
                response = await self.client.get(
                    url,
                    params=params,
                    headers=self._headers,
                    timeout=self._timeout,
                )
            except httpx.TimeoutException as exc:
                raise UpstreamTemporaryError(f"CrossRef 请求超时（{operation}）") from exc
            except httpx.TransportError as exc:
                raise UpstreamTemporaryError(f"CrossRef 连接失败（{operation}）") from exc

            if response.status_code != 429:
                return response
            delay = _retry_after_seconds(response)
            if delay > MAX_RETRY_AFTER_SECONDS:
                # R3：上游冷却时间超出任务路径预算 → 不等待、不重试，交给核验侧降级为 UNVERIFIED
                logger.warning(
                    "crossref_rate_limited_no_wait",
                    operation=operation,
                    retry_after=delay,
                    budget=MAX_RETRY_AFTER_SECONDS,
                )
                raise UpstreamTemporaryError(
                    f"CrossRef 返回 429 且 Retry-After={delay:g}s 超过任务路径上限 "
                    f"{MAX_RETRY_AFTER_SECONDS:g}s（{operation}）"
                )
            if attempt >= self._max_retries:
                raise UpstreamTemporaryError(
                    f"CrossRef 返回 429（{operation}，重试 {self._max_retries} 次后仍限流）"
                )
            logger.warning(
                "crossref_rate_limited",
                operation=operation,
                attempt=attempt + 1,
                retry_after=delay,
            )
            await _wait(delay)

        raise UpstreamTemporaryError(f"CrossRef 请求重试耗尽（{operation}）")

    @staticmethod
    def _ensure_success(response: httpx.Response, *, operation: str) -> None:
        """5xx → 临时错误；非 404 的 4xx → 不可重试的上游错误。"""
        if response.status_code >= 500:
            raise UpstreamTemporaryError(f"CrossRef 返回 {response.status_code}（{operation}）")
        if response.status_code >= 400:
            raise UpstreamError(f"CrossRef 返回 {response.status_code}（{operation}）")

    def _parse_work(
        self, work: dict[str, Any], *, fallback_doi: str | None = None
    ) -> MetadataRecord:
        """把一条 Crossref work 归一化为 `MetadataRecord`；缺标题 → ValidationException。"""
        title = _first_text(work.get("title"))
        if not title:
            raise ValidationException("CrossRef 记录缺少标题")
        doi = normalize_doi(_as_text(work.get("DOI"))) or fallback_doi
        try:
            return MetadataRecord(
                source="crossref",
                doi=doi or None,
                title=title[:_MAX_TITLE_LENGTH],
                authors=_parse_authors(work.get("author")),
                year=_extract_year(work.get("issued")),
                container_title=_first_text(work.get("container-title")),
                cited_by_count=_as_non_negative_int(work.get("is-referenced-by-count")),
                is_open_access=None,
                source_url=_source_url(doi, work.get("URL")),
            )
        except PydanticValidationError as exc:
            raise ValidationException("CrossRef 记录字段不符合 schema") from exc
