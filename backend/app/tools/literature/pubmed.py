"""PubMed 检索源（Phase 1 P1-B / 计划 §1.2）。

走 NCBI E-utilities 两段式检索：
1. `esearch.fcgi`（retmode=json）拿 PMID 列表；
2. `efetch.fcgi`（retmode=xml）拿文章元数据，再归一化为既有 `LiteratureRecord`。

限流口径（路线图 §6）：E-utilities 约 3 req/s → 内部串行节流 0.34s（esearch 与 efetch 同样生效），
并带短 TTL 缓存；配置 `mailto` 时附 `email`/`tool` 参数（NCBI 礼貌用法）。
错误语义与 arxiv 适配器一致：超时/连接失败/5xx/429 重试耗尽 → `UpstreamTemporaryError`；
非 404 4xx → `UpstreamError`；非法 JSON/XML / 缺必需字段 → `ValidationException`。
"""

from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime
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

#: E-utilities 约 3 req/s（路线图 §6）
PUBMED_MIN_INTERVAL_SECONDS = 0.34
PUBMED_CACHE_TTL_SECONDS = 60.0
MAX_RETRY_AFTER_SECONDS = 2.0
_DEFAULT_RETRY_AFTER_SECONDS = 1.0
_DEFAULT_TIMEOUT = httpx.Timeout(20.0)
_MAX_TITLE_LENGTH = 500
_MAX_SOURCE_ID_LENGTH = 64
_MAX_IDS = 50
_TOOL_NAME = "maa-literature-review"
_PUBMED_ARTICLE_URL = "https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
_PMC_PDF_URL = "https://www.ncbi.nlm.nih.gov/pmc/articles/{pmc}/pdf/"
_YEAR_PATTERN = re.compile(r"\b(1[0-9]{3}|20[0-9]{2})\b")
_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


async def _wait(seconds: float) -> None:
    """重试等待入口：独立函数便于测试替换，测试不产生真实 sleep。"""
    await asyncio.sleep(seconds)


class PubMedSource:
    """NCBI E-utilities 检索适配器（实现 `LiteratureSource` 协议）。"""

    ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    MIN_INTERVAL_SECONDS = PUBMED_MIN_INTERVAL_SECONDS
    CACHE_TTL_SECONDS = PUBMED_CACHE_TTL_SECONDS

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
        self._client = client
        self._owns_client = client is None
        self._mailto = mailto.strip()
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
        key = search_cache_key(search)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        pmids = await self._esearch(search)
        if not pmids:
            self._cache.set(key, [])
            return []
        xml_text = await self._efetch(pmids)
        records = [_parse_article(article) for article in _pubmed_articles(xml_text)]
        records = _filter_by_date_range(records, search)[: search.max_results]
        self._cache.set(key, records)
        return list(records)

    # ===== E-utilities 两段式请求 =====

    async def _esearch(self, search: LiteratureSearch) -> list[str]:
        params: dict[str, str] = {
            "db": "pubmed",
            "term": search.query,
            "retmode": "json",
            "retmax": str(min(search.max_results, _MAX_IDS)),
            "sort": "relevance",
        }
        if search.date_from is not None or search.date_to is not None:
            params["datetype"] = "pdat"
            if search.date_from is not None:
                params["mindate"] = search.date_from.strftime("%Y/%m/%d")
            if search.date_to is not None:
                params["maxdate"] = search.date_to.strftime("%Y/%m/%d")
        self._add_polite_params(params)

        response = await self._request(self.ESEARCH_URL, params, operation="esearch")
        try:
            payload: Any = response.json()
        except ValueError as exc:
            raise ValidationException("PubMed esearch 响应不是合法 JSON") from exc
        if not isinstance(payload, dict):
            raise ValidationException("PubMed esearch 响应不是 JSON 对象")
        result = payload.get("esearchresult")
        if not isinstance(result, dict):
            raise ValidationException("PubMed esearch 响应缺少 esearchresult")
        idlist = result.get("idlist")
        if not isinstance(idlist, list):
            raise ValidationException("PubMed esearch 响应缺少 idlist")
        pmids = [item.strip() for item in idlist if isinstance(item, str) and item.strip()]
        if len(pmids) != len(idlist):
            raise ValidationException("PubMed esearch 返回非法 PMID")
        return pmids

    async def _efetch(self, pmids: list[str]) -> str:
        params: dict[str, str] = {
            "db": "pubmed",
            "id": ",".join(pmids),
            "retmode": "xml",
        }
        self._add_polite_params(params)
        response = await self._request(self.EFETCH_URL, params, operation="efetch")
        return response.text

    def _add_polite_params(self, params: dict[str, str]) -> None:
        if self._mailto:
            params["email"] = self._mailto
            params["tool"] = _TOOL_NAME

    async def _request(
        self,
        url: str,
        params: dict[str, str],
        *,
        operation: str,
    ) -> httpx.Response:
        """节流后发 GET：429 按 `Retry-After` 有界重试（超阈值立即降级）。"""
        await self._throttle.wait()
        for attempt in range(self._max_retries + 1):
            try:
                response = await self.client.get(
                    url,
                    params=params,
                    headers={"Accept": "application/json, application/xml"},
                    timeout=self._timeout,
                )
            except httpx.TimeoutException as exc:
                raise UpstreamTemporaryError(f"PubMed 请求超时（{operation}）") from exc
            except httpx.TransportError as exc:
                raise UpstreamTemporaryError(f"PubMed 连接失败（{operation}）") from exc

            if response.status_code != 429:
                self._ensure_success(response, operation=operation)
                return response
            delay = _retry_after_seconds(response)
            if delay > MAX_RETRY_AFTER_SECONDS:
                # R3：上游冷却时间超出任务路径预算 → 不等待、不重试
                raise UpstreamTemporaryError(
                    f"PubMed 返回 429 且 Retry-After={delay:g}s 超过任务路径上限 "
                    f"{MAX_RETRY_AFTER_SECONDS:g}s（{operation}）"
                )
            if attempt >= self._max_retries:
                raise UpstreamTemporaryError(
                    f"PubMed 返回 429（{operation}，重试 {self._max_retries} 次后仍限流）"
                )
            logger.warning(
                "pubmed_rate_limited",
                operation=operation,
                attempt=attempt + 1,
                retry_after=delay,
            )
            await _wait(delay)

        raise UpstreamTemporaryError(f"PubMed 请求重试耗尽（{operation}）")

    @staticmethod
    def _ensure_success(response: httpx.Response, *, operation: str) -> None:
        if response.status_code >= 500:
            raise UpstreamTemporaryError(f"PubMed 返回 {response.status_code}（{operation}）")
        if response.status_code >= 400:
            raise UpstreamError(f"PubMed 返回 {response.status_code}（{operation}）")


