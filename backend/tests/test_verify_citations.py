"""Issue #32 Phase 2A：verify_citations 契约校验与独立短链路执行单测。"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.llm.models import LLMResponse
from app.orchestration.validation.citation_consistency import audit_citation_alignment
from app.orchestration.workflows.verify_citations import (
    DocumentChunkInput,
    VerifyCitationsRunner,
    parse_draft_and_references,
)
from app.schemas.task import TaskCreate
from app.tools.literature.models import ReferenceCandidate, ReferenceVerdict, ReferenceVerification


class _FakeReferenceVerifier:
    """模拟外部元数据源核验器（支持撤稿与正常态）。"""

    intercept_unverified = False

    def __init__(self) -> None:
        self.called_keys: list[str] = []

    async def verify_many(
        self, candidates: list[ReferenceCandidate]
    ) -> list[ReferenceVerification]:
        results: list[ReferenceVerification] = []
        for candidate in candidates:
            self.called_keys.append(candidate.key)
            if "10.1000/retracted" in (candidate.doi or ""):
                results.append(
                    ReferenceVerification(
                        reference_key=candidate.key,
                        title=candidate.title,
                        verdict=ReferenceVerdict.VERIFIED,
                        reason="DOI 命中，但来源标记已撤稿",
                        is_retracted=True,
                        quality_flags=["retracted"],
                        retraction_reason="Data fabrication",
                    )
                )
            else:
                results.append(
                    ReferenceVerification(
                        reference_key=candidate.key,
                        title=candidate.title,
                        verdict=ReferenceVerdict.VERIFIED,
                        reason="Crossref DOI 精确匹配",
                        is_retracted=False,
                        quality_flags=[],
                        cited_by_count=120,
                    )
                )
        return results


class _FakeLLM:
    """返回指定 JSON 数组的最小 LLM 替身。"""

    def __init__(self, payload: list[dict[str, Any]]) -> None:
        self._payload = payload

    async def chat(self, messages: list[Any], **kwargs: Any) -> LLMResponse:
        return LLMResponse(
            content=json.dumps(self._payload, ensure_ascii=False),
            model="fake-model",
            provider="fake",
        )


def test_task_create_verify_citations_validator() -> None:
    """verify_citations 要求 draft_text 或 document_ids 至少提供其一，并自动补默认关键词。"""
    with pytest.raises(ValidationError):
        TaskCreate(
            topic="草稿引用核验测试",
            workflow_preset="verify_citations",
        )

    with_draft = TaskCreate(
        topic="草稿引用核验测试",
        workflow_preset="verify_citations",
        draft_text="大模型评估指标需要多维度校验 [E1]。",
    )
    contract = with_draft.to_contract()
    assert contract.workflow_preset == "verify_citations"
    assert contract.keywords == ["引用核验"]
    assert contract.draft_text is not None

    with_doc = TaskCreate(
        topic="知识库文档引用核验",
        workflow_preset="verify_citations",
        document_ids=[uuid4()],
    )
    assert with_doc.to_contract().keywords == ["引用核验"]

    # 非 verify_citations 仍要求至少 1 个关键词
    with pytest.raises(ValidationError):
        TaskCreate(topic="常规综述缺少关键词", keywords=[])


def test_parse_draft_normalizes_plain_brackets_and_extracts_doi() -> None:
    """纯数字 [1] 自动转为 [E1]，并解析文末参考文献中的 DOI 与年份。"""
    draft = (
        "检索增强生成能够降低幻觉率 [1]。\n\n"
        "## 参考文献\n"
        "- [1] Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks. 2020. "
        "https://doi.org/10.1000/rag2020\n"
    )
    body, bib = parse_draft_and_references(draft)
    assert "[E1]" in body
    assert len(bib) == 1
    assert bib[0]["index"] == 1
    assert bib[0]["doi"] == "10.1000/rag2020"
    assert bib[0]["year"] == 2020


@pytest.mark.asyncio
async def test_verify_citations_runner_reuses_verifiers_and_enforces_abstract_only_rule() -> None:
    """端到端运行短链路：四态+撤稿标记、论断核验（仅题录不得判矛盾）且通过引用一致性闸门。"""
    draft = (
        "方法 A 在基准上准确率提升了 30% [E1]。\n"
        "方法 B 在所有场景下绝对最优 [E2]。\n\n"
        "## 参考文献\n"
        "- [1] Method A Benchmark Study. 2023. https://doi.org/10.1000/good\n"
        "- [2] Retracted Method B Study. 2022. https://doi.org/10.1000/retracted\n"
    )
    # 故意让模型对仅题录的 E2 判 contradicted，验证 ClaimVerifier 会强制降级为 unverifiable
    fake_llm = _FakeLLM(
        [
            {
                "claim_id": "verify:c1",
                "verdict": "supported",
                "confidence": 0.92,
                "evidence_quote": "Method A improves benchmark accuracy by 30%",
                "rationale": "原文直接给出 30% 提升数据",
                "suggested_fix": None,
            },
            {
                "claim_id": "verify:c2",
                "verdict": "contradicted",
                "confidence": 0.8,
                "evidence_quote": "fabricated quote not in metadata",
                "rationale": "与模型印象相反",
                "suggested_fix": "请核实原文",
            },
        ]
    )
    ref_verifier = _FakeReferenceVerifier()
    doc_chunk = DocumentChunkInput(
        doc_id=uuid4(),
        chunk_id=uuid4(),
        title="Method A Benchmark Study",
        content="Experimental results show that Method A improves benchmark accuracy by 30% on QA tasks.",
    )
    runner = VerifyCitationsRunner(reference_verifier=ref_verifier, llm=fake_llm)  # type: ignore[arg-type]
    report = await runner.run(
        topic="草稿引用核验测试",
        draft_text=draft,
        document_chunks=[doc_chunk],
    )

    assert len(report.bibliography) == 2
    assert report.bibliography[1]["is_retracted"] is True
    assert "retracted" in report.bibliography[1]["quality_flags"]
    assert report.stats["reference_verification"]["retracted"] == 1

    assert len(report.claim_checks) == 2
    # c1 有知识库分块原文：full_text + supported + quote 命中原句
    c1 = report.claim_checks[0]
    assert c1.verdict == "supported"
    assert c1.evidence_scope == "full_text"
    assert c1.evidence_quote == "Method A improves benchmark accuracy by 30%"
    # c2 仅有题录元数据：abstract_only，contradicted 被强制降为 unverifiable，且伪造 quote 被清空
    c2 = report.claim_checks[1]
    assert c2.evidence_scope == "abstract_only"
    assert c2.verdict == "unverifiable"
    assert c2.evidence_quote is None

    # 必须通过引用一致性闸门
    audit = audit_citation_alignment(report.final_markdown, report.bibliography)
    assert audit.ok, audit.reason
