"""OA 正文抓取（Issue #73 Phase A）。

按 provider 链依次尝试，命中即止：
OpenAlex best_oa_location → arXiv HTML → arXiv PDF → Europe PMC JATS
→ Unpaywall → CORE（需 API key）。

安全与预算约束：

- 单篇 ``per_document_timeout_seconds`` / 整批 ``total_timeout_seconds`` 双层预算；
- 每次下载（含每一跳重定向）都过 SSRF 校验：仅 http/https，且解析结果必须是公网地址；
- 手动跟随重定向，最多 ``MAX_REDIRECTS`` 跳；
- 正文累计超过 30 MB 中止；content-type 必须与目标格式匹配，PDF 再校验 ``%PDF-`` 魔数；
- license 归一化后必须命中 ``FulltextConfig.license_whitelist``，否则拒绝该来源。
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import re
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote, urljoin, urlparse
from uuid import UUID, uuid4

import httpx
from pydantic import HttpUrl

from app.core.config import settings
from app.core.exceptions import ValidationException
from app.core.logging import get_logger
from app.knowledge.services.document_storage import DocumentStorage, StoredDocument
from app.tools.literature.arxiv import ArxivClient
from app.tools.literature.models import LiteratureRecord

logger = get_logger(__name__)

_MAX_BYTES = 30 * 1024 * 1024
_MAX_REDIRECTS = 3
_READ_CHUNK_SIZE = 1024 * 1024
_PDF_MAGIC = b"%PDF-"
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_CONTENT_TYPES: dict[str, frozenset[str]] = {
    "pdf": frozenset({"application/pdf", "application/octet-stream"}),
    "html": frozenset({"text/html", "application/xhtml+xml"}),
    "jats_xml": frozenset({"application/xml", "text/xml", "application/jats+xml"}),
}
_USER_AGENT = "VeriPaper-FulltextBot/1.0 (+https://veripaper.me)"
_ARXIV_HOSTS = frozenset({"arxiv.org", "www.arxiv.org", "export.arxiv.org"})
_DOI_URL_PATTERN = re.compile(r"doi\.org/(10\.\d{4,9}/[^\s?#]+)", re.IGNORECASE)
_DOI_ID_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$")
_PMID_PATTERN = re.compile(r"^pubmed:(\d+)$")
_ARXIV_ID_PATTERN = re.compile(r"(?P<id>\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?")


class LicenseNotAllowed(RuntimeError):
    """license 不在白名单内，该来源的正文不可落库。"""


class FullTextFetchError(RuntimeError):
    """正文下载失败（状态码 / 重定向 / 大小 / content-type / 魔数）。"""


@dataclass(slots=True)
class FullTextCandidate:
    """一条通过 license 闸门与下载校验的 OA 正文。"""

    provider: str
    source_url: str
    content: bytes
    content_format: str
    license: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


def _is_safe_url(url: str) -> bool:
    """仅接受 http/https 且所有解析结果都是公网地址的 URL（防 SSRF）。"""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except OSError:
        return False
    for info in infos:
        # IPv6 链路本地地址可能带 %scope 后缀，ip_address() 不接受
        address = str(info[4][0]).split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return False
    return True


def _normalize_license(value: Any) -> str:
    """把 license 标识归一化到白名单风格，例如 ``CC BY 4.0`` → ``cc-by-4.0``。"""
    if not isinstance(value, str):
        return ""
    normalized = " ".join(value.strip().lower().replace("_", "-").split())
    normalized = normalized.replace("cc by", "cc-by")
    return normalized.replace("cc-by ", "cc-by-")


def _record_doi(record: LiteratureRecord) -> str | None:
    """从 source_url 的 doi.org 链接或 source_id 中提取 DOI。"""
    match = _DOI_URL_PATTERN.search(str(record.source_url))
    if match:
        return match.group(1)
    source_id = record.source_id.strip()
    if _DOI_ID_PATTERN.fullmatch(source_id):
        return source_id
    return None


def _record_arxiv_id(record: LiteratureRecord) -> str | None:
    """从 arXiv 链接或 source_id 中提取不带版本号的 arXiv id。"""
    for raw_url in (str(record.pdf_url), str(record.source_url)):
        parsed = urlparse(raw_url)
        if parsed.hostname in _ARXIV_HOSTS:
            match = _ARXIV_ID_PATTERN.search(parsed.path)
            if match:
                return match.group("id")
    source_id = record.source_id.strip()
    if "/" in source_id:
        match = _ARXIV_ID_PATTERN.fullmatch(source_id)
        if match:
            return match.group("id")
    return None


def _record_pmid(record: LiteratureRecord) -> str | None:
    """从 ``pubmed:<pmid>`` 形式的 source_id 中提取 PMID。"""
    match = _PMID_PATTERN.fullmatch(record.source_id.strip())
    return match.group(1) if match else None


async def _download_content(
    client: httpx.AsyncClient,
    url: str,
    *,
    expected_format: str,
    max_bytes: int = _MAX_BYTES,
    max_redirects: int = _MAX_REDIRECTS,
) -> bytes:
    """下载正文；每一跳都重新做 SSRF 校验，并校验大小与 content-type。"""
    allowed_types = _CONTENT_TYPES[expected_format]
    current = url
    for _hop in range(max_redirects + 1):
        if not _is_safe_url(current):
            raise FullTextFetchError(f"URL 未通过 SSRF 校验：{current}")
        async with client.stream(
            "GET",
            current,
            headers={"User-Agent": _USER_AGENT},
            follow_redirects=False,
        ) as response:
            if response.status_code in _REDIRECT_STATUSES:
                location = response.headers.get("location")
                if not location:
                    raise FullTextFetchError(f"重定向缺少 Location：{current}")
                current = urljoin(current, location)
                continue
            if response.status_code == 429 or response.status_code >= 500:
                raise FullTextFetchError(
                    f"正文来源暂不可用（HTTP {response.status_code}）：{current}"
                )
            if response.status_code >= 400:
                raise FullTextFetchError(f"正文来源返回 HTTP {response.status_code}：{current}")
            declared = response.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > max_bytes:
                raise FullTextFetchError(f"正文超过大小上限（{max_bytes} 字节）：{current}")
            content_type = response.headers.get("content-type", "")
            content_type = content_type.split(";")[0].strip().lower()
            if content_type not in allowed_types:
                raise FullTextFetchError(
                    f"content-type 不匹配（期望 {expected_format}，"
                    f"实际 {content_type or '空'}）：{current}"
                )
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise FullTextFetchError(f"正文超过大小上限（{max_bytes} 字节）：{current}")
                chunks.append(chunk)
            content = b"".join(chunks)
            if not content:
                raise FullTextFetchError(f"正文为空：{current}")
            if expected_format == "pdf" and not content.startswith(_PDF_MAGIC):
                raise FullTextFetchError(f"PDF 魔数校验失败：{current}")
            return content
    raise FullTextFetchError(f"重定向次数超过上限（{max_redirects}）：{url}")


class _MemoryStorage:
    """把流式上传读进内存的最小存储替身，供 ``ArxivClient.download_pdf`` 复用。"""

    def __init__(self) -> None:
        self.content = b""

    async def save(self, user_id: UUID, upload: Any) -> StoredDocument:
        del user_id
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = await upload.read(_READ_CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > _MAX_BYTES:
                raise ValidationException(f"PDF 超过 {_MAX_BYTES // (1024 * 1024)} MB 限制")
            chunks.append(chunk)
        content = b"".join(chunks)
        if not content.startswith(_PDF_MAGIC):
            raise ValidationException("下载内容不是 PDF")
        self.content = content
        filename = str(upload.filename or "fulltext.pdf")
        return StoredDocument(
            path=Path(filename),
            original_name=filename,
            extension=".pdf",
            size=len(content),
        )


class FullTextResolver:
    """OA 正文解析器：按 provider 链依次尝试，单 provider 失败只降级、不中断。"""

    OPENALEX_API = "https://api.openalex.org"
    ARXIV_HTML = "https://arxiv.org/html"
    EUROPE_PMC_API = "https://www.ebi.ac.uk/europepmc/webservices/rest"
    UNPAYWALL_API = "https://api.unpaywall.org/v2"
    CORE_API = "https://api.core.ac.uk/v3/search/works"
    MAX_BYTES = _MAX_BYTES
    MAX_REDIRECTS = _MAX_REDIRECTS

    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        # 读模块级 settings：与端点一致，避免 get_settings() 缓存清理后对象分叉
        self._settings = settings.fulltext
        self._client = client
        self._owns_client = client is None

    @property
    def client(self) -> httpx.AsyncClient:
        """惰性创建内部客户端（注入的客户端由调用方负责关闭）。"""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0), follow_redirects=False)
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    # ===== license 闸门 =====

    def ensure_license(self, license_id: str) -> str:
        """归一化后必须命中白名单，否则抛 :class:`LicenseNotAllowed`。"""
        normalized = _normalize_license(license_id)
        whitelist = {_normalize_license(item) for item in self._settings.license_whitelist}
        if not normalized or normalized not in whitelist:
            raise LicenseNotAllowed(f"license 不在白名单：{license_id!r}")
        return normalized

    @staticmethod
    def _raise_for_status(response: httpx.Response, operation: str) -> None:
        if response.status_code < 400:
            return
        if response.status_code == 429 or response.status_code >= 500:
            raise FullTextFetchError(f"{operation} 暂不可用（HTTP {response.status_code}）")
        raise FullTextFetchError(f"{operation} 返回 HTTP {response.status_code}")

    # ===== provider 链（命中即止） =====

    async def _openalex_oa(self, record: LiteratureRecord) -> FullTextCandidate | None:
        doi = _record_doi(record)
        if doi is None:
            return None
        params: dict[str, str] = {}
        contact_email = settings.reference_verification.contact_email
        if contact_email:
            params["mailto"] = contact_email
        response = await self.client.get(
            f"{self.OPENALEX_API}/works/https://doi.org/{doi}", params=params
        )
        if response.status_code == 404:
            return None
        self._raise_for_status(response, "OpenAlex")
        payload = response.json()
        if not isinstance(payload, dict):
            return None
        location = payload.get("best_oa_location")
        if not isinstance(location, dict):
            return None
        pdf_url = location.get("pdf_url")
        if not isinstance(pdf_url, str) or not pdf_url:
            return None
        license_id = self.ensure_license(_normalize_license(location.get("license")))
        content = await _download_content(self.client, pdf_url, expected_format="pdf")
        return FullTextCandidate(
            provider="openalex_oa",
            source_url=pdf_url,
            content=content,
            content_format="pdf",
            license=license_id,
        )

    async def _arxiv_html(self, record: LiteratureRecord) -> FullTextCandidate | None:
        arxiv_id = _record_arxiv_id(record)
        if arxiv_id is None:
            return None
        license_id = self.ensure_license("arxiv")
        url = f"{self.ARXIV_HTML}/{arxiv_id}"
        content = await _download_content(self.client, url, expected_format="html")
        return FullTextCandidate(
            provider="arxiv_html",
            source_url=url,
            content=content,
            content_format="html",
            license=license_id,
        )

    async def _arxiv_pdf(self, record: LiteratureRecord) -> FullTextCandidate | None:
        arxiv_id = _record_arxiv_id(record)
        if arxiv_id is None:
            return None
        license_id = self.ensure_license("arxiv")
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"
        storage = _MemoryStorage()
        await ArxivClient(client=self.client).download_pdf(
            record.model_copy(update={"pdf_url": HttpUrl(pdf_url)}),
            cast(DocumentStorage, storage),
            uuid4(),
        )
        return FullTextCandidate(
            provider="arxiv_pdf",
            source_url=pdf_url,
            content=storage.content,
            content_format="pdf",
            license=license_id,
        )

    async def _europe_pmc(self, record: LiteratureRecord) -> FullTextCandidate | None:
        doi = _record_doi(record)
        pmid = _record_pmid(record)
        if doi is not None:
            query = f'DOI:"{doi}"'
        elif pmid is not None:
            query = f"EXT_ID:{pmid} AND SRC:MED"
        else:
            return None
        search_response = await self.client.get(
            f"{self.EUROPE_PMC_API}/search",
            params={
                "query": query,
                "format": "json",
                "resultType": "core",
                "pageSize": "1",
            },
        )
        if search_response.status_code == 404:
            return None
        self._raise_for_status(search_response, "Europe PMC")
        payload = search_response.json()
        if not isinstance(payload, dict):
            return None
        result_list = payload.get("resultList")
        if not isinstance(result_list, dict):
            return None
        results = result_list.get("result")
        if not isinstance(results, list) or not results:
            return None
        item = results[0]
        if not isinstance(item, dict):
            return None
        pmcid = item.get("pmcid")
        if not isinstance(pmcid, str) or not pmcid:
            return None
        if str(item.get("isOpenAccess", "")).upper() != "Y":
            return None
        license_id = self.ensure_license(_normalize_license(item.get("license")) or "pmc-oa")
        url = f"{self.EUROPE_PMC_API}/{pmcid}/fullTextXML"
        content = await _download_content(self.client, url, expected_format="jats_xml")
        return FullTextCandidate(
            provider="europe_pmc",
            source_url=url,
            content=content,
            content_format="jats_xml",
            license=license_id,
        )

    async def _unpaywall(self, record: LiteratureRecord) -> FullTextCandidate | None:
        email = self._settings.unpaywall_email
        doi = _record_doi(record)
        if not email or doi is None:
            return None
        response = await self.client.get(
            f"{self.UNPAYWALL_API}/{quote(doi, safe='/')}", params={"email": email}
        )
        if response.status_code == 404:
            return None
        self._raise_for_status(response, "Unpaywall")
        payload = response.json()
        if not isinstance(payload, dict):
            return None
        location = payload.get("best_oa_location") or payload.get("first_oa_location")
        if not isinstance(location, dict):
            return None
        pdf_url = location.get("url_for_pdf")
        if not isinstance(pdf_url, str) or not pdf_url:
            return None
        license_id = self.ensure_license(_normalize_license(location.get("license")))
        content = await _download_content(self.client, pdf_url, expected_format="pdf")
        return FullTextCandidate(
            provider="unpaywall",
            source_url=pdf_url,
            content=content,
            content_format="pdf",
            license=license_id,
        )

    async def _core(self, record: LiteratureRecord) -> FullTextCandidate | None:
        api_key = self._settings.core_api_key
        doi = _record_doi(record)
        if not api_key or doi is None:
            return None
        response = await self.client.get(
            self.CORE_API,
            params={"q": f'doi:"{doi}"', "limit": "1"},
            headers={"Authorization": f"Bearer {api_key}"},
        )
        if response.status_code == 404:
            return None
        self._raise_for_status(response, "CORE")
        payload = response.json()
        if not isinstance(payload, dict):
            return None
        results = payload.get("results")
        if not isinstance(results, list) or not results:
            return None
        item = results[0]
        if not isinstance(item, dict):
            return None
        pdf_url = item.get("downloadUrl")
        if not isinstance(pdf_url, str) or not pdf_url:
            return None
        license_id = self.ensure_license(_normalize_license(item.get("license")))
        content = await _download_content(self.client, pdf_url, expected_format="pdf")
        return FullTextCandidate(
            provider="core",
            source_url=pdf_url,
            content=content,
            content_format="pdf",
            license=license_id,
        )

    async def _resolve_one(
        self, record: LiteratureRecord
    ) -> tuple[LiteratureRecord, FullTextCandidate] | None:
        """单篇文献：逐 provider 尝试，任一命中即返回；单 provider 超时/失败只降级。"""
        per_document_timeout = self._settings.per_document_timeout_seconds
        for fetch in (
            self._openalex_oa,
            self._arxiv_html,
            self._arxiv_pdf,
            self._europe_pmc,
            self._unpaywall,
            self._core,
        ):
            try:
                async with asyncio.timeout(per_document_timeout):
                    candidate = await fetch(record)
            except TimeoutError:
                logger.warning(
                    "fulltext_provider_timeout",
                    source_id=record.source_id,
                    per_document_timeout_seconds=per_document_timeout,
                )
                continue
            except LicenseNotAllowed as exc:
                logger.warning(
                    "fulltext_license_rejected",
                    source_id=record.source_id,
                    error=str(exc),
                )
                continue
            except Exception as exc:  # 单 provider 失败必须降级到下一个来源
                logger.warning(
                    "fulltext_provider_failed",
                    source_id=record.source_id,
                    error=str(exc),
                )
                continue
            if candidate is not None:
                return (record, candidate)
        return None

    async def resolve(
        self, records: list[LiteratureRecord]
    ) -> list[tuple[LiteratureRecord, FullTextCandidate]]:
        """批量解析：并发受限、整批限时，返回按输入顺序去重（sha256）后的命中结果。"""
        if not self._settings.enabled or not records:
            return []
        semaphore = asyncio.Semaphore(max(1, self._settings.max_concurrency))
        total_timeout = self._settings.total_timeout_seconds

        async def _guarded(
            record: LiteratureRecord,
        ) -> tuple[LiteratureRecord, FullTextCandidate] | None:
            async with semaphore:
                try:
                    return await self._resolve_one(record)
                except Exception as exc:  # 单篇失败不能拖垮整批
                    logger.warning(
                        "fulltext_document_failed",
                        source_id=record.source_id,
                        error=str(exc),
                    )
                    return None

        tasks: list[asyncio.Task[tuple[LiteratureRecord, FullTextCandidate] | None]] = [
            asyncio.create_task(_guarded(record)) for record in records
        ]
        indexes = {id(task): index for index, task in enumerate(tasks)}
        hits: list[tuple[int, tuple[LiteratureRecord, FullTextCandidate]]] = []
        try:
            async with asyncio.timeout(total_timeout):
                pending = set(tasks)
                while pending:
                    done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        resolved = task.result()
                        if resolved is not None:
                            hits.append((indexes[id(task)], resolved))
        except TimeoutError:
            logger.warning(
                "fulltext_total_timeout",
                total_timeout_seconds=total_timeout,
                documents=len(records),
            )
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        hits.sort(key=lambda item: item[0])
        deduped: list[tuple[LiteratureRecord, FullTextCandidate]] = []
        seen: set[str] = set()
        for _, (record, candidate) in hits:
            digest = candidate.sha256
            if digest in seen:
                continue
            seen.add(digest)
            deduped.append((record, candidate))
        return deduped
