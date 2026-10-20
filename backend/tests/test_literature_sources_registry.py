"""文献源注册表与多源聚合测试（Phase 1 P1-B / 计划 §1.2）。

全部请求经 `httpx.MockTransport`，**不产生真实网络**。覆盖：
- `build_sources(names)`：名字/别名映射、重复名字去重、未知名字 → `ValidationException`；
- `MultiSourceLiteratureSource`：并发检索 → 去重（DOI → source_id → 归一化题名）→ 轮转合并
  → 按 `max_results` 截断；`per_source_limit` 生效；**单源失败不影响整体**；
- 全部源失败时才上抛（临时故障优先 `UpstreamTemporaryError`，否则如实重抛）；
- `aclose()` 关闭全部源；注入 client 不被各源关闭；
- arXiv 源适配：官方要求的 ≥3s 请求间隔 + 短 TTL 缓存。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app.core.exceptions import UpstreamError, UpstreamTemporaryError, ValidationException
from app.tools.literature import source_registry as registry_module
from app.tools.literature.models import LiteratureRecord, LiteratureSearch
from app.tools.literature.openalex_search import OpenAlexSearchSource
from app.tools.literature.pubmed import PubMedSource
from app.tools.literature.semantic_scholar import SemanticScholarSource
from app.tools.literature.source_registry import (
    DEFAULT_SOURCE_NAMES,
    LiteratureSource,
    MultiSourceLiteratureSource,
    build_sources,
)

ARXIV_ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2401.12345v1</id>
    <title>An arXiv Paper</title>
    <published>2024-01-15T12:00:00Z</published>
    <author><name>Alice Zhang</name></author>
    <summary>abstract</summary>
    <link rel="alternate" href="https://arxiv.org/abs/2401.12345v1"/>
    <link title="pdf" href="https://arxiv.org/pdf/2401.12345v1"/>
    <category term="cs.CL"/>
  </entry>
</feed>
"""


def _search(query: str = "retrieval augmented generation", **overrides: Any) -> LiteratureSearch:
    data: dict[str, Any] = {"query": query, "max_results": 10}
    data.update(overrides)
    return LiteratureSearch(**data)


def _record(
    source_id: str,
    title: str = "Default Title",
    *,
    doi: str | None = None,
    url: str | None = None,
) -> LiteratureRecord:
    source_url = f"https://doi.org/{doi}" if doi else (url or f"https://example.org/{source_id}")
    return LiteratureRecord(
        source_id=source_id,
        title=title,
        authors=["Alice Zhang"],
        abstract="abstract",
        published_at=datetime(2024, 1, 1, tzinfo=UTC),
        source_url=source_url,
        pdf_url=source_url,
        categories=[],
    )


class FakeSource:
    """注入 fake 检索源：记录收到的 search 参数，可返回固定记录或抛出指定异常。"""

    def __init__(
        self,
        records: list[LiteratureRecord] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.records = list(records or [])
        self.error = error
        self.searches: list[LiteratureSearch] = []
        self.closed = False

    async def search(self, search: LiteratureSearch) -> list[LiteratureRecord]:
        self.searches.append(search)
        if self.error is not None:
            raise self.error
        return list(self.records)

    async def aclose(self) -> None:
        self.closed = True


def _mock_client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=httpx.Timeout(5.0))


class TestBuildSources:
    async def test_known_names_and_aliases(self) -> None:
        client = _mock_client(lambda request: httpx.Response(200))
        try:
            sources = build_sources(
                ["arxiv", "openalex", "s2", "semantic_scholar", "semantic-scholar", "pubmed"],
                client=client,
            )
            # 三个 S2 别名归一为同一个源，重复名字去重
            assert len(sources) == 4
            assert isinstance(sources[1], OpenAlexSearchSource)
            assert isinstance(sources[2], SemanticScholarSource)
            assert isinstance(sources[3], PubMedSource)
            assert all(isinstance(source, LiteratureSource) for source in sources)
            for source in sources:
                await source.aclose()
        finally:
            await client.aclose()

    def test_unknown_name_raises_validation_error(self) -> None:
        with pytest.raises(ValidationException) as excinfo:
            build_sources(["crossref"])
        assert "crossref" in str(excinfo.value)

    def test_empty_names_return_empty_list(self) -> None:
        assert build_sources([]) == []

    def test_default_source_names(self) -> None:
        assert DEFAULT_SOURCE_NAMES == ("arxiv", "openalex")

    async def test_injected_client_is_not_closed_by_sources(self) -> None:
        client = _mock_client(lambda request: httpx.Response(200))
        try:
            sources = build_sources(["openalex", "pubmed"], client=client)
            for source in sources:
                await source.aclose()
            assert client.is_closed is False
        finally:
            await client.aclose()


