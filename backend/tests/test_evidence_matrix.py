"""Issue #32 Phase 3：研究空白矩阵聚合、红线校验与定向补检索单测。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import HttpUrl

from app.orchestration.contracts import ClaimCheckResult, ReviewReport
from app.orchestration.services.evidence_matrix import (
    build_evidence_matrix,
    merge_gap_records_into_matrix,
    verdict_to_bucket,
)
from app.orchestration.services.task_refresh import run_gap_fill_search
from app.orchestration.validation.citation_consistency import audit_citation_alignment
from app.tools.literature.models import LiteratureRecord, LiteratureSearch


def test_verdict_to_bucket_mapping() -> None:
    """验证 6 态判定到 4 档证据强度的完整映射。"""
    assert verdict_to_bucket("supported") == "strong"
    assert verdict_to_bucket("weakly_supported") == "weak_or_caveat"
    assert verdict_to_bucket("overstated") == "weak_or_caveat"
    assert verdict_to_bucket("misrepresented") == "weak_or_caveat"
    assert verdict_to_bucket("contradicted") == "conflicting"
    assert verdict_to_bucket("unverifiable") == "gap"


def test_build_evidence_matrix_and_metadata_redline() -> None:
    """验证矩阵聚合、仅题录红线标注、空格子 suggested_query 生成与老报告向后兼容。"""
    questions = [
        "多智能体协作在检索质量上的效果如何？",
        "医学影像场景下的跨模态幻觉控制是否存在研究空白？",
    ]
    claims = [
        ClaimCheckResult(
            claim_id="n2:c1",
            sentence_text="多智能体协作显著提升了复杂查询的检索质量 [E1]。",
            cited_ref_ids=["E1"],
            verdict="supported",
            confidence=0.92,
            evidence_quote="multi-agent improves retrieval quality",
            rationale="全文实验支持",
            evidence_scope="full_text",
            evidence_passage="Our results show multi-agent improves retrieval quality.",
        ),
        ClaimCheckResult(
            claim_id="n2:c2",
            sentence_text="部分研究表明多智能体协作会增加延迟且收益不稳定 [E2]。",
            cited_ref_ids=["E2"],
            verdict="weakly_supported",
            confidence=0.71,
            evidence_quote=None,
            rationale="仅题录摘要提及",
            evidence_scope="abstract_only",
            evidence_passage="Abstract metadata only.",
        ),
    ]
    bib = [
        {"index": 1, "title": "Multi-Agent RAG", "chunk_id": "chunk-1"},
        {"index": 2, "title": "Latency Analysis of Multi-Agent"},
    ]

    matrix = build_evidence_matrix(
        questions,
        claims,
        bib,
        node_titles={"n2": "多智能体协作检索质量"},
        topic="多智能体检索增强综述",
    )
    assert len(matrix.questions) == 2
    assert len(matrix.cells) == 8  # 2 行 × 4 列

    cell_map = {(c.question_index, c.bucket): c for c in matrix.cells}
    # 第 0 行 strong：有 full_text 证据 → excerpt
    strong0 = cell_map[(0, "strong")]
    assert strong0.claim_ids == ["n2:c1"]
    assert strong0.ref_indices == [1]
    assert strong0.evidence_kind == "excerpt"

    # 第 0 行 weak_or_caveat：仅 abstract_only → 必须标 metadata 且给出 suggested_query
    weak0 = cell_map[(0, "weak_or_caveat")]
    assert weak0.claim_ids == ["n2:c2"]
    assert weak0.evidence_kind == "metadata"
    assert weak0.suggested_query is not None

    # 第 1 行无命中 → gap 格必须为 none 且带定向 suggested_query
    gap1 = cell_map[(1, "gap")]
    assert gap1.claim_ids == []
    assert gap1.evidence_kind == "none"
    assert gap1.suggested_query is not None
    assert "医学影像" in gap1.suggested_query

    # ReviewReport 历史兼容：不传 evidence_matrix 默认 None
    legacy_report = ReviewReport(
        final_markdown="# 旧综述",
        bibliography=[],
        scores={},
        issues=[],
        stats={},
    )
    assert legacy_report.evidence_matrix is None


class _FakeGapSource:
    async def search(self, search: LiteratureSearch) -> list[LiteratureRecord]:
        return [
            # 与已有文献重复的 DOI
            LiteratureRecord(
                source_id="openalex:W1",
                title="Existing Paper",
                authors=["Alice"],
                abstract="Old abstract",
                published_at=datetime(2023, 1, 1, tzinfo=UTC),
                source_url=HttpUrl("https://doi.org/10.1000/existing"),
                pdf_url=HttpUrl("https://doi.org/10.1000/existing"),
            ),
            # 全新文献
            LiteratureRecord(
                source_id="openalex:W99",
                title="Medical Multimodal Hallucination Mitigation",
                authors=["Bob"],
                abstract="New benchmark for medical VLM hallucination.",
                published_at=datetime(2025, 2, 1, tzinfo=UTC),
                source_url=HttpUrl("https://doi.org/10.1000/new-med"),
                pdf_url=HttpUrl("https://doi.org/10.1000/new-med"),
            ),
        ]


@pytest.mark.asyncio
async def test_run_gap_fill_search_dedups_passes_citation_audit_and_updates_matrix() -> None:
    """定向补检索去重、通过引用一致性闸门并正确更新目标行矩阵格子。"""
    existing_bib = [
        {"index": 1, "title": "Existing Paper", "doi": "10.1000/existing"},
    ]
    res = await run_gap_fill_search(
        source=_FakeGapSource(),
        query="medical VLM hallucination",
        question="医学影像跨模态幻觉控制是否存在研究空白？",
        existing_bibliography=existing_bib,
        llm=None,
    )
    assert res.duplicate_count == 1
    assert len(res.new_bibliography_entries) == 1
    assert res.new_bibliography_entries[0]["index"] == 2
    assert "[E2]" in res.section_markdown

    # 合并到原正文和 bibliography 后必须通过引用一致性闸门
    merged_md = f"# 综述\n\n既有观点 [E1]。\n\n{res.section_markdown}"
    merged_bib = [*existing_bib, *res.new_bibliography_entries]
    audit = audit_citation_alignment(merged_md, merged_bib)
    assert audit.ok, audit.reason

    # 矩阵重算：第 0 行 weak_or_caveat 纳入新文献编号 2 且标 metadata
    matrix = build_evidence_matrix(["医学影像跨模态幻觉控制是否存在研究空白？"], [], existing_bib)
    updated = merge_gap_records_into_matrix(
        matrix,
        question_index=0,
        added_ref_indices=[2],
        query="medical VLM hallucination",
    )
    cell_map = {(c.question_index, c.bucket): c for c in updated.cells}
    assert 2 in cell_map[(0, "weak_or_caveat")].ref_indices
    assert cell_map[(0, "weak_or_caveat")].evidence_kind == "metadata"
