"""PubMed 检索源测试（Phase 1 P1-B / 计划 §1.2）。

全部请求经 `httpx.MockTransport`，**不产生真实网络**。覆盖：
- E-utilities 两段式检索：`esearch.fcgi`（idlist）→ `efetch.fcgi`（XML）；
- 参数（db/term/retmax/sort/日期区间/礼貌池 email+tool）；
- XML 归一化为既有 `LiteratureRecord`（PMID/作者/摘要/日期/DOI URL/PMC PDF/文献类型）；
- 内部节流（3 req/s，esearch 与 efetch 之间也生效）与短 TTL 缓存；
- 错误语义与 arxiv 一致：超时/连接失败/5xx/429 耗尽 → `UpstreamTemporaryError`；
  非 404 4xx → `UpstreamError`；非法 JSON / 非法 XML / 缺必需字段 → `ValidationException`；
- 429 按 `Retry-After` 重试（超阈值快速降级）；
- 客户端所有权：注入的 client 不被 `aclose()` 关闭，自建 client 会被关闭。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from typing import Any

import httpx
import pytest

from app.core.exceptions import UpstreamError, UpstreamTemporaryError, ValidationException
from app.tools.literature import pubmed as pubmed_module
from app.tools.literature.models import LiteratureRecord, LiteratureSearch
from app.tools.literature.pubmed import PubMedSource
from app.tools.literature.source_registry import RequestThrottle, SearchCache

PMIDS = ["12345678", "23456789"]
ESEARCH_PAYLOAD: dict[str, Any] = {
    "header": {"type": "esearch", "version": "0.3"},
    "esearchresult": {"count": "2", "retmax": "2", "idlist": PMIDS},
}
EFETCH_XML = """<?xml version="1.0" encoding="utf-8"?>
<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation Status="MEDLINE">
      <PMID Version="1">12345678</PMID>
      <Article>
        <Journal>
          <JournalIssue>
            <PubDate><Year>2024</Year><Month>May</Month><Day>12</Day></PubDate>
          </JournalIssue>
          <Title>Journal of Evidence Engineering</Title>
        </Journal>
        <ArticleTitle>Evidence-Constrained Literature Review</ArticleTitle>
        <Abstract>
          <AbstractText>We study retrieval-augmented generation.</AbstractText>
          <AbstractText Label="METHODS">We evaluate it.</AbstractText>
        </Abstract>
        <AuthorList>
          <Author><LastName>Zhang</LastName><ForeName>Wei</ForeName></Author>
          <Author><CollectiveName>Evidence Review Consortium</CollectiveName></Author>
        </AuthorList>
        <PublicationTypeList>
          <PublicationType UI="D016428">Journal Article</PublicationType>
        </PublicationTypeList>
      </Article>
    </MedlineCitation>
    <PubmedData>
      <ArticleIdList>
        <ArticleId IdType="doi">10.1000/XYZ.123</ArticleId>
        <ArticleId IdType="pmc">PMC123456</ArticleId>
      </ArticleIdList>
    </PubmedData>
  </PubmedArticle>
  <PubmedArticle>
    <MedlineCitation Status="MEDLINE">
      <PMID Version="1">23456789</PMID>
      <Article>
        <Journal>
          <JournalIssue>
            <PubDate><MedlineDate>2023 Jan-Feb</MedlineDate></PubDate>
          </JournalIssue>
          <Title>Another Journal</Title>
        </Journal>
        <ArticleTitle>A Second Paper</ArticleTitle>
        <AuthorList>
          <Author><LastName>Li</LastName><ForeName>Mei</ForeName></Author>
        </AuthorList>
      </Article>
    </MedlineCitation>
  </PubmedArticle>
