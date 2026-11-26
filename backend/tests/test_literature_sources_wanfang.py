"""万方检索源测试（Issue #67 · BYOK）。

全部走 `httpx.MockTransport`，**不产生真实外呼**；覆盖：
- 中文期刊 / 学位论文归一化（DOI → doi.org、无 DOI → 详情页、repeated 字段取首值）；
- 请求契约（路径、X-App-Key、payload 的 collections/query/rows/sort）；
- 未配置 Key → `ValidationException`（且不发请求，避免静默空结果）；
- 401/402 → `UpstreamError`（Key 无效 / 余额不足，文案要能指导用户去设置页换 Key）；
- 429 有限重试、429/503 → `UpstreamTemporaryError`；
- 单任务 5 次基础检索费用护栏（超限不再外呼，返回空列表）；
- 本地日期兜底过滤、同条件缓存只外呼一次。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

from app.core.exceptions import UpstreamError, UpstreamTemporaryError, ValidationException
from app.tools.literature.models import LiteratureSearch
from app.tools.literature.source_registry import RequestThrottle, SearchCache
from app.tools.literature.wanfang import DEFAULT_COLLECTIONS, WanfangSource

_APP_KEY = "test-wfdata-key"
_BASE_URL = "http://api.wfdata.test"


def _document(
    *,
    document_id: str = "periodical_c61d1b0d",
    resource_type: str = "Periodical",
    title: tuple[str, ...] = (
        "数字化转型对企业创新绩效的影响",
        "The impact of digital transformation",
    ),
    creator: tuple[str, ...] = ("张三", "李四"),
    abstract: tuple[str, ...] = ("本文研究数字化转型……", "English abstract"),
    doi: str = "10.3969/j.issn.1000-1239.2023.01.001",
    publish_year: int | None = 2023,
    publish_date: str | None = None,
    keywords: tuple[str, ...] = ("数字化转型", "创新绩效"),
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "Id": document_id,
        "Title": list(title),
        "Creator": list(creator),
        "Abstract": list(abstract),
        "Keywords": list(keywords),
    }
    if doi:
        fields["DOI"] = doi
    if publish_date is not None:
        fields["PublishDate"] = publish_date
    if publish_year is not None:
        fields["PublishYear"] = publish_year
    # 线上真实响应：题录字段在按资源类型命名的子对象里，`fields` 是空对象
    return {
        "resourceType": resource_type,
        "uid": f"uid-{document_id}",
        "fields": {},
        resource_type: fields,
    }


def _payload(*documents: dict[str, Any]) -> dict[str, Any]:
    return {"documents": list(documents), "numFound": len(documents)}


def _mock_client(handler: httpx.MockTransportHandler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _search(query: str = "数字化转型 企业创新", **overrides: Any) -> LiteratureSearch:
    return LiteratureSearch(query=query, max_results=10, **overrides)


def _json_handler(payload: dict[str, Any]) -> httpx.MockTransportHandler:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps(payload, ensure_ascii=False))

    return handler


def _status_handler(status_code: int, *, headers: dict[str, str] | None = None) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, headers=headers, content=b"{}")

    return handler


@asynccontextmanager
async def _source(
    handler: httpx.MockTransportHandler,
    *,
    app_key: str | None = _APP_KEY,
    max_retries: int = 1,
    cache: SearchCache | None = None,
    **kwargs: Any,
) -> AsyncIterator[tuple[WanfangSource, list[httpx.Request]]]:
    """构造注入 MockTransport 的源，并记录全部出站请求；退出时关闭自建 client。

    默认 `SearchCache(0.0)`（缓存立即过期）：大多数用例要观察「每次搜索都真的外呼」；
    需要验证缓存命中的用例显式传 `cache=SearchCache(60.0)`。
    """
    captured: list[httpx.Request] = []

    def recording_handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    client = _mock_client(recording_handler)
    source = WanfangSource(
        client=client,
        app_key=app_key,
        base_url=_BASE_URL,
        max_retries=max_retries,
        throttle=RequestThrottle(0.0),
        cache=cache or SearchCache(0.0),
        **kwargs,
    )
    try:
        yield source, captured
    finally:
        await client.aclose()


async def test_parses_chinese_periodical_document() -> None:
    async with _source(_json_handler(_payload(_document()))) as (source, _captured):
        records = await source.search(_search())

    assert len(records) == 1
    record = records[0]
    assert record.source_id == "wanfang:periodical_c61d1b0d"
    assert record.title == "数字化转型对企业创新绩效的影响"
    assert record.authors == ["张三", "李四"]
    assert record.abstract == "本文研究数字化转型……"
    assert record.published_at.year == 2023
    # 有 DOI 时统一成 doi.org 规范链接，让多源去重能跨源合并
    assert str(record.source_url) == "https://doi.org/10.3969/j.issn.1000-1239.2023.01.001"
    assert str(record.pdf_url) == str(record.source_url)
    assert record.categories == ["数字化转型", "创新绩效"]


async def test_parses_thesis_without_doi_uses_detail_url() -> None:
    thesis = _document(
        document_id="thesis_02f2e3",
        resource_type="Thesis",
        title=("面向智能制造的数字孪生建模方法研究",),
        doi="",
        publish_year=None,
        publish_date="2021-06-01",
        keywords=(),
    )
    async with _source(_json_handler(_payload(thesis))) as (source, _captured):
        records = await source.search(_search())

    record = records[0]
    assert str(record.source_url) == "https://d.wanfangdata.com.cn/thesis/thesis_02f2e3"
    assert record.published_at.date().isoformat() == "2021-06-01"
    assert record.authors == ["张三", "李四"]
    assert record.categories == []


async def test_request_contract_headers_and_payload() -> None:
    async with _source(_json_handler(_payload(_document()))) as (source, captured):
        await source.search(_search())

    assert len(captured) == 1
    request = captured[0]
    assert request.method == "POST"
    assert str(request.url) == f"{_BASE_URL}/openwanfang/getQuery"
    assert request.headers["X-App-Key"] == _APP_KEY
    body = json.loads(request.content)
    assert body["collections"] == list(DEFAULT_COLLECTIONS)
    assert body["query"] == "数字化转型 企业创新"
    assert body["rows"] == 10
    assert body["sort"] == {"sort_name": "OfflineScore"}


async def test_missing_app_key_raises_without_request() -> None:
    """BYOK：用户没配置万方 Key 时明确报错，而不是发一个必然 401 的请求。"""
    async with _source(_json_handler(_payload()), app_key="") as (source, captured):
        with pytest.raises(ValidationException):
            await source.search(_search())
    assert captured == []


async def test_date_range_is_filtered_locally() -> None:
    old = _document(document_id="periodical_old", publish_year=2015, doi="")
    recent = _document(document_id="periodical_new", publish_year=2023, doi="")
    async with _source(_json_handler(_payload(old, recent))) as (source, _captured):
        records = await source.search(_search(date_from="2020-01-01"))

    assert [record.source_id for record in records] == ["wanfang:periodical_new"]


async def test_retries_429_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0.1"}, content=b"{}")
        return httpx.Response(200, content=json.dumps(_payload(_document())))

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("app.tools.literature.wanfang._sleep", no_sleep)
    async with _source(handler, max_retries=1) as (source, _captured):
        records = await source.search(_search())

    assert calls["count"] == 2
    assert len(records) == 1


async def test_429_exhausted_raises_temporary() -> None:
    async with _source(_status_handler(429, headers={"Retry-After": "5"}), max_retries=1) as (
        source,
        _captured,
    ):
        with pytest.raises(UpstreamTemporaryError):
            await source.search(_search())


async def test_503_raises_temporary() -> None:
    async with _source(_status_handler(503)) as (source, _captured):
        with pytest.raises(UpstreamTemporaryError):
            await source.search(_search())


async def test_401_raises_upstream_error() -> None:
    async with _source(_status_handler(401)) as (source, _captured):
        with pytest.raises(UpstreamError):
            await source.search(_search())


async def test_402_insufficient_balance_raises_readable_error() -> None:
    """余额不足（402）也要给出「去设置页换 Key / 取消勾选万方」的可读提示。"""
    async with _source(_status_handler(402)) as (source, _captured):
        with pytest.raises(UpstreamError) as excinfo:
            await source.search(_search())

    assert "余额" in str(excinfo.value) or "设置页" in str(excinfo.value)


async def test_max_calls_budget_stops_upstream_requests() -> None:
    """费用护栏：单任务最多 5 次基础检索；超限后不再外呼、返回空列表。"""
    async with _source(_json_handler(_payload(_document())), max_calls=2) as (source, captured):
        first = await source.search(_search(query="数字化转型"))
        second = await source.search(_search(query="企业创新"))
        third = await source.search(_search(query="智能制造"))

    assert len(first) == 1
    assert len(second) == 1
    assert third == []
    assert len(captured) == 2
    assert source.calls == 2


async def test_cache_hits_do_not_call_upstream_twice() -> None:
    async with _source(_json_handler(_payload(_document())), cache=SearchCache(60.0)) as (
        source,
        captured,
    ):
        await source.search(_search())
        await source.search(_search())

    assert len(captured) == 1


async def test_document_without_id_raises_validation_error() -> None:
    broken = {"resource_type": "Periodical", "fields": {"Title": ["没有 Id"]}}
    async with _source(_json_handler(_payload(broken))) as (source, _captured):
        with pytest.raises(ValidationException):
            await source.search(_search())


def test_default_base_url_uses_https() -> None:
    """官方脚本写的是 http，但实测 http 监听直接 400；默认必须是 https。"""
    from app.tools.literature.wanfang import DEFAULT_BASE_URL

    assert DEFAULT_BASE_URL == "https://api.wfdata.com"


async def test_parses_legacy_flat_fields_shape() -> None:
    """兼容旧版/文档形状：字段直接放在 `fields` 里。"""
    legacy = {
        "resource_type": "Periodical",
        "fields": {
            "Id": "periodical_legacy",
            "Title": ["扁平字段形状"],
            "Creator": ["王五"],
            "PublishYear": 2022,
            "DOI": "10.3969/legacy.2022.001",
        },
    }
    async with _source(_json_handler(_payload(legacy))) as (source, _captured):
        records = await source.search(_search())

    assert len(records) == 1
    assert records[0].source_id == "wanfang:periodical_legacy"
    assert records[0].title == "扁平字段形状"
    assert str(records[0].source_url) == "https://doi.org/10.3969/legacy.2022.001"