# ===== XML 解析 =====


def _pubmed_articles(xml_text: str) -> list[ET.Element]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValidationException("PubMed efetch 响应不是合法 XML") from exc
    if root.find(".//ERROR") is not None:
        raise ValidationException("PubMed efetch 返回错误文档")
    return list(root.findall(".//PubmedArticle"))


def _parse_article(article: ET.Element) -> LiteratureRecord:
    """单篇 `PubmedArticle` → `LiteratureRecord`；缺必需字段 → ValidationException。"""
    citation = _find_child(article, "MedlineCitation")
    if citation is None:
        raise ValidationException("PubMed 条目缺少 MedlineCitation")
    pmid = _node_text(citation.find("PMID"))
    if not pmid:
        raise ValidationException("PubMed 条目缺少 PMID")

    article_node = _find_child(citation, "Article")
    if article_node is None:
        raise ValidationException("PubMed 条目缺少 Article")
    title = _element_text(article_node.find("ArticleTitle"))
    if not title:
        raise ValidationException("PubMed 条目缺少标题")
    published_at = _published_at(citation, article_node)

    doi = _normalize_doi(_article_id(article, "doi"))
    pmc_id = _article_id(article, "pmc")
    pubmed_url = _PUBMED_ARTICLE_URL.format(pmid=pmid)
    source_url = f"https://doi.org/{doi}" if doi else pubmed_url
    pdf_url = _PMC_PDF_URL.format(pmc=pmc_id) if pmc_id else source_url

    try:
        return LiteratureRecord(
            source_id=f"pubmed:{pmid}"[:_MAX_SOURCE_ID_LENGTH],
            title=title[:_MAX_TITLE_LENGTH],
            authors=_authors(article_node),
            abstract=_abstract(article_node),
            published_at=published_at,
            source_url=HttpUrl(source_url),
            pdf_url=HttpUrl(pdf_url),
            categories=_publication_types(article_node),
        )
    except PydanticValidationError as exc:
        raise ValidationException("PubMed 条目字段不符合 LiteratureRecord 契约") from exc


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find_child(element: ET.Element, name: str) -> ET.Element | None:
    for child in element:
        if _local_name(child.tag) == name:
            return child
    return None


def _element_text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return " ".join("".join(element.itertext()).split())


