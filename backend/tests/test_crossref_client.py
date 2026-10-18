"""CrossRef 元数据适配器测试（Issue #3 T1）。

全部请求都经 `httpx.MockTransport` 构造，**不产生真实网络请求**。
覆盖：
- DOI 归一化（大小写 / `https://doi.org/` 前缀 / 首尾空白）；
- `lookup_doi` 命中解析与 404 → None；
- `search_by_title` 请求参数、结果归一化与空结果；
- 超时 / 连接失败 / 5xx / 429 重试耗尽 → `UpstreamTemporaryError`；
- 非 404 的 4xx → `UpstreamError`（不重试）；
- 坏 JSON / 非法 schema → `ValidationException`；
- 礼貌 User-Agent（含 mailto）；
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
from app.tools.literature import crossref as crossref_module
from app.tools.literature.crossref import CrossrefClient, normalize_doi

FIXTURE_DIR = Path(__file__).parent / "fixtures"
WORK_PAYLOAD: dict[str, Any] = json.loads(
    (FIXTURE_DIR / "crossref_work.json").read_text(encoding="utf-8")
)
SEARCH_PAYLOAD: dict[str, Any] = json.loads(
    (FIXTURE_DIR / "crossref_search.json").read_text(encoding="utf-8")
)


def _mock_client(handler: Any) -> httpx.AsyncClient:
    """按 `test_arxiv_client.py` 的写法构造 MockTransport 客户端（零真实网络）。"""
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        timeout=httpx.Timeout(5.0),
    )


@asynccontextmanager
async def _crossref(handler: Any, **kwargs: Any) -> AsyncIterator[CrossrefClient]:
    """构造注入 MockTransport 的客户端，测试结束自动关闭底层 http 客户端。"""
    client = _mock_client(handler)
    try:
        yield CrossrefClient(client=client, **kwargs)
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
        assert normalize_doi("  HTTPS://DOI.ORG/10.1000/XYZ.123  ") == "10.1000/xyz.123"

    def test_keeps_bare_doi_and_lowercases(self) -> None:
        assert normalize_doi("10.1000/AbC") == "10.1000/abc"

    def test_blank_input_returns_empty(self) -> None:
        assert normalize_doi("   ") == ""


class TestLookupDoi:
    async def test_parses_work_and_normalizes_fields(self) -> None:
        captured: list[httpx.Request] = []
        async with _crossref(_json_handler(WORK_PAYLOAD, captured)) as adapter:
            record = await adapter.lookup_doi("  HTTPS://DOI.ORG/10.1000/XYZ.123  ")

        assert record is not None
        assert record.source == "crossref"
        assert record.doi == "10.1000/xyz.123"
        assert record.title == (
            "Evidence-Constrained Literature Review with Multi-Agent Collaboration"
        )
        assert record.authors == ["Wei Zhang", "Mei Li"]
        assert record.year == 2024
        assert record.container_title == "Journal of Evidence Engineering"
        assert record.cited_by_count == 42
        assert record.is_open_access is None
        assert str(record.source_url) == "https://doi.org/10.1000/xyz.123"

        assert len(captured) == 1
        request = captured[0]
        assert request.method == "GET"
        assert request.url.scheme == "https"
        assert request.url.host == "api.crossref.org"
        assert request.url.path == "/works/10.1000/xyz.123"

    async def test_404_returns_none(self) -> None:
        captured: list[httpx.Request] = []
        async with _crossref(_status_handler(404, captured)) as adapter:
            result = await adapter.lookup_doi("10.1000/missing")
        assert result is None
        assert len(captured) == 1

    @pytest.mark.parametrize(
        "payload",
        [
            {"status": "ok"},
            {"status": "ok", "message": []},
            {"status": "ok", "message": {"DOI": "10.1000/xyz"}},
            {"status": "ok", "message": {"DOI": "10.1000/xyz", "title": []}},
        ],
    )
    async def test_invalid_schema_raises_validation_error(self, payload: dict[str, Any]) -> None:
        async with _crossref(_json_handler(payload, [])) as adapter:
            with pytest.raises(ValidationException):
                await adapter.lookup_doi("10.1000/xyz")

    async def test_malformed_json_raises_validation_error(self) -> None:
        handler = lambda request: httpx.Response(200, text="<html>not json</html>")  # noqa: E731
        async with _crossref(handler) as adapter:
            with pytest.raises(ValidationException):
                await adapter.lookup_doi("10.1000/xyz")

    async def test_non_object_json_raises_validation_error(self) -> None:
        handler = lambda request: httpx.Response(200, json=["not", "an", "object"])  # noqa: E731
        async with _crossref(handler) as adapter:
            with pytest.raises(ValidationException):
                await adapter.lookup_doi("10.1000/xyz")


class TestSearchByTitle:
    async def test_parses_items_and_normalizes_fields(self) -> None:
        captured: list[httpx.Request] = []
        async with _crossref(_json_handler(SEARCH_PAYLOAD, captured)) as adapter:
            records = await adapter.search_by_title("Evidence-Constrained Literature Review")

        assert len(records) == 2
        first, second = records
        assert first.source == "crossref"
        assert first.doi == "10.1000/abc.001"
        assert first.title == (
            "Evidence-Constrained Literature Review with Multi-Agent Collaboration"
        )
        assert first.authors == ["Wei Zhang", "Evidence Review Consortium"]
        assert first.year == 2024
        assert first.container_title == "Journal of Evidence Engineering"
        assert first.cited_by_count == 42
        assert str(first.source_url) == "https://doi.org/10.1000/abc.001"

        assert second.doi == "10.1000/abc.002"
        assert second.authors == []
        assert second.year == 2021
        assert second.container_title is None
        assert second.cited_by_count == 7

        assert len(captured) == 1
        request = captured[0]
        assert request.url.path == "/works"
        assert request.url.params["query.bibliographic"] == (
            "Evidence-Constrained Literature Review"
        )
        assert request.url.params["rows"] == "3"
        assert request.url.params.get("query.author") is None

    async def test_passes_rows_and_author_filter(self) -> None:
        captured: list[httpx.Request] = []
        async with _crossref(_json_handler(SEARCH_PAYLOAD, captured)) as adapter:
            await adapter.search_by_title("citation authenticity", rows=5, author="Wei Zhang")

        params = captured[0].url.params
        assert params["rows"] == "5"
        assert params["query.author"] == "Wei Zhang"

    async def test_empty_results_return_empty_list(self) -> None:
        payload = {"status": "ok", "message": {"items": []}}
        async with _crossref(_json_handler(payload, [])) as adapter:
            assert await adapter.search_by_title("no hit") == []

    async def test_missing_items_raises_validation_error(self) -> None:
        payload = {"status": "ok", "message": {}}
        async with _crossref(_json_handler(payload, [])) as adapter:
            with pytest.raises(ValidationException):
                await adapter.search_by_title("citation authenticity")

    async def test_non_object_item_raises_validation_error(self) -> None:
        payload = {"status": "ok", "message": {"items": ["not-a-dict"]}}
        async with _crossref(_json_handler(payload, [])) as adapter:
            with pytest.raises(ValidationException):
                await adapter.search_by_title("citation authenticity")


class TestErrorClassification:
    async def test_timeout_is_temporary_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("read timed out", request=request)

        async with _crossref(handler) as adapter:
            with pytest.raises(UpstreamTemporaryError):
                await adapter.lookup_doi("10.1000/xyz.123")

    async def test_connect_failure_is_temporary_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        async with _crossref(handler) as adapter:
            with pytest.raises(UpstreamTemporaryError):
                await adapter.lookup_doi("10.1000/xyz.123")

    @pytest.mark.parametrize("status", [500, 502, 503])
    async def test_5xx_is_temporary_error_and_not_retried(self, status: int) -> None:
        captured: list[httpx.Request] = []
        async with _crossref(_status_handler(status, captured)) as adapter:
            with pytest.raises(UpstreamTemporaryError):
                await adapter.lookup_doi("10.1000/xyz.123")
        assert len(captured) == 1

    @pytest.mark.parametrize("status", [400, 401, 403, 410])
    async def test_non_404_4xx_is_upstream_error_and_not_retried(self, status: int) -> None:
        captured: list[httpx.Request] = []
        async with _crossref(_status_handler(status, captured)) as adapter:
            with pytest.raises(UpstreamError) as excinfo:
                await adapter.lookup_doi("10.1000/xyz.123")
        assert not isinstance(excinfo.value, UpstreamTemporaryError)
        assert len(captured) == 1


@pytest.fixture
def recorded_waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """替换模块内 `_wait`：记录等待时长且不产生真实 sleep。"""
    waits: list[float] = []

    async def fake_wait(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(crossref_module, "_wait", fake_wait)
    return waits


class TestRateLimitRetry:
    """429 处理遵循计划 §2.9 R3：Retry-After 超阈值不得在任务路径内睡眠。"""

    def test_retry_after_budget_is_two_seconds(self) -> None:
        assert crossref_module.MAX_RETRY_AFTER_SECONDS == 2.0

    async def test_retries_after_429_with_short_retry_after(
        self, recorded_waits: list[float]
    ) -> None:
        captured: list[httpx.Request] = []
        responses = [
            httpx.Response(429, headers={"retry-after": "0.25"}),
            httpx.Response(200, json=WORK_PAYLOAD),
        ]
        async with _crossref(_sequence_handler(responses, captured)) as adapter:
            record = await adapter.lookup_doi("10.1000/xyz.123")

        assert record is not None
        assert record.doi == "10.1000/xyz.123"
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
        async with _crossref(_sequence_handler(responses, captured)) as adapter:
            record = await adapter.lookup_doi("10.1000/xyz.123")

        assert record is not None
        assert len(captured) == 2
        assert recorded_waits == [float(retry_after)]

    @pytest.mark.parametrize("retry_after", ["60", "2.5", "10"])
    async def test_retry_after_over_budget_fails_fast_without_waiting(
        self, recorded_waits: list[float], retry_after: str
    ) -> None:
        captured: list[httpx.Request] = []
        responses = [httpx.Response(429, headers={"retry-after": retry_after})]
        async with _crossref(_sequence_handler(responses, captured)) as adapter:
            with pytest.raises(UpstreamTemporaryError):
                await adapter.lookup_doi("10.1000/xyz.123")

        assert recorded_waits == []
        assert len(captured) == 1

    async def test_retry_after_60_on_search_fails_fast(self, recorded_waits: list[float]) -> None:
        """生产实测场景：标题检索 429 + `Retry-After: 60` 必须立即降级。"""
        captured: list[httpx.Request] = []
        responses = [httpx.Response(429, headers={"retry-after": "60"})]
        async with _crossref(_sequence_handler(responses, captured)) as adapter:
            with pytest.raises(UpstreamTemporaryError):
                await adapter.search_by_title("citation authenticity")

        assert recorded_waits == []
        assert len(captured) == 1

    async def test_429_retry_exhaustion_is_temporary_error(
        self, recorded_waits: list[float]
    ) -> None:
        captured: list[httpx.Request] = []
        responses = [httpx.Response(429, headers={"retry-after": "1"})]
        async with _crossref(_sequence_handler(responses, captured), max_retries=2) as adapter:
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
        async with _crossref(_sequence_handler(responses, captured), max_retries=2) as adapter:
            record = await adapter.lookup_doi("10.1000/xyz.123")

        assert record is not None
        assert len(captured) == 3
        assert recorded_waits == [0.0, 0.0]

    async def test_429_without_retry_after_uses_default_within_budget(
        self, recorded_waits: list[float]
    ) -> None:
        captured: list[httpx.Request] = []
        responses = [httpx.Response(429), httpx.Response(200, json=SEARCH_PAYLOAD)]
        async with _crossref(_sequence_handler(responses, captured)) as adapter:
            records = await adapter.search_by_title("citation authenticity")

        assert len(records) == 2
        assert len(recorded_waits) == 1
        assert 0 < recorded_waits[0] <= crossref_module.MAX_RETRY_AFTER_SECONDS


class TestPoliteHeaders:
    async def test_user_agent_contains_mailto(self) -> None:
        captured: list[httpx.Request] = []
        async with _crossref(
            _json_handler(WORK_PAYLOAD, captured), mailto="contact@example.org"
        ) as adapter:
            await adapter.lookup_doi("10.1000/xyz.123")

        assert captured[0].headers["user-agent"] == (
            "maa-literature-review/0.1 (mailto:contact@example.org)"
        )

    async def test_user_agent_without_mailto(self) -> None:
        captured: list[httpx.Request] = []
        async with _crossref(_json_handler(WORK_PAYLOAD, captured)) as adapter:
            await adapter.lookup_doi("10.1000/xyz.123")

        assert captured[0].headers["user-agent"] == "maa-literature-review/0.1"

    async def test_custom_user_agent_is_preserved(self) -> None:
        captured: list[httpx.Request] = []
        async with _crossref(
            _json_handler(WORK_PAYLOAD, captured),
            user_agent="custom-agent/2.0",
            mailto="ops@example.org",
        ) as adapter:
            await adapter.lookup_doi("10.1000/xyz.123")

        assert captured[0].headers["user-agent"] == ("custom-agent/2.0 (mailto:ops@example.org)")


class TestClientLifecycle:
    async def test_injected_client_is_not_closed(self) -> None:
        client = _mock_client(lambda request: httpx.Response(200, json=WORK_PAYLOAD))
        adapter = CrossrefClient(client=client)
        try:
            await adapter.aclose()
            assert client.is_closed is False
        finally:
            await client.aclose()

    async def test_owned_client_is_created_lazily_and_closed(self) -> None:
        adapter = CrossrefClient()
        owned = adapter.client
        assert adapter.client is owned
        assert owned.is_closed is False
        await adapter.aclose()
        assert owned.is_closed is True

    async def test_aclose_is_idempotent(self) -> None:
        client = _mock_client(lambda request: httpx.Response(200, json=WORK_PAYLOAD))
        adapter = CrossrefClient(client=client)
        try:
            await adapter.aclose()
            await adapter.aclose()
            assert client.is_closed is False
        finally:
            await client.aclose()
