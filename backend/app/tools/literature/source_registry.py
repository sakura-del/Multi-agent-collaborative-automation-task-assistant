"""文献源注册表与多源聚合（Phase 1 P1-B / 计划 §1.2）。

- 统一源协议：`search()` / `aclose()`，输出既有 `LiteratureRecord`；
- `RequestThrottle` / `SearchCache` 是各源共用的「串行节流 + 短 TTL 缓存」原语
  （限流口径见路线图 §6：arXiv ≥3s、PubMed 3 req/s、OpenAlex polite pool、S2 免费档 ~1 req/s）；
- `MultiSourceLiteratureSource` 并发检索 → 去重（DOI → source_id → 归一化题名）→ 轮转合并 → 截断；
  **单源失败只记 warning 并继续**，只有全部源都失败时才上抛（临时故障优先抛 `UpstreamTemporaryError`，
  以便工作流回退离线清单）；
- `build_sources(names)` 按名字/别名构造源，未知名字 → `ValidationException`。
"""

from __future__ import annotations

import asyncio
import re
import time
import unicodedata
from collections.abc import Callable, Sequence
from typing import Any, Protocol, runtime_checkable

import httpx

from app.core.exceptions import UpstreamTemporaryError, ValidationException
from app.core.logging import get_logger
from app.tools.literature.models import LiteratureRecord, LiteratureSearch

logger = get_logger(__name__)

#: 默认文献源（计划 §1.2：任务未配置时默认 `arxiv` + `openalex`）
DEFAULT_SOURCE_NAMES: tuple[str, ...] = ("arxiv", "openalex")

#: arXiv 官方要求请求间隔 ≥3 秒（路线图 §6）
ARXIV_MIN_INTERVAL_SECONDS = 3.0
ARXIV_CACHE_TTL_SECONDS = 60.0

#: 名字 / 别名 → 归一化源名（`s2` 与两种写法都指向 Semantic Scholar）
SOURCE_ALIASES: dict[str, str] = {
    "arxiv": "arxiv",
    "openalex": "openalex",
    "semantic_scholar": "semantic_scholar",
    "semantic-scholar": "semantic_scholar",
    "s2": "semantic_scholar",
    "pubmed": "pubmed",
}

_DOI_URL_PATTERN = re.compile(r"^https?://(?:dx\.)?doi\.org/(?P<doi>.+)$", re.IGNORECASE)

ClockFn = Callable[[], float]
WaitFn = Callable[[float], Any]


@runtime_checkable
class LiteratureSource(Protocol):
    """文献检索源协议（`ArxivClient` 等适配器与多源聚合器都满足它）。"""

    async def search(self, search: LiteratureSearch) -> list[LiteratureRecord]: ...

    async def aclose(self) -> None: ...


async def _wait(seconds: float) -> None:
    """等待入口：独立函数便于测试替换，测试不产生真实 sleep。"""
    await asyncio.sleep(seconds)


def _now() -> float:
    """单调时钟入口：独立函数便于测试替换（节流与缓存 TTL 用它计时）。"""
    return time.monotonic()


class RequestThrottle:
    """串行节流：同一源的两次请求之间至少间隔 `min_interval_seconds`。

    用 `asyncio.Lock` 串行化，保证并发调用也不会同时打上游（「不得并发轰炸」）。
    """

    def __init__(
        self,
        min_interval_seconds: float,
        *,
        sleep: WaitFn | None = None,
        clock: ClockFn | None = None,
    ) -> None:
        self._min_interval = max(0.0, min_interval_seconds)
        self._sleep = sleep or _wait
        self._clock = clock or _now
        self._lock = asyncio.Lock()
        self._last_request_at: float | None = None

    async def wait(self) -> None:
        async with self._lock:
            now = self._clock()
            if self._last_request_at is not None:
                remaining = self._min_interval - (now - self._last_request_at)
                if remaining > 0:
                    await self._sleep(remaining)
            self._last_request_at = self._clock()


