"""OpenAlex 检索源（Phase 1 P1-B / 计划 §1.2）。

复用既有 `OpenAlexClient` 的 HTTP 层（超时/连接失败/5xx/429 错误分类、礼貌池 `mailto`），
只新增 `/works` 检索端点与 `LiteratureRecord` 归一化；内部做串行节流与短 TTL 缓存。

限流口径（路线图 §6）：OpenAlex 无硬性 req/s 限制，但要求礼貌池带 `mailto`；
这里取 0.15s 最小间隔（约 6–7 req/s）保守行事，并依靠缓存避免重复外呼。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import HttpUrl
from pydantic import ValidationError as PydanticValidationError

from app.core.exceptions import ValidationException
from app.tools.literature.models import LiteratureRecord, LiteratureSearch
from app.tools.literature.openalex import OpenAlexClient, normalize_doi
from app.tools.literature.source_registry import (
    RequestThrottle,
    SearchCache,
    search_cache_key,
)

#: OpenAlex polite pool 建议不要高频请求；0.15s 间隔在无 mailto 时也足够保守
OPENALEX_MIN_INTERVAL_SECONDS = 0.15
OPENALEX_CACHE_TTL_SECONDS = 60.0
_MAX_TITLE_LENGTH = 500
_MAX_SOURCE_ID_LENGTH = 64


class OpenAlexSearchSource(OpenAlexClient):
    """OpenAlex `/works` 检索适配器（继承现有客户端的 HTTP 层与错误语义）。"""

    MIN_INTERVAL_SECONDS = OPENALEX_MIN_INTERVAL_SECONDS
    CACHE_TTL_SECONDS = OPENALEX_CACHE_TTL_SECONDS

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        mailto: str = "",
        timeout: httpx.Timeout | None = None,
        max_retries: int = 1,
        throttle: RequestThrottle | None = None,
        cache: SearchCache | None = None,
    ) -> None:
        super().__init__(
            client=client,
            mailto=mailto,
            timeout=timeout,
            max_retries=max_retries,
        )
        self._throttle = throttle or RequestThrottle(self.MIN_INTERVAL_SECONDS)
        self._cache = cache or SearchCache(self.CACHE_TTL_SECONDS)

    async def search(self, search: LiteratureSearch) -> list[LiteratureRecord]:
        """检索 `/works`（full-text search），返回归一化后的 `LiteratureRecord` 列表。"""
        key = search_cache_key(search)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        await self._throttle.wait()
        response = await self._get(
            "/works",
            params=self._search_params(search),
            operation="search",
        )
        self._ensure_success(response, operation="search")
        payload = _parse_json_object(response)
        results = payload.get("results")
        if not isinstance(results, list):
            raise ValidationException("OpenAlex 检索响应缺少 results 列表")

        records = [self._parse_result(item) for item in results]
        records = _filter_by_date_range(records, search)[: search.max_results]
        self._cache.set(key, records)
        return list(records)

    # ===== 引文图谱遍历（Issue #32 Phase 2B：引文滚雪球）=====

    async def resolve_work(self, doi_or_title: str) -> tuple[str, LiteratureRecord] | None:
        """将种子论文（DOI 或标题）解析为 OpenAlex short work ID（如 W2741809807）与记录。"""
        raw = doi_or_title.strip()
        if not raw:
            return None

        doi = normalize_doi(raw)
        if doi and doi.startswith("10."):
            await self._throttle.wait()
            response = await self._get(
                f"/works/https://doi.org/{doi}",
                params=self._polite_params(),
                operation="resolve_work_doi",
            )
            if response.status_code == 404:
                return None
            self._ensure_success(response, operation="resolve_work_doi")
            payload = _parse_json_object(response)
            record = self._parse_result(payload)
            return _short_work_id(_text(payload.get("id"))), record

        await self._throttle.wait()
        params: dict[str, str] = {
            "search": raw,
            "per_page": "1",
            "sort": "relevance_score:desc",
        }
        if polite := self._polite_params():
            params.update(polite)
        response = await self._get(
            "/works",
            params=params,
            operation="resolve_work_title",
        )
        self._ensure_success(response, operation="resolve_work_title")
        payload = _parse_json_object(response)
        results = payload.get("results")
        if not isinstance(results, list) or not results:
            return None
        first = results[0]
        record = self._parse_result(first)
        return _short_work_id(_text(first.get("id"))), record

    async def fetch_references(
        self, work_id: str, *, max_results: int = 25
    ) -> list[LiteratureRecord]:
        """向后滚雪球：拉取指定论文的 `referenced_works` 并批量归一化为 LiteratureRecord。"""
        short_id = _short_work_id(work_id)
        if not short_id:
            return []
        limit = max(1, min(max_results, 50))
        cache_key = f"oa:refs:{short_id}:{limit}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        await self._throttle.wait()
        response = await self._get(
            f"/works/{short_id}",
            params=self._polite_params(),
            operation="fetch_work_references",
        )
        if response.status_code == 404:
            return []
        self._ensure_success(response, operation="fetch_work_references")
        payload = _parse_json_object(response)
        raw_refs = payload.get("referenced_works")
        if not isinstance(raw_refs, list) or not raw_refs:
            self._cache.set(cache_key, [])
            return []

        ref_ids = [
            rid for item in raw_refs if isinstance(item, str) and (rid := _short_work_id(item))
        ][:limit]
        if not ref_ids:
            self._cache.set(cache_key, [])
            return []

        await self._throttle.wait()
        params: dict[str, str] = {
            "filter": f"openalex:{'|'.join(ref_ids)}",
            "per_page": str(min(len(ref_ids), 50)),
        }
        if polite := self._polite_params():
            params.update(polite)
        batch_response = await self._get(
            "/works",
            params=params,
            operation="fetch_references_batch",
        )
        self._ensure_success(batch_response, operation="fetch_references_batch")
        batch_payload = _parse_json_object(batch_response)
        results = batch_payload.get("results")
        if not isinstance(results, list):
            return []

        records = self._parse_results_lenient(results)[:limit]
        self._cache.set(cache_key, records)
        return list(records)

    async def fetch_citing_works(
        self,
        work_id: str,
        *,
        max_results: int = 25,
        sort: str = "cited_by_count:desc",
    ) -> list[LiteratureRecord]:
        """向前滚雪球（反向引文检索）：调用 `/works?filter=cites:<work_id>` 拉取施引文献。"""
        short_id = _short_work_id(work_id)
        if not short_id:
            return []
        limit = max(1, min(max_results, 50))
        cache_key = f"oa:cites:{short_id}:{sort}:{limit}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        await self._throttle.wait()
        params: dict[str, str] = {
            "filter": f"cites:{short_id}",
            "sort": sort,
            "per_page": str(limit),
        }
        if polite := self._polite_params():
            params.update(polite)
        response = await self._get(
            "/works",
            params=params,
            operation="fetch_citing_works",
        )
        self._ensure_success(response, operation="fetch_citing_works")
        payload = _parse_json_object(response)
        results = payload.get("results")
        if not isinstance(results, list):
            return []

        records = self._parse_results_lenient(results)[:limit]
        self._cache.set(cache_key, records)
        return list(records)

    def _parse_results_lenient(self, items: list[Any]) -> list[LiteratureRecord]:
        """批量解析引文记录时跳过单条缺日期/残缺条目，避免单篇脏元数据打断整批滚雪球。"""
        parsed: list[LiteratureRecord] = []
        for item in items:
            try:
                parsed.append(self._parse_result(item))
            except ValidationException:
                continue
        return parsed

    # ===== 内部实现 =====

    def _search_params(self, search: LiteratureSearch) -> dict[str, str]:
        params: dict[str, str] = {
            "search": search.query,
            "per_page": str(min(search.max_results, 50)),
            # 窗口由 from_publication_date 过滤，排序始终按相关度：
            # 「最新优先」会把不相关的新论文排到最前（arXiv 侧实测过）。
            "sort": "relevance_score:desc",
        }
        filters: list[str] = []
        if search.date_from is not None:
            filters.append(f"from_publication_date:{search.date_from.isoformat()}")
        if search.date_to is not None:
            filters.append(f"to_publication_date:{search.date_to.isoformat()}")
        if filters:
            params["filter"] = ",".join(filters)
        polite = self._polite_params()
        if polite:
            params.update(polite)
        return params

    def _parse_result(self, item: Any) -> LiteratureRecord:
        """单条 OpenAlex work → `LiteratureRecord`；缺必需字段 → ValidationException。"""
        if not isinstance(item, dict):
            raise ValidationException("OpenAlex 检索结果条目必须是对象")

        work_id = _text(item.get("id"))
        if not work_id:
            raise ValidationException("OpenAlex 检索结果缺少 id")
        title = _text(item.get("title")) or _text(item.get("display_name"))
        if not title:
            raise ValidationException("OpenAlex 检索结果缺少标题")
        published_at = _published_at(item)

        doi = normalize_doi(_text(item.get("doi"))) or None
        location = item.get("primary_location")
        location = location if isinstance(location, dict) else {}
        best_oa = item.get("best_oa_location")
        best_oa = best_oa if isinstance(best_oa, dict) else {}

        landing_page = _text(location.get("landing_page_url"))
        source_url = f"https://doi.org/{doi}" if doi else landing_page or work_id
        pdf_url = _text(location.get("pdf_url")) or _text(best_oa.get("pdf_url")) or source_url

        try:
            return LiteratureRecord(
                source_id=_openalex_source_id(work_id),
                title=title[:_MAX_TITLE_LENGTH],
                authors=_authors(item.get("authorships")),
                abstract=_abstract(item.get("abstract_inverted_index")),
                published_at=published_at,
                source_url=HttpUrl(source_url),
                pdf_url=HttpUrl(pdf_url),
                categories=_categories(item.get("keywords")),
            )
        except PydanticValidationError as exc:
            raise ValidationException("OpenAlex 检索结果字段不符合 LiteratureRecord 契约") from exc


# ===== 归一化辅助 =====


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _short_work_id(work_id: str) -> str:
    """`https://openalex.org/W2741809807` / `openalex:W2741809807` → `W2741809807`。"""
    cleaned = work_id.strip().rstrip("/")
    if cleaned.lower().startswith("openalex:"):
        cleaned = cleaned.split(":", 1)[1].strip()
    if "/" in cleaned:
        cleaned = cleaned.rsplit("/", 1)[-1].strip()
    return cleaned


def _openalex_source_id(work_id: str) -> str:
    """`https://openalex.org/W2741809807` → `openalex:W2741809807`（≤64 字符）。"""
    suffix = _short_work_id(work_id) or work_id.strip()
    return f"openalex:{suffix}"[:_MAX_SOURCE_ID_LENGTH]


def _published_at(item: dict[str, Any]) -> datetime:
    """`publication_date`（YYYY-MM-DD）优先，缺失时用 `publication_year` 的 1 月 1 日。"""
    raw_date = _text(item.get("publication_date")).strip()
    if raw_date:
        try:
            return datetime.fromisoformat(raw_date).replace(tzinfo=UTC)
        except ValueError as exc:
            raise ValidationException(f"OpenAlex 出版日期非法：{raw_date}") from exc
    year = item.get("publication_year")
    if isinstance(year, int) and not isinstance(year, bool) and 1000 <= year <= 2999:
        return datetime(year, 1, 1, tzinfo=UTC)
    raise ValidationException("OpenAlex 检索结果缺少可解析的出版日期")


def _authors(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    authors: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        author = item.get("author")
        if not isinstance(author, dict):
            continue
        name = _text(author.get("display_name")).strip()
        if name:
            authors.append(name)
    return authors


def _abstract(value: Any) -> str:
    """OpenAlex 摘要以倒排索引返回：按 position 还原为文本。"""
    if not isinstance(value, dict):
        return ""
    positions: dict[int, str] = {}
    for word, indexes in value.items():
        if not isinstance(word, str) or not isinstance(indexes, list):
            continue
        for index in indexes:
            if isinstance(index, int) and not isinstance(index, bool) and index >= 0:
                positions[index] = word
    return " ".join(positions[index] for index in sorted(positions))


def _categories(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    categories: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = _text(item.get("display_name")).strip()
        if name:
            categories.append(name)
    return categories[:10]


def _filter_by_date_range(
    records: list[LiteratureRecord], search: LiteratureSearch
) -> list[LiteratureRecord]:
    """本地日期兜底过滤（与 arxiv 适配器一致，不依赖上游 filter 一定生效）。"""
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
        raise ValidationException("OpenAlex 检索响应不是合法 JSON") from exc
    if not isinstance(payload, dict):
        raise ValidationException("OpenAlex 检索响应不是 JSON 对象")
    return payload
