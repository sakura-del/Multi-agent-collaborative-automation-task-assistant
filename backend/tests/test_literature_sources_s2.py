"""Semantic Scholar 检索源测试（Phase 1 P1-B / 计划 §1.2）。

全部请求经 `httpx.MockTransport`，**不产生真实网络**。覆盖：
- Graph API `/paper/search` 参数（query/limit/fields）与可选 `x-api-key` 头；
- 结果归一化为既有 `LiteratureRecord`（source_id/作者/摘要/日期/DOI URL/开放获取 PDF/领域分类）；
- 内部节流（免费档约 1 req/s）与短 TTL 缓存；
- 错误语义与 arxiv 一致：超时/连接失败/5xx/429 耗尽 → `UpstreamTemporaryError`；
  非 404 4xx → `UpstreamError`；非法 JSON / 缺必需字段 → `ValidationException`；
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
from app.tools.literature import semantic_scholar as s2_module
from app.tools.literature.models import LiteratureRecord, LiteratureSearch
from app.tools.literature.semantic_scholar import SemanticScholarSource
from app.tools.literature.source_registry import RequestThrottle, SearchCache

PAPER_ID = "204e3073870fae3d05bcbc2f6a8e263d9b72e776"


class FakeClock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


def _paper(**overrides: Any) -> dict[str, Any]:
    paper: dict[str, Any] = {
        "paperId": PAPER_ID,
        "externalIds": {"DOI": "10.1000/XYZ.123", "ArXiv": "2401.12345"},
        "title": "Evidence-Constrained Literature Review",
        "abstract": "We study retrieval-augmented generation.",
        "year": 2024,
        "publicationDate": "2024-05-12",
        "authors": [
            {"authorId": "1", "name": "Wei Zhang"},
            {"authorId": "2", "name": "Mei Li"},
        ],
        "url": f"https://www.semanticscholar.org/paper/{PAPER_ID}",
        "openAccessPdf": {"url": "https://example.org/paper.pdf"},
        "venue": "PeerJ",
        "fieldsOfStudy": ["Computer Science"],
    }
    paper.update(overrides)
    return paper


def _payload(*papers: dict[str, Any]) -> dict[str, Any]:
    return {"total": len(papers), "offset": 0, "data": list(papers)}


def _search(query: str = "retrieval augmented generation", **overrides: Any) -> LiteratureSearch:
    data: dict[str, Any] = {"query": query, "max_results": 5}
    data.update(overrides)
    return LiteratureSearch(**data)


def _mock_client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=httpx.Timeout(5.0))


@asynccontextmanager
async def _source(
    handler: Any,
    *,
    throttle: RequestThrottle | None = None,
    cache: SearchCache | None = None,
    **kwargs: Any,
) -> AsyncIterator[tuple[SemanticScholarSource, list[httpx.Request]]]:
    captured: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    client = _mock_client(recording)
    try:
        yield (
            SemanticScholarSource(
                client=client,
                throttle=throttle or RequestThrottle(0.0),
                cache=cache or SearchCache(0.0),
                **kwargs,
            ),
            captured,
        )
    finally:
        await client.aclose()


def _json_handler(payload: dict[str, Any]) -> Any:
    return lambda request: httpx.Response(200, json=payload)


def _status_handler(status: int) -> Any:
    return lambda request: httpx.Response(status, json={"error": "upstream"})


def _retry_after_handler(retry_after: str) -> Any:
    return lambda request: httpx.Response(
        429,
        headers={"retry-after": retry_after},
        json={"error": "rate limited"},
    )


class TestSearchParameters:
    async def test_sends_query_limit_and_fields(self) -> None:
        async with _source(_json_handler(_payload(_paper()))) as (source, captured):
            await source.search(_search())

        assert len(captured) == 1
        request = captured[0]
        assert request.url.host == "api.semanticscholar.org"
        assert request.url.path == "/graph/v1/paper/search"
        params = request.url.params
        assert params["query"] == "retrieval augmented generation"
        assert params["limit"] == "5"
        assert "title" in params["fields"]
        assert "externalIds" in params["fields"]
        assert request.headers.get("x-api-key") is None

    async def test_sends_api_key_header_when_configured(self) -> None:
        async with _source(_json_handler(_payload(_paper())), api_key="s2-secret") as (
            source,
            captured,
        ):
            await source.search(_search())
        assert captured[0].headers["x-api-key"] == "s2-secret"


class TestRecordMapping:
    async def test_maps_paper_to_literature_record(self) -> None:
        async with _source(_json_handler(_payload(_paper()))) as (source, _captured):
            records = await source.search(_search())

        assert len(records) == 1
        record = records[0]
        assert isinstance(record, LiteratureRecord)
        assert record.source_id == f"s2:{PAPER_ID}"
        assert record.title == "Evidence-Constrained Literature Review"
        assert record.authors == ["Wei Zhang", "Mei Li"]
        assert record.abstract == "We study retrieval-augmented generation."
        assert record.published_at == datetime(2024, 5, 12, tzinfo=UTC)
        assert str(record.source_url) == "https://doi.org/10.1000/xyz.123"
        assert str(record.pdf_url) == "https://example.org/paper.pdf"
        assert record.categories == ["Computer Science"]

    async def test_falls_back_to_s2_url_and_year(self) -> None:
        paper = _paper(externalIds={}, publicationDate=None, year=2023, openAccessPdf=None)
        async with _source(_json_handler(_payload(paper))) as (source, _captured):
            records = await source.search(_search())
        record = records[0]
        assert str(record.source_url) == f"https://www.semanticscholar.org/paper/{PAPER_ID}"
        assert str(record.pdf_url) == str(record.source_url)
        assert record.published_at == datetime(2023, 1, 1, tzinfo=UTC)

    async def test_filters_by_date_range_locally(self) -> None:
        older = _paper(paperId="old", externalIds={}, publicationDate="2020-01-01")
        newer = _paper(paperId="new", externalIds={}, publicationDate="2024-06-01")
        async with _source(_json_handler(_payload(older, newer))) as (source, _captured):
            records = await source.search(_search(date_from=date(2024, 1, 1)))
        assert [record.source_id for record in records] == ["s2:new"]

    async def test_empty_results_return_empty_list(self) -> None:
        async with _source(_json_handler({"total": 0, "data": []})) as (source, captured):
            assert await source.search(_search()) == []
        assert len(captured) == 1


class TestSchemaErrors:
    @pytest.mark.parametrize(
        "payload",
        [
            {"total": 0},
            {"data": "not-a-list"},
            {"data": ["not-a-dict"]},
            {"data": [{"title": "missing paperId"}]},
            {"data": [{"paperId": "x", "title": ""}]},
            {"data": [{"paperId": "x", "title": "no date"}]},
        ],
    )
    async def test_invalid_schema_raises_validation_error(self, payload: dict[str, Any]) -> None:
        async with _source(_json_handler(payload)) as (source, _captured):
            with pytest.raises(ValidationException):
                await source.search(_search())

    async def test_broken_json_raises_validation_error(self) -> None:
        async with _source(lambda request: httpx.Response(200, text="<html>not json</html>")) as (
            source,
            _captured,
        ):
            with pytest.raises(ValidationException):
                await source.search(_search())


class TestErrorClassification:
    async def test_timeout_is_temporary_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("read timed out", request=request)

        async with _source(handler) as (source, _captured):
            with pytest.raises(UpstreamTemporaryError):
                await source.search(_search())

    async def test_connect_failure_is_temporary_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        async with _source(handler) as (source, _captured):
            with pytest.raises(UpstreamTemporaryError):
                await source.search(_search())

    @pytest.mark.parametrize("status", [500, 502, 503])
    async def test_5xx_is_temporary_error(self, status: int) -> None:
        async with _source(_status_handler(status)) as (source, _captured):
            with pytest.raises(UpstreamTemporaryError):
                await source.search(_search())

    @pytest.mark.parametrize("status", [400, 401, 403])
    async def test_4xx_is_upstream_error(self, status: int) -> None:
        async with _source(_status_handler(status)) as (source, _captured):
            with pytest.raises(UpstreamError):
                await source.search(_search())


class TestRateLimitRetry:
    async def test_retries_after_short_retry_after(self, monkeypatch: pytest.MonkeyPatch) -> None:
        waits: list[float] = []

        async def fake_wait(seconds: float) -> None:
            waits.append(seconds)

        monkeypatch.setattr(s2_module, "_wait", fake_wait)
        responses = [
            httpx.Response(429, headers={"retry-after": "1"}),
            httpx.Response(200, json=_payload(_paper())),
        ]
        calls: list[int] = []

        def sequence(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return responses[min(len(calls) - 1, len(responses) - 1)]

        async with _source(sequence) as (source, _captured):
            records = await source.search(_search())

        assert records
        assert len(calls) == 2
        assert waits == [1.0]

    async def test_retry_after_over_budget_fails_fast(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        waits: list[float] = []

        async def fake_wait(seconds: float) -> None:
            waits.append(seconds)

        monkeypatch.setattr(s2_module, "_wait", fake_wait)
        async with _source(_retry_after_handler("60")) as (source, captured):
            with pytest.raises(UpstreamTemporaryError):
                await source.search(_search())

        assert waits == []
        assert len(captured) == 1


class TestThrottleAndCache:
    async def test_serial_throttle_between_requests(self) -> None:
        clock = FakeClock()
        waits: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            waits.append(seconds)

        throttle = RequestThrottle(1.0, sleep=fake_sleep, clock=clock)
        async with _source(
            _json_handler(_payload(_paper())),
            throttle=throttle,
            cache=SearchCache(0.0),
        ) as (source, _captured):
            await source.search(_search("first query"))
            await source.search(_search("second query"))

        assert waits == [1.0]

    async def test_cache_hit_skips_second_http_request(self) -> None:
        clock = FakeClock()
        async with _source(
            _json_handler(_payload(_paper())),
            cache=SearchCache(60.0, clock=clock),
        ) as (source, captured):
            await source.search(_search("cached query"))
            await source.search(_search("cached query"))

        assert len(captured) == 1

    async def test_cache_expires_after_ttl(self) -> None:
        clock = FakeClock()
        async with _source(
            _json_handler(_payload(_paper())),
            cache=SearchCache(60.0, clock=clock),
        ) as (source, captured):
            await source.search(_search("ttl query"))
            clock.value += 30.0
            await source.search(_search("ttl query"))
            assert len(captured) == 1
            clock.value += 31.0
            await source.search(_search("ttl query"))

        assert len(captured) == 2


class TestClientLifecycle:
    async def test_injected_client_is_not_closed(self) -> None:
        client = _mock_client(_json_handler(_payload()))
        source = SemanticScholarSource(client=client)
        try:
            await source.aclose()
            assert client.is_closed is False
        finally:
            await client.aclose()

    async def test_owned_client_is_closed(self) -> None:
        source = SemanticScholarSource()
        owned = source.client
        assert owned.is_closed is False
        await source.aclose()
        assert owned.is_closed is True