class SearchCache:
    """短 TTL 检索缓存：同一检索条件在 TTL 内不重复外呼。"""

    def __init__(self, ttl_seconds: float, *, clock: ClockFn | None = None) -> None:
        self._ttl = max(0.0, ttl_seconds)
        self._clock = clock or _now
        self._items: dict[str, tuple[float, list[LiteratureRecord]]] = {}

    def get(self, key: str) -> list[LiteratureRecord] | None:
        entry = self._items.get(key)
        if entry is None:
            return None
        expires_at, records = entry
        if self._clock() >= expires_at:
            self._items.pop(key, None)
            return None
        return list(records)

    def set(self, key: str, records: Sequence[LiteratureRecord]) -> None:
        self._items[key] = (self._clock() + self._ttl, list(records))


def search_cache_key(search: LiteratureSearch) -> str:
    """检索条件 → 缓存键（题名大小写/首尾空白不敏感，日期与条数参与键）。"""
    return "|".join(
        (
            search.query.strip().casefold(),
            search.date_from.isoformat() if search.date_from is not None else "",
            search.date_to.isoformat() if search.date_to is not None else "",
            str(search.max_results),
        )
    )


class MultiSourceLiteratureSource:
    """多源聚合检索：并发请求各源 → 去重 → 轮转合并 → 按 `max_results` 截断。"""

    def __init__(
        self,
        sources: Sequence[LiteratureSource],
        *,
        per_source_limit: int = 10,
    ) -> None:
        self._sources = list(sources)
        self._per_source_limit = max(1, per_source_limit)

    @property
    def sources(self) -> list[LiteratureSource]:
        return list(self._sources)

    async def search(self, search: LiteratureSearch) -> list[LiteratureRecord]:
        if not self._sources:
            return []

        limit = max(1, min(self._per_source_limit, search.max_results))
        per_source_search = search.model_copy(update={"max_results": limit})
        outcomes = await asyncio.gather(
            *(source.search(per_source_search) for source in self._sources),
            return_exceptions=True,
        )

        results_by_source: list[list[LiteratureRecord]] = []
        failures: list[Exception] = []
        for source, outcome in zip(self._sources, outcomes, strict=True):
            # gather(return_exceptions=True)：异常以返回值形式回来，逐源隔离
            if isinstance(outcome, BaseException):
                error = outcome if isinstance(outcome, Exception) else RuntimeError(str(outcome))
                failures.append(error)
                results_by_source.append([])
                logger.warning(
                    "literature_source_failed",
                    source=type(source).__name__,
                    error=str(error),
                    error_type=type(error).__name__,
                )
            else:
                results_by_source.append(list(outcome))

        merged = _merge_deduped(results_by_source)
        if merged:
            return merged[: search.max_results]

        # 只有全部源都失败才上抛；部分成功但 0 结果按「无命中」返回空列表
        if failures and len(failures) == len(self._sources):
            temporary = [
                failure for failure in failures if isinstance(failure, UpstreamTemporaryError)
            ]
            if temporary:
                raise UpstreamTemporaryError(
                    f"全部文献源检索失败（{len(failures)} 个）：{temporary[0]}"
                ) from temporary[0]
            raise failures[0]
        return []

    async def aclose(self) -> None:
        for source in self._sources:
            await source.aclose()


class _ArxivSource:
    """arXiv 源适配：包装既有 `ArxivClient`，补官方要求的 ≥3s 节流与短 TTL 缓存。"""

    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._arxiv: Any | None = None
        self._throttle = RequestThrottle(ARXIV_MIN_INTERVAL_SECONDS)
        self._cache = SearchCache(ARXIV_CACHE_TTL_SECONDS)

    async def search(self, search: LiteratureSearch) -> list[LiteratureRecord]:
        key = search_cache_key(search)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        await self._throttle.wait()
        records = await self._arxiv_client().search(search)
        self._cache.set(key, records)
        return list(records)

    async def aclose(self) -> None:
        if self._arxiv is not None:
            await self._arxiv.aclose()
            self._arxiv = None

    def _arxiv_client(self) -> Any:
        if self._arxiv is None:
            from app.tools.literature.arxiv import ArxivClient

            self._arxiv = ArxivClient(client=self._client)
        return self._arxiv


