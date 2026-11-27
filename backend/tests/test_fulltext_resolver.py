"""OA 正文 resolver 测试（Issue #73 Phase A / 实施计划 Task 4）。

全部外部请求经 ``httpx.MockTransport``，DNS 由 autouse fixture 假解析，**零真实网络**。
覆盖：license 白名单闸门与归一化、SSRF 防护（私网/环回/链路本地/非 http/https/DNS 失败）、
provider 降级顺序（超时 / HTTP 错误 / license 拒绝）、单篇与整批超时预算、
重定向上限、30 MB 上限、content-type 校验、同批 sha256 去重、未配置 provider 跳过。
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import socket
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app.core.config import settings
from app.tools.literature.fulltext_resolver import (
    FullTextCandidate,
    FullTextFetchError,
    FullTextResolver,
    LicenseNotAllowed,
    _download_content,
    _is_safe_url,
)
from app.tools.literature.models import LiteratureRecord

PDF_BYTES = b"%PDF-1.7\n" + b"fulltext-evidence" * 64
HTML_BYTES = b"<html><body><h1>Introduction</h1><p>full text</p></body></html>"
JATS_BYTES = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b"<article><body><sec><title>Methods</title><p>JATS full text</p></sec></body></article>"
)
# Fastly 的公网地址：用于替代真实 DNS，避免测试依赖网络
PUBLIC_IP = "151.101.0.223"
ARXIV_ID = "2401.00001"
REPOSITORY_URL = "https://repository.example.org/paper.pdf"


@pytest.fixture(autouse=True)
def _hermetic_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """禁止真实 DNS：普通主机名一律解析到公网地址，IP 字面量走真实解析。"""
    real_getaddrinfo = socket.getaddrinfo

    def _fake_getaddrinfo(host: str, port: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, 0))]
        return real_getaddrinfo(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)


def _record(**overrides: Any) -> LiteratureRecord:
    """构造一条合法文献记录；``published_at`` 为必填字段（计划骨架缺失，见 notes）。"""
    base: dict[str, Any] = {
        "source_id": "10.1234/abc",
        "title": "A Test Paper",
        "authors": ["A"],
        "abstract": "abstract",
        "published_at": datetime(2024, 1, 15, tzinfo=UTC),
        "source_url": "https://example.org/paper",
        "pdf_url": f"https://arxiv.org/pdf/{ARXIV_ID}",
        "categories": [],
    }
    base.update(overrides)
    return LiteratureRecord.model_validate(base)


def _arxiv_record(arxiv_id: str = ARXIV_ID) -> LiteratureRecord:
    return _record(
        source_id=arxiv_id,
        source_url=f"https://arxiv.org/abs/{arxiv_id}",
        pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
    )


def _mock_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        timeout=httpx.Timeout(5.0),
    )


# ===== license 闸门 =====


def test_license_gate_rejects_non_whitelisted() -> None:
    resolver = FullTextResolver(
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    )
    with pytest.raises(LicenseNotAllowed):
        resolver.ensure_license("publisher-specific-oa")


def test_license_gate_accepts_whitelisted_and_normalizes() -> None:
    resolver = FullTextResolver()
    # 大小写 / 空格 / 下划线差异都要能归一化到白名单值
    assert resolver.ensure_license("CC BY 4.0") == "cc-by-4.0"
    assert resolver.ensure_license("arXiv") == "arxiv"
    assert resolver.ensure_license("cc0") == "cc0"
    with pytest.raises(LicenseNotAllowed):
        resolver.ensure_license("")
    with pytest.raises(LicenseNotAllowed):
        resolver.ensure_license("   ")


# ===== SSRF 防护 =====


def test_ssrf_guard_blocks_private_and_loopback() -> None:
    assert _is_safe_url("https://arxiv.org/pdf/1") is True
    assert _is_safe_url("http://127.0.0.1/x.pdf") is False
    assert _is_safe_url("http://169.254.169.254/latest/meta-data") is False
    assert _is_safe_url("file:///etc/passwd") is False


def test_ssrf_guard_rejects_unresolvable_host(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise_getaddrinfo(host: str, port: Any, *args: Any, **kwargs: Any) -> Any:
        raise socket.gaierror("name resolution failed")

    monkeypatch.setattr(socket, "getaddrinfo", _raise_getaddrinfo)
    assert _is_safe_url("https://does-not-exist.invalid/paper.pdf") is False


# ===== provider 链降级 =====


async def test_provider_falls_back_on_timeout_then_succeeds() -> None:
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(str(request.url))
        if request.url.path.startswith("/html/"):
            raise httpx.ReadTimeout("mock html timeout", request=request)
        if request.url.path.startswith("/pdf/"):
            return httpx.Response(
                200, content=PDF_BYTES, headers={"content-type": "application/pdf"}
            )
        return httpx.Response(404)

    client = _mock_client(handler)
    try:
        resolved = await FullTextResolver(client=client).resolve([_arxiv_record()])
    finally:
        await client.aclose()

    assert captured == [
        f"https://arxiv.org/html/{ARXIV_ID}",
        f"https://arxiv.org/pdf/{ARXIV_ID}",
    ]
    assert len(resolved) == 1
    record, candidate = resolved[0]
    assert record.source_id == ARXIV_ID
    assert candidate.provider == "arxiv_pdf"
    assert candidate.source_url == f"https://arxiv.org/pdf/{ARXIV_ID}"
    assert candidate.content == PDF_BYTES
    assert candidate.content_format == "pdf"
    assert candidate.license == "arxiv"
    assert candidate.sha256 == hashlib.sha256(PDF_BYTES).hexdigest()


async def test_provider_http_error_falls_back_to_next_provider() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/html/"):
            return httpx.Response(429)
        if request.url.path.startswith("/pdf/"):
            return httpx.Response(
                200, content=PDF_BYTES, headers={"content-type": "application/pdf"}
            )
        return httpx.Response(404)

    client = _mock_client(handler)
    try:
        resolved = await FullTextResolver(client=client).resolve([_arxiv_record()])
    finally:
        await client.aclose()

    assert len(resolved) == 1
    assert resolved[0][1].provider == "arxiv_pdf"


async def test_license_rejected_provider_falls_back_to_next(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.fulltext, "unpaywall_email", "resolver-test@example.org")
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        captured.append(host)
        if host == "api.openalex.org":
            return httpx.Response(
                200,
                json={
                    "best_oa_location": {
                        "pdf_url": REPOSITORY_URL,
                        "license": "publisher-specific-oa",
                    }
                },
            )
        if host == "api.unpaywall.org":
            return httpx.Response(
                200,
                json={
                    "best_oa_location": {
                        "url_for_pdf": REPOSITORY_URL,
                        "license": "cc-by",
                    }
                },
            )
        if host == "repository.example.org":
            return httpx.Response(
                200, content=PDF_BYTES, headers={"content-type": "application/pdf"}
            )
        return httpx.Response(404)

    record = _record(pdf_url="https://example.org/paper.pdf")
    client = _mock_client(handler)
    try:
        resolved = await FullTextResolver(client=client).resolve([record])
    finally:
        await client.aclose()

    assert len(resolved) == 1
    _, candidate = resolved[0]
    assert candidate.provider == "unpaywall"
    assert candidate.source_url == REPOSITORY_URL
    assert candidate.license == "cc-by"
    assert candidate.content == PDF_BYTES
    # OpenAlex 的 license 被拒后不再下载其正文，且 Unpaywall 命中的正文只下载一次
    assert captured[0] == "api.openalex.org"
    assert captured.count("repository.example.org") == 1
    assert captured.index("api.openalex.org") < captured.index("api.unpaywall.org")


async def test_europe_pmc_provider_returns_jats() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "api.openalex.org":
            return httpx.Response(404)
        if host == "www.ebi.ac.uk" and request.url.path.endswith("/search"):
            return httpx.Response(
                200,
                json={
                    "resultList": {
                        "result": [
                            {
                                "pmcid": "PMC1234567",
                                "isOpenAccess": "Y",
                                "license": "CC BY",
                            }
                        ]
                    }
                },
            )
        if host == "www.ebi.ac.uk" and request.url.path.endswith("/fullTextXML"):
            return httpx.Response(
                200, content=JATS_BYTES, headers={"content-type": "application/xml"}
            )
        return httpx.Response(404)

    record = _record(pdf_url="https://example.org/paper.pdf")
    client = _mock_client(handler)
    try:
        resolved = await FullTextResolver(client=client).resolve([record])
    finally:
        await client.aclose()

    assert len(resolved) == 1
    _, candidate = resolved[0]
    assert candidate.provider == "europe_pmc"
    assert candidate.content == JATS_BYTES
    assert candidate.content_format == "jats_xml"
    assert candidate.license == "cc-by"
    assert candidate.source_url == (
        "https://www.ebi.ac.uk/europepmc/webservices/rest/PMC1234567/fullTextXML"
    )


# ===== 超时预算 =====


async def test_per_document_timeout_moves_to_next_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _slow_openalex(self: FullTextResolver, record: LiteratureRecord) -> None:
        await asyncio.sleep(5.0)
        return None

    monkeypatch.setattr(FullTextResolver, "_openalex_oa", _slow_openalex)
    monkeypatch.setattr(settings.fulltext, "per_document_timeout_seconds", 0.3)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/html/"):
            return httpx.Response(200, content=HTML_BYTES, headers={"content-type": "text/html"})
        return httpx.Response(404)

    client = _mock_client(handler)
    try:
        resolved = await FullTextResolver(client=client).resolve([_record()])
    finally:
        await client.aclose()

    assert len(resolved) == 1
    _, candidate = resolved[0]
    assert candidate.provider == "arxiv_html"
    assert candidate.content == HTML_BYTES
    assert candidate.content_format == "html"


async def test_total_timeout_keeps_completed_results(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _slow_openalex(
        self: FullTextResolver, record: LiteratureRecord
    ) -> FullTextCandidate | None:
        # 只让非 arXiv 记录卡在首个 provider 上；arXiv 记录继续走 arXiv HTML 快速命中。
        if record.source_id != ARXIV_ID:
            await asyncio.sleep(5.0)
        return None

    monkeypatch.setattr(FullTextResolver, "_openalex_oa", _slow_openalex)
    monkeypatch.setattr(settings.fulltext, "per_document_timeout_seconds", 5.0)
    monkeypatch.setattr(settings.fulltext, "total_timeout_seconds", 0.5)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/html/"):
            return httpx.Response(200, content=HTML_BYTES, headers={"content-type": "text/html"})
        return httpx.Response(404)

    fast = _arxiv_record()
    slow = _record(
        source_id="10.1234/slow",
        source_url="https://example.org/slow",
        pdf_url="https://example.org/slow.pdf",
    )
    client = _mock_client(handler)
    try:
        resolved = await FullTextResolver(client=client).resolve([fast, slow])
    finally:
        await client.aclose()

    # 总预算耗尽时已完成的结果仍要返回，未完成的被取消
    assert len(resolved) == 1
    record, candidate = resolved[0]
    assert record.source_id == ARXIV_ID
    assert candidate.provider == "arxiv_html"


# ===== 下载安全 / 预算 =====


async def test_download_rejects_redirect_chain_beyond_limit() -> None:
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://example.org/next.pdf"})

    client = _mock_client(handler)
    try:
        with pytest.raises(FullTextFetchError, match="重定向"):
            await _download_content(client, "https://example.org/start.pdf", expected_format="pdf")
    finally:
        await client.aclose()

    assert len(captured) == FullTextResolver.MAX_REDIRECTS + 1


async def test_download_rejects_oversized_response() -> None:
    big = b"%PDF-" + b"x" * 64

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=big, headers={"content-type": "application/pdf"})

    client = _mock_client(handler)
    try:
        with pytest.raises(FullTextFetchError, match="大小上限"):
            await _download_content(
                client,
                "https://example.org/big.pdf",
                expected_format="pdf",
                max_bytes=32,
            )
    finally:
        await client.aclose()


async def test_download_rejects_mismatched_content_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=HTML_BYTES, headers={"content-type": "text/html; charset=utf-8"}
        )

    client = _mock_client(handler)
    try:
        with pytest.raises(FullTextFetchError, match="content-type"):
            await _download_content(client, "https://example.org/paper.pdf", expected_format="pdf")
    finally:
        await client.aclose()


# ===== 批处理 =====


async def test_batch_deduplicates_identical_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/pdf/"):
            return httpx.Response(
                200, content=PDF_BYTES, headers={"content-type": "application/pdf"}
            )
        return httpx.Response(404)

    records = [_arxiv_record("2401.00001"), _arxiv_record("2401.00002")]
    client = _mock_client(handler)
    try:
        resolved = await FullTextResolver(client=client).resolve(records)
    finally:
        await client.aclose()

    # 两份字节完全相同的正文按 sha256 去重，只保留一条
    assert len(resolved) == 1
    assert resolved[0][1].sha256 == hashlib.sha256(PDF_BYTES).hexdigest()


async def test_optional_providers_skipped_without_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.fulltext, "unpaywall_email", "")
    monkeypatch.setattr(settings.fulltext, "core_api_key", "")
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.url.host)
        return httpx.Response(404)

    record = _record(pdf_url="https://example.org/paper.pdf")
    client = _mock_client(handler)
    try:
        resolved = await FullTextResolver(client=client).resolve([record])
    finally:
        await client.aclose()

    assert resolved == []
    assert "api.unpaywall.org" not in captured
    assert "api.core.ac.uk" not in captured
    assert captured == ["api.openalex.org", "www.ebi.ac.uk"]


async def test_resolve_skips_all_providers_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.fulltext, "enabled", False)
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(str(request.url))
        return httpx.Response(200, content=PDF_BYTES, headers={"content-type": "application/pdf"})

    client = _mock_client(handler)
    try:
        resolved = await FullTextResolver(client=client).resolve([_record()])
    finally:
        await client.aclose()

    assert resolved == []
    assert captured == []