</PubmedArticleSet>
"""


class FakeClock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


def _search(query: str = "retrieval augmented generation", **overrides: Any) -> LiteratureSearch:
    data: dict[str, Any] = {"query": query, "max_results": 5}
    data.update(overrides)
    return LiteratureSearch(**data)


def _mock_client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=httpx.Timeout(5.0))


def _eutils_handler(
    *,
    esearch: dict[str, Any] | None = None,
    xml: str = EFETCH_XML,
    esearch_status: int = 200,
    efetch_status: int = 200,
) -> Any:
    payload = esearch if esearch is not None else ESEARCH_PAYLOAD

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/esearch.fcgi"):
            return httpx.Response(esearch_status, json=payload)
        if request.url.path.endswith("/efetch.fcgi"):
            return httpx.Response(efetch_status, text=xml)
        return httpx.Response(404, json={"error": "unknown endpoint"})

    return handler


def _sequence_handler(responses: list[httpx.Response]) -> Any:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return responses[min(len(calls) - 1, len(responses) - 1)]

    return handler


def _rate_limited_handler(retry_after: str) -> Any:
    return lambda request: httpx.Response(
        429,
        headers={"retry-after": retry_after},
        json={"error": "rate limited"},
    )


@asynccontextmanager
async def _source(
    handler: Any,
    *,
    throttle: RequestThrottle | None = None,
    cache: SearchCache | None = None,
    **kwargs: Any,
) -> AsyncIterator[tuple[PubMedSource, list[httpx.Request]]]:
    captured: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    client = _mock_client(recording)
    try:
        yield (
            PubMedSource(
                client=client,
                throttle=throttle or RequestThrottle(0.0),
                cache=cache or SearchCache(0.0),
                **kwargs,
            ),
            captured,
        )
    finally:
        await client.aclose()


class TestSearchFlow:
    async def test_esearch_then_efetch(self) -> None:
        async with _source(_eutils_handler(), mailto="contact@example.org") as (source, captured):
            records = await source.search(_search())

        assert len(captured) == 2
        esearch_request, efetch_request = captured
        assert esearch_request.url.host == "eutils.ncbi.nlm.nih.gov"
        assert esearch_request.url.path == "/entrez/eutils/esearch.fcgi"
        esearch_params = esearch_request.url.params
        assert esearch_params["db"] == "pubmed"
        assert esearch_params["term"] == "retrieval augmented generation"
        assert esearch_params["retmode"] == "json"
        assert esearch_params["retmax"] == "5"
        assert esearch_params["sort"] == "relevance"
        assert esearch_params["email"] == "contact@example.org"
        assert esearch_params["tool"] == "maa-literature-review"

        assert efetch_request.url.path == "/entrez/eutils/efetch.fcgi"
        efetch_params = efetch_request.url.params
        assert efetch_params["db"] == "pubmed"
        assert efetch_params["id"] == ",".join(PMIDS)
        assert efetch_params["retmode"] == "xml"

        assert len(records) == 2

    async def test_sends_date_range_to_esearch(self) -> None:
        async with _source(_eutils_handler()) as (source, captured):
            await source.search(_search(date_from=date(2024, 1, 1), date_to=date(2024, 12, 31)))

        params = captured[0].url.params
        assert params["datetype"] == "pdat"
        assert params["mindate"] == "2024/01/01"
        assert params["maxdate"] == "2024/12/31"

    async def test_omits_date_params_without_range(self) -> None:
        async with _source(_eutils_handler()) as (source, captured):
            await source.search(_search())
        assert captured[0].url.params.get("mindate") is None


class TestRecordMapping:
    async def test_maps_article_to_literature_record(self) -> None:
        async with _source(_eutils_handler()) as (source, _captured):
            records = await source.search(_search())

        first = records[0]
        assert isinstance(first, LiteratureRecord)
        assert first.source_id == "pubmed:12345678"
        assert first.title == "Evidence-Constrained Literature Review"
        assert first.authors == ["Wei Zhang", "Evidence Review Consortium"]
        assert first.abstract == "We study retrieval-augmented generation. We evaluate it."
        assert first.published_at == datetime(2024, 5, 12, tzinfo=UTC)
        assert str(first.source_url) == "https://doi.org/10.1000/xyz.123"
        # PMC 全文 PDF（无 PMC 时回退详情页）
        assert str(first.pdf_url) == "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC123456/pdf/"
        assert first.categories == ["Journal Article"]

    async def test_medline_date_and_pubmed_landing_fallback(self) -> None:
        async with _source(_eutils_handler()) as (source, _captured):
            records = await source.search(_search())

        second = records[1]
        assert second.source_id == "pubmed:23456789"
        assert second.published_at == datetime(2023, 1, 1, tzinfo=UTC)
        assert str(second.source_url) == "https://pubmed.ncbi.nlm.nih.gov/23456789/"
        assert str(second.pdf_url) == str(second.source_url)

    async def test_empty_idlist_returns_empty_list_without_efetch(self) -> None:
        payload = {"esearchresult": {"count": "0", "idlist": []}}
        async with _source(_eutils_handler(esearch=payload)) as (source, captured):
            assert await source.search(_search()) == []
        assert len(captured) == 1


class TestSchemaErrors:
    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"esearchresult": {}},
            {"esearchresult": {"idlist": "not-a-list"}},
            {"esearchresult": {"idlist": [123]}},
        ],
    )
    async def test_invalid_esearch_json_raises_validation_error(
        self, payload: dict[str, Any]
    ) -> None:
        async with _source(_eutils_handler(esearch=payload)) as (source, _captured):
            with pytest.raises(ValidationException):
                await source.search(_search())

    async def test_broken_json_raises_validation_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>not json</html>")

        async with _source(handler) as (source, _captured):
            with pytest.raises(ValidationException):
                await source.search(_search())

    async def test_broken_xml_raises_validation_error(self) -> None:
        async with _source(_eutils_handler(xml="<PubmedArticleSet><broken")) as (source, _cap):
            with pytest.raises(ValidationException):
                await source.search(_search())

    async def test_eutils_error_document_raises_validation_error(self) -> None:
        async with _source(
            _eutils_handler(
                xml='<?xml version="1.0"?><eFetchResult><ERROR>bad request</ERROR></eFetchResult>'
            )
        ) as (source, _captured):
            with pytest.raises(ValidationException):
                await source.search(_search())

    @pytest.mark.parametrize(
        "xml",
        [
            "<PubmedArticleSet><PubmedArticle/></PubmedArticleSet>",
            (
                "<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>1</PMID>"
                "<Article><ArticleTitle>No date</ArticleTitle></Article></MedlineCitation>"
                "</PubmedArticle></PubmedArticleSet>"
            ),
        ],
    )
    async def test_article_missing_required_fields_raises_validation_error(self, xml: str) -> None:
        async with _source(_eutils_handler(xml=xml)) as (source, _captured):
            with pytest.raises(ValidationException):
                await source.search(_search())


class TestErrorClassification:
    async def test_esearch_timeout_is_temporary_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("read timed out", request=request)

        async with _source(handler) as (source, _captured):
            with pytest.raises(UpstreamTemporaryError):
                await source.search(_search())

    async def test_efetch_5xx_is_temporary_error(self) -> None:
        async with _source(_eutils_handler(efetch_status=503)) as (source, _captured):
            with pytest.raises(UpstreamTemporaryError):
                await source.search(_search())

    @pytest.mark.parametrize("status", [500, 502])
    async def test_esearch_5xx_is_temporary_error(self, status: int) -> None:
        async with _source(_eutils_handler(esearch_status=status)) as (source, _captured):
            with pytest.raises(UpstreamTemporaryError):
                await source.search(_search())

    @pytest.mark.parametrize("status", [400, 403])
    async def test_esearch_4xx_is_upstream_error(self, status: int) -> None:
        async with _source(_eutils_handler(esearch_status=status)) as (source, _captured):
            with pytest.raises(UpstreamError):
                await source.search(_search())


class TestRateLimitRetry:
    async def test_retries_after_short_retry_after(self, monkeypatch: pytest.MonkeyPatch) -> None:
        waits: list[float] = []

        async def fake_wait(seconds: float) -> None:
            waits.append(seconds)

        monkeypatch.setattr(pubmed_module, "_wait", fake_wait)
        responses = [
            httpx.Response(429, headers={"retry-after": "1"}),
            httpx.Response(200, json=ESEARCH_PAYLOAD),
            httpx.Response(200, text=EFETCH_XML),
        ]

        async with _source(_sequence_handler(responses)) as (source, _captured):
            records = await source.search(_search())

        assert len(records) == 2
        assert waits == [1.0]

    async def test_retry_after_over_budget_fails_fast(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        waits: list[float] = []

        async def fake_wait(seconds: float) -> None:
            waits.append(seconds)

        monkeypatch.setattr(pubmed_module, "_wait", fake_wait)
        async with _source(_rate_limited_handler("60")) as (source, captured):
            with pytest.raises(UpstreamTemporaryError):
                await source.search(_search())

        assert waits == []
        assert len(captured) == 1


class TestThrottleAndCache:
    async def test_throttle_between_esearch_and_efetch(self) -> None:
        clock = FakeClock()
        waits: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            waits.append(seconds)

        throttle = RequestThrottle(0.34, sleep=fake_sleep, clock=clock)
        async with _source(_eutils_handler(), throttle=throttle, cache=SearchCache(0.0)) as (
            source,
            _captured,
        ):
            await source.search(_search())

        assert waits == [0.34]

    async def test_cache_hit_skips_all_http_requests(self) -> None:
        clock = FakeClock()
        async with _source(_eutils_handler(), cache=SearchCache(60.0, clock=clock)) as (
            source,
            captured,
        ):
            await source.search(_search("cached query"))
            await source.search(_search("cached query"))

        assert len(captured) == 2  # 仅首次的 esearch + efetch

    async def test_cache_expires_after_ttl(self) -> None:
        clock = FakeClock()
        async with _source(_eutils_handler(), cache=SearchCache(60.0, clock=clock)) as (
            source,
            captured,
        ):
            await source.search(_search("ttl query"))
            clock.value += 61.0
            await source.search(_search("ttl query"))

        assert len(captured) == 4


class TestClientLifecycle:
    async def test_injected_client_is_not_closed(self) -> None:
        client = _mock_client(_eutils_handler())
        source = PubMedSource(client=client)
        try:
            await source.aclose()
            assert client.is_closed is False
        finally:
            await client.aclose()

    async def test_owned_client_is_closed(self) -> None:
        source = PubMedSource()
        owned = source.client
        assert owned.is_closed is False
        await source.aclose()
        assert owned.is_closed is True
