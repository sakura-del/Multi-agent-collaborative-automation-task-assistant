"""OpenAlex 元数据适配器（Issue #3 T1）。

只取元数据、不取全文（版权约束）。错误分类与 `arxiv.py` 保持一致：
- 超时 / 连接失败 / 5xx / 429 重试耗尽或 `Retry-After` 超阈值 → `UpstreamTemporaryError`
  （调用方可降级为「未核验」）；
- 非 404 的 4xx → `UpstreamError`（请求本身有问题，不得降级）；
- 404 → `None`（DOI 不存在）；
- 响应不是合法 JSON / 缺必需字段 → `ValidationException`。

429 处理遵循计划 §2.9 裁决 R3：`Retry-After` 超过 `MAX_RETRY_AFTER_SECONDS`（2s）时
**不在任务路径内等待**，直接视为该源不可用并抛 `UpstreamTemporaryError`；
只有 `Retry-After ≤ 2s` 才做短暂重试——核验是任务旁路能力，不能为等上游冷却挂住用户任务。

礼貌池约定：`mailto` 同时进入请求头 `User-Agent: {user_agent} (mailto:{mailto})`
与查询参数 `mailto=...`（OpenAlex polite pool）。
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

    与 `crossref.normalize_doi` 语义完全一致；返回空字符串表示没有可用 DOI。
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
        raise ValidationException(f"OpenAlex 响应不是合法 JSON（{operation}）") from exc
    if not isinstance(payload, dict):
        raise ValidationException(f"OpenAlex 响应不是 JSON 对象（{operation}）")
    return payload


def _as_text(value: Any) -> str:
    """只接受字符串，其余类型视为空文本（上游字段类型不稳定时保持健壮）。"""
    return value if isinstance(value, str) else ""


def _parse_authors(value: Any) -> list[str]:
    """解析 `authorships[].author.display_name`。"""
    if not isinstance(value, list):
        return []
    authors: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        author = item.get("author")
        if not isinstance(author, dict):
            continue
        name = _as_text(author.get("display_name")).strip()
        if name:
            authors.append(name)
    return authors


def _as_year(value: Any) -> int | None:
    """`publication_year` 合法区间为 1000–2999，其余 → None。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 1000 <= value <= 2999 else None


