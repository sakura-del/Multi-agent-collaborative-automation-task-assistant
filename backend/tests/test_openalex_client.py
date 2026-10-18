"""OpenAlex 元数据适配器测试（Issue #3 T1）。

全部请求都经 `httpx.MockTransport` 构造，**不产生真实网络请求**。
覆盖：
- DOI 归一化（大小写 / `https://doi.org/` 前缀 / 首尾空白）与 `/works/https://doi.org/{doi}` 路径；
- `lookup_doi` 命中解析（含 `cited_by_count` / `is_open_access` / 期刊名）与 404 → None；
- `search_by_title` 请求参数（`search` / `per_page` / `mailto`）、`display_name` 兜底、空结果；
- 超时 / 连接失败 / 5xx / 429 重试耗尽 → `UpstreamTemporaryError`；
- 非 404 的 4xx → `UpstreamError`（不重试）；
- 坏 JSON / 非法 schema → `ValidationException`；
- polite pool：`mailto` 同时进入查询参数与 User-Agent；
- 客户端所有权：注入的 client 不被 `aclose()` 关闭，自建 client 会被关闭。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core.exceptions import UpstreamError, UpstreamTemporaryError, ValidationException
from app.tools.literature import openalex as openalex_module
from app.tools.literature.models import MetadataRecord
from app.tools.literature.openalex import OpenAlexClient, normalize_doi

FIXTURE_DIR = Path(__file__).parent / "fixtures"
WORK_PAYLOAD: dict[str, Any] = json.loads(
    (FIXTURE_DIR / "openalex_work.json").read_text(encoding="utf-8")
)
SEARCH_PAYLOAD: dict[str, Any] = json.loads(
    (FIXTURE_DIR / "openalex_search.json").read_text(encoding="utf-8")
)


def _mock_client(handler: Any) -> httpx.AsyncClient:
    """按 `test_arxiv_client.py` 的写法构造 MockTransport 客户端（零真实网络）。"""
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        timeout=httpx.Timeout(5.0),
    )


@asynccontextmanager
async def _openalex(handler: Any, **kwargs: Any) -> AsyncIterator[OpenAlexClient]:
    """构造注入 MockTransport 的客户端，测试结束自动关闭底层 http 客户端。"""
    client = _mock_client(handler)
    try:
        yield OpenAlexClient(client=client, **kwargs)
    finally:
        await client.aclose()


def _json_handler(payload: dict[str, Any], captured: list[httpx.Request]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=payload)

    return handler


def _status_handler(
    status: int,
    captured: list[httpx.Request],
    *,
    headers: dict[str, str] | None = None,
) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(status, headers=headers, json={"error": "upstream"})

    return handler


def _sequence_handler(responses: list[httpx.Response], captured: list[httpx.Request]) -> Any:
    """按请求次数依次返回预设响应；超出后用最后一个响应兜底。"""

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        index = min(len(captured) - 1, len(responses) - 1)
        return responses[index]

    return handler


class TestNormalizeDoi:
    def test_strips_https_prefix_and_lowercases(self) -> None:
        assert normalize_doi("  HTTPS://DOI.ORG/10.7717/PEERJ.4375  ") == "10.7717/peerj.4375"

    def test_keeps_bare_doi_and_lowercases(self) -> None:
        assert normalize_doi("10.7717/PeerJ.4375") == "10.7717/peerj.4375"

    def test_blank_input_returns_empty(self) -> None:
        assert normalize_doi("   ") == ""


class TestLookupDoi:
    async def test_parses_work_and_normalizes_fields(self) -> None:
        captured: list[httpx.Request] = []
        async with _openalex(
            _json_handler(WORK_PAYLOAD, captured), mailto="contact@example.org"
        ) as adapter:
            record = await adapter.lookup_doi("  HTTPS://DOI.ORG/10.7717/PEERJ.4375 ")

        assert record is not None
        assert isinstance(record, MetadataRecord)
        assert record.source == "openalex"
        assert record.doi == "10.7717/peerj.4375"
        assert record.title == (
            "Evidence-Constrained Literature Review with Multi-Agent Collaboration"
        )
        assert record.authors == ["Wei Zhang", "Mei Li"]
        assert record.year == 2024
        assert record.container_title == "PeerJ"
        assert record.cited_by_count == 128
        assert record.is_open_access is True
        assert str(record.source_url) == "https://openalex.org/W2741809807"

        assert len(captured) == 1
        request = captured[0]
        assert request.method == "GET"
        assert request.url.scheme == "https"
        assert request.url.host == "api.openalex.org"
        assert request.url.path == "/works/https://doi.org/10.7717/peerj.4375"
        assert request.url.params["mailto"] == "contact@example.org"

    async def test_404_returns_none(self) -> None:
        captured: list[httpx.Request] = []
        async with _openalex(_status_handler(404, captured)) as adapter:
            result = await adapter.lookup_doi("10.7717/missing.0001")
        assert result is None
        assert len(captured) == 1

    @pytest.mark.parametrize(
        "payload",
        [
            {"id": "https://openalex.org/W1", "doi": "https://doi.org/10.1000/xyz"},
            {"id": "https://openalex.org/W1", "title": "", "display_name": ""},
            {"id": "https://openalex.org/W1", "title": None},
        ],
    )
    async def test_invalid_schema_raises_validation_error(self, payload: dict[str, Any]) -> None:
        async with _openalex(_json_handler(payload, [])) as adapter:
            with pytest.raises(ValidationException):
                await adapter.lookup_doi("10.1000/xyz")

    async def test_malformed_json_raises_validation_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>not json</html>")

        async with _openalex(handler) as adapter:
            with pytest.raises(ValidationException):
                await adapter.lookup_doi("10.1000/xyz")

    async def test_non_object_json_raises_validation_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=["not", "an", "object"])

        async with _openalex(handler) as adapter:
            with pytest.raises(ValidationException):
                await adapter.lookup_doi("10.1000/xyz")


class TestSearchByTitle:
    async def test_parses_results_and_normalizes_fields(self) -> None:
        captured: list[httpx.Request] = []
        async with _openalex(
            _json_handler(SEARCH_PAYLOAD, captured), mailto="contact@example.org"
        ) as adapter:
            records = await adapter.search_by_title("Evidence-Constrained Literature Review")

        assert len(records) == 2
        first, second = records
        assert first.source == "openalex"
        assert first.doi == "10.7717/peerj.4375"
        assert first.title == (
            "Evidence-Constrained Literature Review with Multi-Agent Collaboration"
        )
        assert first.authors == ["Wei Zhang"]
        assert first.year == 2024
        assert first.container_title == "PeerJ"
        assert first.cited_by_count == 128
        assert first.is_open_access is True
        assert str(first.source_url) == "https://openalex.org/W2741809807"

        # 第二条：缺 title 时用 display_name 兜底；无 DOI / 期刊 / 作者
        assert second.title == "A Survey of Citation Authenticity Verification"
        assert second.doi is None
        assert second.authors == []
        assert second.year == 2021
        assert second.container_title is None
        assert second.cited_by_count == 7
        assert second.is_open_access is False
        assert str(second.source_url) == "https://openalex.org/W9999999999"

        assert len(captured) == 1
        request = captured[0]
        assert request.url.path == "/works"
        assert request.url.params["search"] == "Evidence-Constrained Literature Review"
        assert request.url.params["per_page"] == "3"
        assert request.url.params["mailto"] == "contact@example.org"

    async def test_passes_per_page(self) -> None:
        captured: list[httpx.Request] = []
        async with _openalex(_json_handler(SEARCH_PAYLOAD, captured)) as adapter:
            await adapter.search_by_title("citation authenticity", per_page=10)

        assert captured[0].url.params["per_page"] == "10"
        assert captured[0].url.params.get("mailto") is None

    async def test_empty_results_return_empty_list(self) -> None:
        payload = {"meta": {"count": 0}, "results": []}
        async with _openalex(_json_handler(payload, [])) as adapter:
            assert await adapter.search_by_title("no hit") == []

    async def test_missing_results_raises_validation_error(self) -> None:
        payload = {"meta": {"count": 0}}
        async with _openalex(_json_handler(payload, [])) as adapter:
            with pytest.raises(ValidationException):
                await adapter.search_by_title("citation authenticity")

    async def test_non_object_item_raises_validation_error(self) -> None:
        payload = {"meta": {"count": 1}, "results": ["not-a-dict"]}
        async with _openalex(_json_handler(payload, [])) as adapter:
            with pytest.raises(ValidationException):
                await adapter.search_by_title("citation authenticity")


class TestErrorClassification:
    async def test_timeout_is_temporary_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("read timed out", request=request)

        async with _openalex(handler) as adapter:
            with pytest.raises(UpstreamTemporaryError):
                await adapter.lookup_doi("10.7717/peerj.4375")

    async def test_connect_failure_is_temporary_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        async with _openalex(handler) as adapter:
            with pytest.raises(UpstreamTemporaryError):
                await adapter.lookup_doi("10.7717/peerj.4375")

    @pytest.mark.parametrize("status", [500, 502, 503])
    async def test_5xx_is_temporary_error_and_not_retried(self, status: int) -> None:
        captured: list[httpx.Request] = []
        async with _openalex(_status_handler(status, captured)) as adapter:
            with pytest.raises(UpstreamTemporaryError):
                await adapter.lookup_doi("10.7717/peerj.4375")
        assert len(captured) == 1

    @pytest.mark.parametrize("status", [400, 401, 403, 410])
    async def test_non_404_4xx_is_upstream_error_and_not_retried(self, status: int) -> None:
        captured: list[httpx.Request] = []
        async with _openalex(_status_handler(status, captured)) as adapter:
            with pytest.raises(UpstreamError) as excinfo:
                await adapter.lookup_doi("10.7717/peerj.4375")
        assert not isinstance(excinfo.value, UpstreamTemporaryError)
        assert len(captured) == 1


@pytest.fixture
def recorded_waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """替换模块内 `_wait`：记录等待时长且不产生真实 sleep。"""
    waits: list[float] = []

    async def fake_wait(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(openalex_module, "_wait", fake_wait)
    return waits


class TestRateLimitRetry:
    """429 处理遵循计划 §2.9 R3：Retry-After 超阈值不得在任务路径内睡眠。"""

    def test_retry_after_budget_is_two_seconds(self) -> None:
        assert openalex_module.MAX_RETRY_AFTER_SECONDS == 2.0

    async def test_retries_after_429_with_short_retry_after(
        self, recorded_waits: list[float]
    ) -> None:
        captured: list[httpx.Request] = []
        responses = [
            httpx.Response(429, headers={"retry-after": "0.25"}),
            httpx.Response(200, json=WORK_PAYLOAD),
        ]
        async with _openalex(_sequence_handler(responses, captured)) as adapter:
            record = await adapter.lookup_doi("10.7717/peerj.4375")

        assert record is not None
        assert record.doi == "10.7717/peerj.4375"
        assert len(captured) == 2
        assert recorded_waits == [0.25]
        assert captured[0].url == captured[1].url

    @pytest.mark.parametrize("retry_after", ["2", "1"])
    async def test_retry_after_within_budget_is_retried(
        self, recorded_waits: list[float], retry_after: str
    ) -> None:
        captured: list[httpx.Request] = []
        responses = [
            httpx.Response(429, headers={"retry-after": retry_after}),
            httpx.Response(200, json=WORK_PAYLOAD),
        ]
        async with _openalex(_sequence_handler(responses, captured)) as adapter:
            record = await adapter.lookup_doi("10.7717/peerj.4375")

        assert record is not None
        assert len(captured) == 2
        assert recorded_waits == [float(retry_after)]

    @pytest.mark.parametrize("retry_after", ["60", "2.5", "10"])
    async def test_retry_after_over_budget_fails_fast_without_waiting(
        self, recorded_waits: list[float], retry_after: str
    ) -> None:
        captured: list[httpx.Request] = []
        responses = [httpx.Response(429, headers={"retry-after": retry_after})]
        async with _openalex(_sequence_handler(responses, captured)) as adapter:
            with pytest.raises(UpstreamTemporaryError):
                await adapter.lookup_doi("10.7717/peerj.4375")

        assert recorded_waits == []
        assert len(captured) == 1

    async def test_retry_after_60_on_search_fails_fast(self, recorded_waits: list[float]) -> None:
        """生产实测场景：标题检索 429 + `Retry-After: 60` 必须立即降级。"""
        captured: list[httpx.Request] = []
        responses = [httpx.Response(429, headers={"retry-after": "60"})]
        async with _openalex(_sequence_handler(responses, captured)) as adapter:
            with pytest.raises(UpstreamTemporaryError):
                await adapter.search_by_title("citation authenticity")

        assert recorded_waits == []
        assert len(captured) == 1

    async def test_429_retry_exhaustion_is_temporary_error(
        self, recorded_waits: list[float]
    ) -> None:
        captured: list[httpx.Request] = []
        responses = [httpx.Response(429, headers={"retry-after": "1"})]
        async with _openalex(_sequence_handler(responses, captured), max_retries=2) as adapter:
            with pytest.raises(UpstreamTemporaryError):
                await adapter.search_by_title("citation authenticity")

        assert len(captured) == 3
        assert recorded_waits == [1.0, 1.0]

    async def test_429_zero_or_negative_retry_after_retries_immediately(
        self, recorded_waits: list[float]
    ) -> None:
        captured: list[httpx.Request] = []
        responses = [
            httpx.Response(429, headers={"retry-after": "0"}),
            httpx.Response(429, headers={"retry-after": "-3"}),
            httpx.Response(200, json=WORK_PAYLOAD),
        ]
        async with _openalex(_sequence_handler(responses, captured), max_retries=2) as adapter:
            record = await adapter.lookup_doi("10.7717/peerj.4375")

        assert record is not None
        assert len(captured) == 3
        assert recorded_waits == [0.0, 0.0]

    async def test_429_without_retry_after_uses_default_within_budget(
        self, recorded_waits: list[float]
    ) -> None:
        captured: list[httpx.Request] = []
        responses = [httpx.Response(429), httpx.Response(200, json=SEARCH_PAYLOAD)]
        async with _openalex(_sequence_handler(responses, captured)) as adapter:
            records = await adapter.search_by_title("citation authenticity")

        assert len(records) == 2
        assert len(recorded_waits) == 1
        assert 0 < recorded_waits[0] <= openalex_module.MAX_RETRY_AFTER_SECONDS


class TestPolitePool:
    async def test_mailto_enters_query_params(self) -> None:
        captured: list[httpx.Request] = []
        async with _openalex(
            _json_handler(WORK_PAYLOAD, captured), mailto="contact@example.org"
        ) as adapter:
            await adapter.lookup_doi("10.7717/peerj.4375")

        assert captured[0].url.params["mailto"] == "contact@example.org"

    async def test_mailto_enters_user_agent(self) -> None:
        captured: list[httpx.Request] = []
        async with _openalex(
            _json_handler(WORK_PAYLOAD, captured), mailto="contact@example.org"
        ) as adapter:
            await adapter.lookup_doi("10.7717/peerj.4375")

        assert captured[0].headers["user-agent"] == (
            "maa-literature-review/0.1 (mailto:contact@example.org)"
        )

    async def test_no_mailto_param_when_not_configured(self) -> None:
        captured: list[httpx.Request] = []
        async with _openalex(_json_handler(WORK_PAYLOAD, captured)) as adapter:
            await adapter.lookup_doi("10.7717/peerj.4375")

        assert captured[0].url.params.get("mailto") is None
        assert captured[0].headers["user-agent"] == "maa-literature-review/0.1"


class TestClientLifecycle:
    async def test_injected_client_is_not_closed(self) -> None:
        client = _mock_client(lambda request: httpx.Response(200, json=WORK_PAYLOAD))
        adapter = OpenAlexClient(client=client)
        try:
            await adapter.aclose()
            assert client.is_closed is False
        finally:
            await client.aclose()

    async def test_owned_client_is_created_lazily_and_closed(self) -> None:
        adapter = OpenAlexClient()
        owned = adapter.client
        assert adapter.client is owned
        assert owned.is_closed is False
        await adapter.aclose()
        assert owned.is_closed is True

    async def test_aclose_is_idempotent(self) -> None:
        client = _mock_client(lambda request: httpx.Response(200, json=WORK_PAYLOAD))
        adapter = OpenAlexClient(client=client)
        try:
            await adapter.aclose()
            await adapter.aclose()
            assert client.is_closed is False
        finally:
            await client.aclose()