def _node_text(element: ET.Element | None) -> str:
    return _element_text(element)


def _node_int(element: ET.Element | None) -> int | None:
    text = _node_text(element)
    if not text.isdigit():
        return None
    return int(text)


def _month_number(element: ET.Element | None) -> int:
    text = _node_text(element).casefold()
    return _MONTHS.get(text[:3], 1)


def _safe_datetime(year: int, month: int, day: int) -> datetime:
    if not 1000 <= year <= 2999:
        raise ValidationException(f"PubMed 出版年份越界：{year}")
    month = min(max(month, 1), 12)
    day = min(max(day, 1), 28 if month == 2 else 30 if month in (4, 6, 9, 11) else 31)
    return datetime(year, month, day, tzinfo=UTC)


def _published_at(citation: ET.Element, article_node: ET.Element) -> datetime:
    """`PubDate`（Year/Month/Day 或 MedlineDate）→ `ArticleDate` → `DateCompleted/Revised`。"""
    pub_date = article_node.find("Journal/JournalIssue/PubDate")
    if pub_date is not None:
        year = _node_int(pub_date.find("Year"))
        if year is not None:
            return _safe_datetime(
                year,
                _month_number(pub_date.find("Month")),
                _node_int(pub_date.find("Day")) or 1,
            )
        medline_date = _node_text(pub_date.find("MedlineDate"))
        match = _YEAR_PATTERN.search(medline_date)
        if match is not None:
            return datetime(int(match.group(1)), 1, 1, tzinfo=UTC)

    article_date = article_node.find("ArticleDate")
    if article_date is not None:
        year = _node_int(article_date.find("Year"))
        if year is not None:
            return _safe_datetime(
                year,
                _month_number(article_date.find("Month")),
                _node_int(article_date.find("Day")) or 1,
            )

    for path in ("DateCompleted", "DateRevised"):
        date_node = citation.find(path)
        if date_node is None:
            continue
        year = _node_int(date_node.find("Year"))
        if year is not None:
            return _safe_datetime(
                year,
                _month_number(date_node.find("Month")),
                _node_int(date_node.find("Day")) or 1,
            )
    raise ValidationException("PubMed 条目缺少可解析的出版日期")


def _authors(article_node: ET.Element) -> list[str]:
    author_list = article_node.find("AuthorList")
    if author_list is None:
        return []
    authors: list[str] = []
    for author in author_list.findall("Author"):
        collective = _node_text(author.find("CollectiveName"))
        if collective:
            authors.append(collective)
            continue
        family = _node_text(author.find("LastName"))
        given = _node_text(author.find("ForeName")) or _node_text(author.find("Initials"))
        if family and given:
            authors.append(f"{given} {family}")
        elif family:
            authors.append(family)
    return authors


def _abstract(article_node: ET.Element) -> str:
    abstract_node = article_node.find("Abstract")
    if abstract_node is None:
        return ""
    texts = [_element_text(node) for node in abstract_node.findall("AbstractText")]
    return " ".join(text for text in texts if text)


def _publication_types(article_node: ET.Element) -> list[str]:
    types: list[str] = []
    for node in article_node.findall("PublicationTypeList/PublicationType"):
        text = _node_text(node)
        if text:
            types.append(text)
    return types[:10]


def _article_id(article: ET.Element, id_type: str) -> str:
    for node in article.findall("PubmedData/ArticleIdList/ArticleId"):
        if node.get("IdType") != id_type:
            continue
        value = _node_text(node)
        if value:
            return value
    return ""


def _normalize_doi(doi: str) -> str | None:
    value = (doi or "").strip().lower()
    for prefix in (
        "https://doi.org/",
        "http://doi.org/",
        "https://dx.doi.org/",
        "http://dx.doi.org/",
        "doi:",
    ):
        if value.startswith(prefix):
            value = value[len(prefix) :].strip()
            break
    return value or None


def _filter_by_date_range(
    records: list[LiteratureRecord], search: LiteratureSearch
) -> list[LiteratureRecord]:
    filtered: list[LiteratureRecord] = []
    for record in records:
        published: date = record.published_at.date()
        if search.date_from is not None and published < search.date_from:
            continue
        if search.date_to is not None and published > search.date_to:
            continue
        filtered.append(record)
    return filtered


def _retry_after_seconds(response: httpx.Response) -> float:
    raw = response.headers.get("retry-after", "").strip()
    try:
        seconds = float(raw)
    except ValueError:
        return _DEFAULT_RETRY_AFTER_SECONDS
    return max(0.0, seconds)