def _as_non_negative_int(value: Any) -> int | None:
    """非负整数（如被引次数），其余类型/负值 → None。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def _container_title(value: Any) -> str | None:
    """期刊名取 `primary_location.source.display_name`；缺失 → None。"""
    if not isinstance(value, dict):
        return None
    source = value.get("source")
    if not isinstance(source, dict):
        return None
    name = _as_text(source.get("display_name")).strip()
    return name or None


def _is_open_access(value: Any) -> bool | None:
    """开放获取标记取 `open_access.is_oa`；缺失 → None（未知）。"""
    if not isinstance(value, dict):
        return None
    flag = value.get("is_oa")
    return flag if isinstance(flag, bool) else None


def _source_url(raw_id: Any, doi: str | None) -> HttpUrl | None:
    """优先用 OpenAlex work id（如 `https://openalex.org/W...`），其次 DOI 规范链接。"""
    candidate = _as_text(raw_id).strip() or (f"https://doi.org/{doi}" if doi else "")
    if not candidate:
        return None
    try:
        return HttpUrl(candidate)
    except PydanticValidationError:
        return None


class OpenAlexClient:
    """OpenAlex REST API 适配器（可注入 httpx.AsyncClient 以便测试）。"""

    BASE_URL = "https://api.openalex.org"

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
        """GET /works/https://doi.org/{doi}；404 → None。"""
        normalized = normalize_doi(doi)
        if not normalized:
            return None
        response = await self._get(
            f"/works/https://doi.org/{normalized}",
            params=self._polite_params(),
            operation="lookup_doi",
        )
        if response.status_code == 404:
            return None
        self._ensure_success(response, operation="lookup_doi")
        payload = _parse_json_object(response, operation="lookup_doi")
        return self._parse_work(payload, fallback_doi=normalized)

    async def search_by_title(self, title: str, *, per_page: int = 3) -> list[MetadataRecord]:
        """GET /works?search=...；无命中 → []（不是异常）。"""
        params = {
            "search": title,
            "per_page": str(per_page),
        }
        if self._mailto:
            params["mailto"] = self._mailto
        response = await self._get(
            "/works",
            params=params,
            operation="search_by_title",
        )
        self._ensure_success(response, operation="search_by_title")
        payload = _parse_json_object(response, operation="search_by_title")
        results = payload.get("results")
        if not isinstance(results, list):
            raise ValidationException("OpenAlex 检索响应缺少 results 列表")
        records: list[MetadataRecord] = []
        for item in results:
            if not isinstance(item, dict):
                raise ValidationException("OpenAlex 检索结果条目必须是对象")
            records.append(self._parse_work(item))
        return records

    # ===== 内部实现 =====

    @property
    def _headers(self) -> dict[str, str]:
        agent = self._user_agent
        if self._mailto:
            agent = f"{self._user_agent} (mailto:{self._mailto})"
        return {"User-Agent": agent, "Accept": "application/json"}

    def _polite_params(self) -> dict[str, str] | None:
        """polite pool 查询参数：mailto 为空时不下发。"""
        return {"mailto": self._mailto} if self._mailto else None

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
                raise UpstreamTemporaryError(f"OpenAlex 请求超时（{operation}）") from exc
            except httpx.TransportError as exc:
                raise UpstreamTemporaryError(f"OpenAlex 连接失败（{operation}）") from exc

            if response.status_code != 429:
                return response
            delay = _retry_after_seconds(response)
            if delay > MAX_RETRY_AFTER_SECONDS:
                # R3：上游冷却时间超出任务路径预算 → 不等待、不重试，交给核验侧降级为 UNVERIFIED
                logger.warning(
                    "openalex_rate_limited_no_wait",
                    operation=operation,
                    retry_after=delay,
                    budget=MAX_RETRY_AFTER_SECONDS,
                )
                raise UpstreamTemporaryError(
                    f"OpenAlex 返回 429 且 Retry-After={delay:g}s 超过任务路径上限 "
                    f"{MAX_RETRY_AFTER_SECONDS:g}s（{operation}）"
                )
            if attempt >= self._max_retries:
                raise UpstreamTemporaryError(
                    f"OpenAlex 返回 429（{operation}，重试 {self._max_retries} 次后仍限流）"
                )
            logger.warning(
                "openalex_rate_limited",
                operation=operation,
                attempt=attempt + 1,
                retry_after=delay,
            )
            await _wait(delay)

        raise UpstreamTemporaryError(f"OpenAlex 请求重试耗尽（{operation}）")

    @staticmethod
    def _ensure_success(response: httpx.Response, *, operation: str) -> None:
        """5xx → 临时错误；非 404 的 4xx → 不可重试的上游错误。"""
        if response.status_code >= 500:
            raise UpstreamTemporaryError(f"OpenAlex 返回 {response.status_code}（{operation}）")
        if response.status_code >= 400:
            raise UpstreamError(f"OpenAlex 返回 {response.status_code}（{operation}）")

    def _parse_work(
        self, work: dict[str, Any], *, fallback_doi: str | None = None
    ) -> MetadataRecord:
        """把一条 OpenAlex work 归一化为 `MetadataRecord`；缺标题 → ValidationException。"""
        title = _as_text(work.get("title")).strip() or _as_text(work.get("display_name")).strip()
        if not title:
            raise ValidationException("OpenAlex 记录缺少标题")
        doi = normalize_doi(_as_text(work.get("doi"))) or fallback_doi
        try:
            return MetadataRecord(
                source="openalex",
                doi=doi or None,
                title=title[:_MAX_TITLE_LENGTH],
                authors=_parse_authors(work.get("authorships")),
                year=_as_year(work.get("publication_year")),
                container_title=_container_title(work.get("primary_location")),
                cited_by_count=_as_non_negative_int(work.get("cited_by_count")),
                is_open_access=_is_open_access(work.get("open_access")),
                is_retracted=(
                    work.get("is_retracted")
                    if isinstance(work.get("is_retracted"), bool)
                    else None
                ),
                source_url=_source_url(work.get("id"), doi),
            )
        except PydanticValidationError as exc:
            raise ValidationException("OpenAlex 记录字段不符合 schema") from exc
