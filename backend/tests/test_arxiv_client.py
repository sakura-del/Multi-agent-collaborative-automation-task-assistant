"""arXiv 检索适配器测试（Plan 3 Task 1）。

覆盖：
- Atom 解析与字段归一化（标题/摘要空白折叠、作者、UTC 发布时间、arXiv ID、URL）；
- 本地日期过滤、超时与 5xx → `UpstreamTemporaryError`；
- 下载主机白名单（仅 HTTPS arxiv.org / export.arxiv.org）、重定向逐跳校验；
- 落盘到 `DocumentStorage`：PDF 签名、30MB 上限、非 PDF 拒绝；
- 离线清单回退只捕获 `UpstreamTemporaryError`，schema/格式错误必须上抛。
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError as PydanticValidationError

from app.core.exceptions import UpstreamTemporaryError, ValidationException
from app.knowledge.services.document_storage import DocumentStorage
from app.tools.literature.arxiv import (
    ArxivClient,
    load_offline_manifest,
    search_with_offline_fallback,
)
from app.tools.literature.models import LiteratureRecord, LiteratureSearch

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "arxiv_response.xml"
ATOM_XML = FIXTURE_PATH.read_text(encoding="utf-8")
PDF_BYTES = b"%PDF-1.7\n" + b"evidence" * 512


def _record(**overrides: Any) -> LiteratureRecord:
    payload: dict[str, Any] = {
        "source_id": "2401.12345v1",
        "title": "Retrieval-Augmented Generation for Evidence Tracing",
        "authors": ["Alice Zhang"],
        "abstract": "abstract",
        "published_at": datetime(2024, 1, 15, 12, 0, tzinfo=UTC),
        "source_url": "https://arxiv.org/abs/2401.12345v1",
        "pdf_url": "https://arxiv.org/pdf/2401.12345v1",
        "categories": ["cs.CL"],
    }
    payload.update(overrides)
    return LiteratureRecord.model_validate(payload)


def _mock_client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        timeout=httpx.Timeout(5.0),
    )


def _atom_handler(captured: list[httpx.Request]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            text=ATOM_XML,
            headers={"content-type": "application/atom+xml"},
        )

    return handler


class TestSearchParsing:
    async def test_normalizes_atom_entries(self) -> None:
        captured: list[httpx.Request] = []
        client = _mock_client(_atom_handler(captured))
        try:
            records = await ArxivClient(client=client).search(
                LiteratureSearch(query="retrieval augmented generation", max_results=5)
            )
        finally:
            await client.aclose()

        assert len(records) == 2
        first = records[0]
        assert first.source_id == "2401.12345v1"
        assert first.title == "Retrieval-Augmented Generation for Evidence Tracing"
        assert first.authors == ["Alice Zhang", "Bob Li"]
        assert first.published_at == datetime(2024, 1, 15, 12, 0, tzinfo=UTC)
        assert str(first.source_url) == "https://arxiv.org/abs/2401.12345v1"
        assert str(first.pdf_url) == "https://arxiv.org/pdf/2401.12345v1"
        assert first.categories == ["cs.CL", "cs.IR"]
        assert first.abstract == (
            "We study how retrieval-augmented generation traces evidence across documents."
        )

        request = captured[0]
        assert request.url.path == "/api/query"
        assert request.url.params["search_query"] == ("all:retrieval augmented generation")
        assert request.url.params["start"] == "0"
        assert request.url.params["max_results"] == "5"
        assert request.url.params["sortBy"] == "relevance"
        assert request.url.params["sortOrder"] == "descending"

    async def test_filters_dates_locally(self) -> None:
        client = _mock_client(_atom_handler([]))
        try:
            records = await ArxivClient(client=client).search(
                LiteratureSearch(
                    query="retrieval augmented generation",
                    date_from=date(2024, 1, 1),
                    date_to=date(2024, 12, 31),
                )
            )
        finally:
            await client.aclose()

        assert [record.source_id for record in records] == ["2401.12345v1"]


class TestUpstreamFailures:
    async def test_timeout_raises_temporary_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("read timed out", request=request)

        client = _mock_client(handler)
        try:
            with pytest.raises(UpstreamTemporaryError):
                await ArxivClient(client=client).search(
                    LiteratureSearch(query="retrieval augmented generation")
                )
        finally:
            await client.aclose()

    async def test_server_error_raises_temporary_error(self) -> None:
        client = _mock_client(lambda request: httpx.Response(503, text="busy"))
        try:
            with pytest.raises(UpstreamTemporaryError):
                await ArxivClient(client=client).search(
                    LiteratureSearch(query="retrieval augmented generation")
                )
        finally:
            await client.aclose()

    async def test_malformed_feed_raises_validation_error(self) -> None:
        client = _mock_client(lambda request: httpx.Response(200, text="<not-atom/>"))
        try:
            with pytest.raises(ValidationException):
                await ArxivClient(client=client).search(
                    LiteratureSearch(query="retrieval augmented generation")
                )
        finally:
            await client.aclose()

    async def test_query_schema_is_enforced_before_request(self) -> None:
        captured: list[httpx.Request] = []
        client = _mock_client(_atom_handler(captured))
        try:
            with pytest.raises(PydanticValidationError):
                await ArxivClient(client=client).search(LiteratureSearch(query="x"))
        finally:
            await client.aclose()
        assert captured == []


class TestDownloadRestrictions:
    async def test_rejects_non_arxiv_host_without_request(self, tmp_path: Path) -> None:
        captured: list[httpx.Request] = []
        client = _mock_client(_atom_handler(captured))
        storage = DocumentStorage(tmp_path / "uploads", max_file_bytes=30 * 1024 * 1024)
        try:
            with pytest.raises(ValidationException):
                await ArxivClient(client=client).download_pdf(
                    _record(pdf_url="https://evil.example.com/paper.pdf"),
                    storage,
                    uuid4(),
                )
        finally:
            await client.aclose()
        assert captured == []

    async def test_rejects_plain_http_host(self, tmp_path: Path) -> None:
        client = _mock_client(lambda request: httpx.Response(200, content=PDF_BYTES))
        storage = DocumentStorage(tmp_path / "uploads", max_file_bytes=30 * 1024 * 1024)
        try:
            with pytest.raises(ValidationException):
                await ArxivClient(client=client).download_pdf(
                    _record(pdf_url="http://arxiv.org/pdf/2401.12345v1"),
                    storage,
                    uuid4(),
                )
        finally:
            await client.aclose()

    async def test_revalidates_redirect_host(self, tmp_path: Path) -> None:
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(302, headers={"location": "https://evil.example.com/paper.pdf"})

        client = _mock_client(handler)
        storage = DocumentStorage(tmp_path / "uploads", max_file_bytes=30 * 1024 * 1024)
        try:
            with pytest.raises(ValidationException):
                await ArxivClient(client=client).download_pdf(_record(), storage, uuid4())
        finally:
            await client.aclose()
        assert len(captured) == 1

    async def test_rejects_redirect_loop_beyond_limit(self, tmp_path: Path) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"location": "https://arxiv.org/pdf/2401.12345v1"})

        client = _mock_client(handler)
        storage = DocumentStorage(tmp_path / "uploads", max_file_bytes=30 * 1024 * 1024)
        try:
            with pytest.raises(ValidationException):
                await ArxivClient(client=client).download_pdf(_record(), storage, uuid4())
        finally:
            await client.aclose()


class TestDownloadPersistence:
    async def test_streams_pdf_into_storage(self, tmp_path: Path) -> None:
        client = _mock_client(lambda request: httpx.Response(200, content=PDF_BYTES))
        storage = DocumentStorage(tmp_path / "uploads", max_file_bytes=30 * 1024 * 1024)
        user_id = uuid4()
        try:
            stored = await ArxivClient(client=client).download_pdf(_record(), storage, user_id)
        finally:
            await client.aclose()

        assert stored.size == len(PDF_BYTES)
        assert stored.extension == ".pdf"
        assert stored.path.read_bytes() == PDF_BYTES
        assert stored.path.parent == (tmp_path / "uploads" / str(user_id)).resolve()

    async def test_rejects_non_pdf_signature(self, tmp_path: Path) -> None:
        client = _mock_client(lambda request: httpx.Response(200, content=b"<html>no</html>"))
        storage = DocumentStorage(tmp_path / "uploads", max_file_bytes=30 * 1024 * 1024)
        try:
            with pytest.raises(ValidationException):
                await ArxivClient(client=client).download_pdf(_record(), storage, uuid4())
        finally:
            await client.aclose()
        assert list((tmp_path / "uploads").rglob("*.pdf")) == []

    async def test_enforces_size_limit(self, tmp_path: Path) -> None:
        client = _mock_client(
            lambda request: httpx.Response(200, content=b"%PDF-1.7\n" + b"x" * 4096)
        )
        storage = DocumentStorage(tmp_path / "uploads", max_file_bytes=1024)
        try:
            with pytest.raises(ValidationException):
                await ArxivClient(client=client).download_pdf(_record(), storage, uuid4())
        finally:
            await client.aclose()


class TestOfflineFallback:
    def _manifest(self, tmp_path: Path) -> Path:
        payload = {
            "records": [
                {
                    **_record().model_dump(mode="json"),
                    "local_path": "files/2401.12345v1.pdf",
                    "sha256": "0" * 64,
                },
                {
                    **_record(
                        source_id="2212.09999v2",
                        title="Older Multi-Agent Survey",
                        published_at=datetime(2022, 12, 20, 8, 30, tzinfo=UTC),
                    ).model_dump(mode="json"),
                    "local_path": "files/2212.09999v2.pdf",
                    "sha256": "1" * 64,
                },
            ]
        }
        path = tmp_path / "manifest.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    async def test_falls_back_only_on_temporary_error(self, tmp_path: Path) -> None:
        manifest = self._manifest(tmp_path)

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("read timed out", request=request)

        client = _mock_client(handler)
        try:
            records = await search_with_offline_fallback(
                ArxivClient(client=client),
                LiteratureSearch(query="retrieval augmented generation"),
                manifest,
            )
        finally:
            await client.aclose()

        assert [record.source_id for record in records] == [
            "2401.12345v1",
            "2212.09999v2",
        ]

    async def test_does_not_fall_back_on_schema_error(self, tmp_path: Path) -> None:
        manifest = self._manifest(tmp_path)
        client = _mock_client(lambda request: httpx.Response(200, text="<not-atom/>"))
        try:
            with pytest.raises(ValidationException):
                await search_with_offline_fallback(
                    ArxivClient(client=client),
                    LiteratureSearch(query="retrieval augmented generation"),
                    manifest,
                )
        finally:
            await client.aclose()

    def test_manifest_schema_is_strict(self, tmp_path: Path) -> None:
        path = tmp_path / "manifest.json"
        path.write_text(
            json.dumps({"records": [{"title": "missing required fields"}]}),
            encoding="utf-8",
        )
        with pytest.raises(ValidationException):
            load_offline_manifest(path)