def build_sources(
    names: Sequence[str],
    *,
    client: httpx.AsyncClient | None = None,
    mailto: str = "",
    s2_api_key: str = "",
) -> list[LiteratureSource]:
    """按名字构造文献源（别名归一、重复名字去重）；未知名字 → `ValidationException`。"""
    sources: list[LiteratureSource] = []
    seen: set[str] = set()
    for raw_name in names:
        canonical = _canonical_source_name(raw_name)
        if canonical in seen:
            continue
        seen.add(canonical)
        if canonical == "arxiv":
            sources.append(_ArxivSource(client=client))
        elif canonical == "openalex":
            from app.tools.literature.openalex_search import OpenAlexSearchSource

            sources.append(OpenAlexSearchSource(client=client, mailto=mailto))
        elif canonical == "semantic_scholar":
            from app.tools.literature.semantic_scholar import SemanticScholarSource

            sources.append(SemanticScholarSource(client=client, api_key=s2_api_key))
        else:  # pubmed
            from app.tools.literature.pubmed import PubMedSource

            sources.append(PubMedSource(client=client, mailto=mailto))
    return sources


def build_multi_source(
    names: Sequence[str],
    *,
    per_source_limit: int = 10,
    client: httpx.AsyncClient | None = None,
    mailto: str = "",
    s2_api_key: str = "",
) -> MultiSourceLiteratureSource:
    """便捷构造：`build_sources(names)` + `MultiSourceLiteratureSource`。"""
    return MultiSourceLiteratureSource(
        build_sources(names, client=client, mailto=mailto, s2_api_key=s2_api_key),
        per_source_limit=per_source_limit,
    )


def _canonical_source_name(raw_name: str) -> str:
    name = str(raw_name).strip().casefold()
    canonical = SOURCE_ALIASES.get(name)
    if canonical is None:
        options = "、".join(sorted({value for value in SOURCE_ALIASES.values()}))
        raise ValidationException(f"未知文献源：{raw_name}（可选：{options}）")
    return canonical


def _merge_deduped(
    results_by_source: Sequence[Sequence[LiteratureRecord]],
) -> list[LiteratureRecord]:
    """轮转（round-robin）合并各源结果，并按 DOI → source_id → 归一化题名去重。"""
    seen_dois: set[str] = set()
    seen_source_ids: set[str] = set()
    seen_titles: set[str] = set()
    merged: list[LiteratureRecord] = []
    longest = max((len(records) for records in results_by_source), default=0)
    for position in range(longest):
        for records in results_by_source:
            if position >= len(records):
                continue
            record = records[position]
            doi = _record_doi(record)
            source_id = record.source_id.strip().casefold()
            title = _normalize_title(record.title)
            if (doi is not None and doi in seen_dois) or source_id in seen_source_ids:
                continue
            if title and title in seen_titles:
                continue
            if doi is not None:
                seen_dois.add(doi)
            seen_source_ids.add(source_id)
            if title:
                seen_titles.add(title)
            merged.append(record)
    return merged


def _record_doi(record: LiteratureRecord) -> str | None:
    """从 `source_url` 提取归一化 DOI（各源有 DOI 时使用 `https://doi.org/...` 规范链接）。"""
    match = _DOI_URL_PATTERN.match(str(record.source_url))
    if match is None:
        return None
    return match.group("doi").strip().strip("/").casefold() or None


def _normalize_title(title: str) -> str:
    """题名归一：NFKC + casefold + 折叠空白（跨源同名判重）。"""
    normalized = unicodedata.normalize("NFKC", title)
    return " ".join(normalized.split()).casefold()