class TestMultiSourceDedup:
    async def test_dedupes_by_normalized_doi(self) -> None:
        first = _record("openalex:W1", "Paper One", doi="10.1000/ABC.123")
        second = _record("s2:xyz", "Paper One (duplicate record)", doi="10.1000/abc.123")
        merged = await MultiSourceLiteratureSource(
            [FakeSource([first]), FakeSource([second])]
        ).search(_search())
        assert [record.source_id for record in merged] == ["openalex:W1"]

    async def test_dedupes_by_source_id(self) -> None:
        first = _record("arxiv:2401.12345v1", "Paper One")
        second = _record("arxiv:2401.12345v1", "Paper One v2 title")
        merged = await MultiSourceLiteratureSource(
            [FakeSource([first]), FakeSource([second])]
        ).search(_search())
        assert len(merged) == 1

    async def test_dedupes_by_normalized_title(self) -> None:
        first = _record("openalex:W1", "An  Example   Title", url="https://a.example/1")
        second = _record("s2:abc", "an example title", url="https://b.example/2")
        merged = await MultiSourceLiteratureSource(
            [FakeSource([first]), FakeSource([second])]
        ).search(_search())
        assert [record.source_id for record in merged] == ["openalex:W1"]

    async def test_keeps_distinct_records(self) -> None:
        records = [
            _record("openalex:W1", "First Paper"),
            _record("openalex:W2", "Second Paper"),
        ]
        merged = await MultiSourceLiteratureSource([FakeSource(records)]).search(_search())
        assert [record.source_id for record in merged] == ["openalex:W1", "openalex:W2"]

    async def test_round_robin_interleaves_sources(self) -> None:
        first = FakeSource([_record("a:1", "A1"), _record("a:2", "A2")])
        second = FakeSource([_record("b:1", "B1"), _record("b:2", "B2")])
        merged = await MultiSourceLiteratureSource([first, second]).search(_search())
        assert [record.source_id for record in merged] == ["a:1", "b:1", "a:2", "b:2"]

    async def test_truncates_to_max_results(self) -> None:
        first = FakeSource([_record(f"a:{index}", f"A{index}") for index in range(5)])
        second = FakeSource([_record(f"b:{index}", f"B{index}") for index in range(5)])
        merged = await MultiSourceLiteratureSource([first, second]).search(_search(max_results=3))
        assert len(merged) == 3

    async def test_per_source_limit_is_applied_to_each_search(self) -> None:
        first = FakeSource([])
        second = FakeSource([])
        multi = MultiSourceLiteratureSource([first, second], per_source_limit=2)
        await multi.search(_search(max_results=10))
        assert [source.searches[0].max_results for source in (first, second)] == [2, 2]

    async def test_single_source_failure_does_not_fail_overall(self) -> None:
        failing = FakeSource(error=UpstreamTemporaryError("PubMed 超时"))
        healthy = FakeSource([_record("openalex:W1", "Healthy Paper")])
        merged = await MultiSourceLiteratureSource([failing, healthy]).search(_search())
        assert [record.source_id for record in merged] == ["openalex:W1"]

    async def test_all_sources_failed_temporarily_raises_temporary_error(self) -> None:
        sources = [
            FakeSource(error=UpstreamTemporaryError("timeout")),
            FakeSource(error=UpstreamTemporaryError("503")),
        ]
        with pytest.raises(UpstreamTemporaryError):
            await MultiSourceLiteratureSource(sources).search(_search())

    async def test_all_sources_failed_non_temporarily_reraises(self) -> None:
        sources = [
            FakeSource(error=UpstreamError("400")),
            FakeSource(error=ValidationException("坏响应")),
        ]
        with pytest.raises(UpstreamError):
            await MultiSourceLiteratureSource(sources).search(_search())

    async def test_empty_sources_return_empty_list(self) -> None:
        assert await MultiSourceLiteratureSource([]).search(_search()) == []

    async def test_aclose_closes_every_source(self) -> None:
        sources = [FakeSource(), FakeSource()]
        multi = MultiSourceLiteratureSource(sources)
        await multi.aclose()
        assert all(source.closed for source in sources)


class TestArxivSourceAdapter:
    async def test_throttle_uses_three_second_interval(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        waits: list[float] = []

        async def fake_wait(seconds: float) -> None:
            waits.append(seconds)

        monkeypatch.setattr(registry_module, "_wait", fake_wait)
        monkeypatch.setattr(registry_module, "_now", lambda: 100.0)

        client = _mock_client(lambda request: httpx.Response(200, text=ARXIV_ATOM))
        try:
            sources = build_sources(["arxiv"], client=client)
            source = sources[0]
            await source.search(_search("first query"))
            await source.search(_search("second query"))
            await source.aclose()
        finally:
            await client.aclose()

        assert waits == [registry_module.ARXIV_MIN_INTERVAL_SECONDS]

    async def test_cache_skips_second_arxiv_request(self) -> None:
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, text=ARXIV_ATOM)

        client = _mock_client(handler)
        try:
            sources = build_sources(["arxiv"], client=client)
            source = sources[0]
            first = await source.search(_search("cached query"))
            second = await source.search(_search("cached query"))
            await source.aclose()
        finally:
            await client.aclose()

        assert len(captured) == 1
        assert [record.source_id for record in first] == [record.source_id for record in second]

    async def test_injected_client_is_not_closed(self) -> None:
        client = _mock_client(lambda request: httpx.Response(200, text=ARXIV_ATOM))
        try:
            source = build_sources(["arxiv"], client=client)[0]
            await source.aclose()
            assert client.is_closed is False
        finally:
            await client.aclose()
