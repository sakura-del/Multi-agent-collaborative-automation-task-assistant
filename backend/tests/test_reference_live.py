"""引用真实性核验的真实网络验收测试（Issue #3 验收标准）。

**默认不跑**：只有设置 `MAA_REFERENCE_LIVE=1` 时才执行，避免离线门禁产生真实外呼。
本地与服务器均可直连 `api.crossref.org` / `api.openalex.org`（见计划 §2.8 探针结论）。

运行：
    $env:MAA_REFERENCE_LIVE="1"
    .\\.venv\\Scripts\\python.exe -m pytest tests/test_reference_live.py -q --no-cov --basetemp="$env:TEMP\\pytest-dsh-live"

覆盖验收项：
- 真实 DOI `10.2196/80371` → 「已验证」
- 伪造 DOI → 「未找到」
- 真实 DOI + 被改错的标题 → 「元数据不符」
- 无 DOI 的 arXiv 文献走标题检索 → 「已验证」
- 外部源不可达 → 「未验证」（降级，不判失败）
"""

from __future__ import annotations

import os

import httpx
import pytest

from app.knowledge.services.reference_verifier import ReferenceVerifier
from app.tools.literature.crossref import CrossrefClient
from app.tools.literature.models import ReferenceCandidate, ReferenceVerdict
from app.tools.literature.openalex import OpenAlexClient

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        os.getenv("MAA_REFERENCE_LIVE") != "1",
        reason="真实网络验收：需显式设置 MAA_REFERENCE_LIVE=1",
    ),
]

REAL_DOI = "10.2196/80371"
REAL_TITLE = (
    "Influence of Topic Familiarity and Prompt Specificity on Citation Fabrication "
    "in Mental Health Research Using Large Language Models: Experimental Study"
)
CORRUPTED_TITLE = "Influence of Coffee Consumption on Quantum Foam Stabilization"
#: 冻结 demo 语料里的真实条目（arXiv 2309.15217，无 DOI）：走标题检索核验路径
CORPUS_TITLE = "Ragas: Automated Evaluation of Retrieval Augmented Generation"
CORPUS_AUTHORS = ["Shahul Es", "Jithin James", "Luis Espinosa-Anke", "Steven Schockaert"]
CORPUS_YEAR = 2023
CONTACT = "maa-acceptance@example.com"


def _verifier() -> ReferenceVerifier:
    timeout = httpx.Timeout(30.0)
    return ReferenceVerifier(
        crossref=CrossrefClient(mailto=CONTACT, timeout=timeout),
        openalex=OpenAlexClient(mailto=CONTACT, timeout=timeout),
        max_concurrency=2,
    )


@pytest.mark.asyncio
async def test_real_doi_is_verified() -> None:
    """验收：真实 DOI 判为「已验证」。"""
    verification = await _verifier().verify(
        ReferenceCandidate(
            key=REAL_DOI,
            title=REAL_TITLE,
            authors=["Linardon"],
            year=2025,
            doi=REAL_DOI,
        )
    )

    assert verification.verdict is ReferenceVerdict.VERIFIED, verification.reason
    assert verification.matched is not None
    assert verification.matched.doi == REAL_DOI


@pytest.mark.asyncio
async def test_fabricated_doi_is_not_found() -> None:
    """验收：伪造 DOI 判为「未找到」。"""
    verification = await _verifier().verify(
        ReferenceCandidate(
            key="10.9999/definitely-not-real-xyz",
            title="A Study That Does Not Exist Anywhere In The Literature",
            authors=["Nobody"],
            year=2031,
            doi="10.9999/definitely-not-real-xyz",
        )
    )

    assert verification.verdict is ReferenceVerdict.NOT_FOUND, verification.reason


@pytest.mark.asyncio
async def test_corrupted_title_with_real_doi_is_mismatch() -> None:
    """验收：故意改错标题 → 「元数据不符」（DOI 存在，故不是「未找到」）。"""
    verification = await _verifier().verify(
        ReferenceCandidate(
            key=REAL_DOI,
            title=CORRUPTED_TITLE,
            authors=["Linardon"],
            year=2025,
            doi=REAL_DOI,
        )
    )

    assert verification.verdict is ReferenceVerdict.METADATA_MISMATCH, verification.reason


@pytest.mark.asyncio
async def test_arxiv_title_without_doi_is_verified() -> None:
    """验收（本项目语料形态）：arXiv 文献无 DOI，走标题检索也能核验。

    语料实测：`Ragas...` 本地年份 2023、OpenAlex 命中 2024（会议版），
    在默认 `year_tolerance=1` 内 → 「已验证」。
    """
    verification = await _verifier().verify(
        ReferenceCandidate(
            key=CORPUS_TITLE,
            title=CORPUS_TITLE,
            authors=list(CORPUS_AUTHORS),
            year=CORPUS_YEAR,
        )
    )

    assert verification.verdict is ReferenceVerdict.VERIFIED, verification.reason


@pytest.mark.asyncio
async def test_unindexed_arxiv_preprint_is_not_found() -> None:
    """语料实测（计划 §2.8）：新近 arXiv 预印本可能尚未被外部源收录。

    这类条目判 `NOT_FOUND` 是**诚实结论**，也正是默认 `mode=report`（advisory）
    而不是 `enforce` 的原因：不得因外部源未收录而阻断任务。
    """
    title = "Designing AI Systems that Augment Human Performed vs. Demonstrated Critical Thinking"
    verification = await _verifier().verify(ReferenceCandidate(key=title, title=title, year=2025))

    assert verification.verdict is ReferenceVerdict.NOT_FOUND, verification.reason
    assert verification.similarity < 0.90


@pytest.mark.asyncio
async def test_unreachable_sources_degrade_to_unverified() -> None:
    """验收：外部源不可达 → 「未验证」降级态，绝不判失败、不抛异常。"""

    def _unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("模拟外部源不可达", request=request)

    transport = httpx.MockTransport(_unreachable)
    async with httpx.AsyncClient(transport=transport) as client:
        verifier = ReferenceVerifier(
            crossref=CrossrefClient(client=client, mailto=CONTACT),
            openalex=OpenAlexClient(client=client, mailto=CONTACT),
        )
        verification = await verifier.verify(
            ReferenceCandidate(key=CORPUS_TITLE, title=CORPUS_TITLE, year=CORPUS_YEAR)
        )

    assert verification.verdict is ReferenceVerdict.UNVERIFIED, verification.reason
