"""OpenAlex 检索源测试（Phase 1 P1-B / 计划 §1.2）。

全部请求经 `httpx.MockTransport`，**不产生真实网络**。覆盖：
- `/works` 检索参数（search/per_page/排序/日期过滤/mailto 礼貌池）；
- 结果归一化为既有 `LiteratureRecord`（source_id/作者/摘要倒排索引/日期/DOI URL/PDF/分类）；
- 内部节流（两次请求之间至少间隔 min_interval）与短 TTL 缓存（同 query 不重复外呼）；
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
from app.tools.literature import openalex as openalex_module
from app.tools.literature.models import LiteratureRecord, LiteratureSearch
from app.tools.literature.openalex_search import OpenAlexSearchSource
from app.tools.literature.source_registry import RequestThrottle, SearchCache


class FakeClock:
    """可手动推进的单调时钟（测试节流与缓存 TTL，不产生真实等待）。"""

    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


def _work(**overrides: Any) -> dict[str, Any]:
    work: dict[str, Any] = {
        "id": "https://openalex.org/W2741809807",
        "doi": "https://doi.org/10.7717/PEERJ.4375",
        "title": "Evidence-Constrained Literature Review with Multi-Agent Collaboration",
        "display_name": "Evidence-Constrained Literature Review with Multi-Agent Collaboration",
        "publication_date": "2024-05-12",
        "publication_year": 2024,
        "authorships": [
            {"author": {"display_name": "Wei Zhang"}},
            {"author": {"display_name": "Mei Li"}},
        ],
        "primary_location": {
            "landing_page_url": "https://example.org/landing",
            "pdf_url": "https://example.org/paper.pdf",
        },
        "abstract_inverted_index": {"We": [0], "study": [1], "RAG": [2]},
        "keywords": [{"display_name": "Retrieval-Augmented Generation"}],
    }
    work.update(overrides)
    return work


def _payload(*works: dict[str, Any]) -> dict[str, Any]:
    return {"meta": {"count": len(works)}, "results": list(works)}


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
) -> AsyncIterator[tuple[OpenAlexSearchSource, list[httpx.Request]]]:
    captured: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    client = _mock_client(recording)
    try:
        yield (
            OpenAlexSearchSource(
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
    async def test_sends_search_params_and_mailto(self) -> None:
        async with _source(
            _json_handler(_payload(_work())),
            mailto="contact@example.org",
            cache=SearchCache(0.0),
        ) as (source, captured):
            await source.search(
                _search(
                    date_from=date(2024, 1, 1),
                    date_to=date(2024, 12, 31),
                )
            )

        assert len(captured) == 1
        request = captured[0]
        assert request.url.host == "api.openalex.org"
        assert request.url.path == "/works"
        params = request.url.params
        assert params["search"] == "retrieval augmented generation"
        assert params["per_page"] == "5"
        assert params["sort"] == "relevance_score:desc"
        assert params["mailto"] == "contact@example.org"
        assert params["filter"] == (
            "from_publication_date:2024-01-01,to_publication_date:2024-12-31"
        )

    async def test_omits_filter_without_date_range(self) -> None:
        async with _source(_json_handler(_payload())) as (source, captured):
            await source.search(_search())
        assert captured[0].url.params.get("filter") is None

    async def test_limits_per_page_to_contract_max(self) -> None:
        async with _source(_json_handler(_payload())) as (source, captured):
            await source.search(_search(max_results=50))
        assert captured[0].url.params["per_page"] == "50"


class TestRecordMapping:
    async def test_maps_work_to_literature_record(self) -> None:
        async with _source(_json_handler(_payload(_work())), mailto="contact@example.org") as (
            source,
            captured,
        ):
            records = await source.search(_search())

        assert len(records) == 1
        record = records[0]
        assert isinstance(record, LiteratureRecord)
        assert record.source_id == "openalex:W2741809807"
        assert record.title == (
            "Evidence-Constrained Literature Review with Multi-Agent Collaboration"
        )
        assert record.authors == ["Wei Zhang", "Mei Li"]
        assert record.abstract == "We study RAG"
        assert record.published_at == datetime(2024, 5, 12, tzinfo=UTC)
        # DOI 归一化后作为规范链接（供跨源去重）
        assert str(record.source_url) == "https://doi.org/10.7717/peerj.4375"
        assert str(record.pdf_url) == "https://example.org/paper.pdf"
        assert record.categories == ["Retrieval-Augmented Generation"]
        assert captured[0].headers["user-agent"] == (
            "maa-literature-review/0.1 (mailto:contact@example.org)"
        )

    async def test_falls_back_to_year_for_missing_publication_date(self) -> None:
        work = _work(publication_date=None, publication_year=2023)
        async with _source(_json_handler(_payload(work))) as (source, _captured):
            records = await source.search(_search())
        assert records[0].published_at == datetime(2023, 1, 1, tzinfo=UTC)

    async def test_pdf_url_falls_back_to_landing_page(self) -> None:
        work = _work(primary_location={"landing_page_url": "https://example.org/landing"})
        async with _source(_json_handler(_payload(work))) as (source, _captured):
            records = await source.search(_search())
        assert str(records[0].pdf_url) == "https://doi.org/10.7717/peerj.4375"

    async def test_uses_best_oa_location_pdf(self) -> None:
        work = _work(
            primary_location={},
            best_oa_location={"pdf_url": "https://example.org/oa.pdf"},
        )
        async with _source(_json_handler(_payload(work))) as (source, _captured):
            records = await source.search(_search())
        assert str(records[0].pdf_url) == "https://example.org/oa.pdf"

    async def test_filters_by_date_range_locally(self) -> None:
        older = _work(id="https://openalex.org/W1", doi=None, publication_date="2020-01-01")
        newer = _work(id="https://openalex.org/W2", doi=None, publication_date="2024-06-01")
        async with _source(_json_handler(_payload(older, newer))) as (source, _captured):
            records = await source.search(_search(date_from=date(2024, 1, 1)))
        assert [record.source_id for record in records] == ["openalex:W2"]

    async def test_empty_results_return_empty_list(self) -> None:
        async with _source(_json_handler(_payload())) as (source, captured):
            assert await source.search(_search()) == []
        assert len(captured) == 1


class TestSchemaErrors:
    @pytest.mark.parametrize(
        "payload",
        [
            {"meta": {"count": 0}},
            {"results": "not-a-list"},
            {"results": ["not-a-dict"]},
            {"results": [{"id": "https://openalex.org/W1", "title": "no date"}]},
            {"results": [{"title": "no id", "publication_date": "2024-01-01"}]},
        ],
    )
    async def test_invalid_schema_raises_validation_error(self, payload: dict[str, Any]) -> None:
        async with _source(_json_handler(payload)) as (source, _captured):
            with pytest.raises(ValidationException):
                await source.search(_search())

    async def test_broken_json_raises_validation_error(self) -> None:
        async with _source(lambda request: httpx.Response(200, text="<html>")) as (source, _cap):
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

        monkeypatch.setattr(openalex_module, "_wait", fake_wait)
        responses = [
            httpx.Response(429, headers={"retry-after": "1"}),
            httpx.Response(200, json=_payload(_work())),
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

        monkeypatch.setattr(openalex_module, "_wait", fake_wait)
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
            _json_handler(_payload(_work())),
            throttle=throttle,
            cache=SearchCache(0.0),
        ) as (source, _captured):
            await source.search(_search("first query"))
            await source.search(_search("second query"))

        assert waits == [1.0]

    async def test_cache_hit_skips_second_http_request(self) -> None:
        clock = FakeClock()
        async with _source(
            _json_handler(_payload(_work())),
            cache=SearchCache(60.0, clock=clock),
        ) as (source, captured):
            first = await source.search(_search("cached query"))
            second = await source.search(_search("cached query"))

        assert len(captured) == 1
        assert [record.source_id for record in first] == [record.source_id for record in second]

    async def test_cache_expires_after_ttl(self) -> None:
        clock = FakeClock()
        async with _source(
            _json_handler(_payload(_work())),
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
        source = OpenAlexSearchSource(client=client)
        try:
            await source.aclose()
            assert client.is_closed is False
        finally:
            await client.aclose()

    async def test_owned_client_is_closed(self) -> None:
        source = OpenAlexSearchSource()
        owned = source.client
        assert owned.is_closed is False
        await source.aclose()
        assert owned.is_closed is True
