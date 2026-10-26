"""确定性引用校验测试（Plan 3 Task 4）。

覆盖五项固定检查名：
`citation_markers_resolve` / `evidence_is_cited` / `formal_claims_have_evidence` /
`required_sections_present` / `duplicate_paragraph_ratio`，
以及不可重试的完整性错误（证据 ID 非法 / 归属越权）。
"""

from __future__ import annotations

from uuid import uuid4

from app.knowledge.services.citation_validator import REQUIREMENT_CHECKS, CitationValidator
from app.orchestration.contracts import Evidence, ExecutionResult

REQUIRED_SECTIONS = ("摘要", "方法", "结论")

GOOD_CONTENT = """摘要
本文综述检索增强生成的评估方法 [E1]。

方法
我们比较了检索质量与证据可追溯性指标 [E2]。

结论
证据绑定可以提升综述可信度 [E1][E2]。
"""


def _evidence(index: int, *, malformed: bool = False) -> Evidence:
    return Evidence(
        doc_id=None if malformed else uuid4(),
        chunk_id=uuid4(),
        source_url=f"https://arxiv.org/abs/2401.1234{index}",
        excerpt=f"证据片段 {index}",
    )


def _result(content: str, evidence: list[Evidence] | None = None) -> ExecutionResult:
    return ExecutionResult(
        node_id="n3",
        content=content,
        evidence=list(evidence if evidence is not None else [_evidence(1), _evidence(2)]),
        duration_ms=1,
    )


def _checks(report) -> dict[str, bool]:
    return {check.name: check.passed for check in report.checks}


class TestCitationRules:
    def test_valid_content_passes_all_requirements(self) -> None:
        report = CitationValidator().validate(_result(GOOD_CONTENT), REQUIRED_SECTIONS)

        assert report.passed is True
        assert report.retryable is False
        assert report.score == 100
        names = {check.name for check in report.checks}
        assert set(REQUIREMENT_CHECKS) <= names
        assert all(_checks(report)[name] for name in REQUIREMENT_CHECKS)

    def test_unknown_marker_fails_and_is_retryable(self) -> None:
        content = GOOD_CONTENT.replace("[E2]", "[E9]")
        report = CitationValidator().validate(_result(content), REQUIRED_SECTIONS)

        assert report.passed is False
        assert report.retryable is True
        assert _checks(report)["citation_markers_resolve"] is False
        assert "E9" in report.feedback or "[E9]" in report.feedback

    def test_partial_evidence_coverage_passes_with_note(self) -> None:
        """单节点只引用池内部分证据是允许的：通过但记录覆盖率提示。"""
        content = GOOD_CONTENT.replace("[E2]", "").replace("[E1][E2]", "[E1]")
        report = CitationValidator().validate(_result(content), REQUIRED_SECTIONS)

        assert report.passed is True
        check = next(item for item in report.checks if item.name == "evidence_is_cited")
        assert check.passed is True
        assert "未引用" in check.message

    def test_output_ignoring_all_evidence_fails(self) -> None:
        """池非空却完全不引用证据：视为空转，可重试失败（Plan 3 Exit Gate 实跑语义）。"""
        content = GOOD_CONTENT.replace("[E1]", "").replace("[E2]", "")
        report = CitationValidator().validate(_result(content), REQUIRED_SECTIONS)

        assert report.passed is False
        assert report.retryable is True
        assert _checks(report)["evidence_is_cited"] is False

    def test_evidence_pool_larger_than_result_contract_is_detected(self) -> None:
        """池比 result.evidence 多时仍要求正文用到池内证据，并给出覆盖率提示。"""
        pool = [_evidence(1), _evidence(2), _evidence(3)]
        result = _result(GOOD_CONTENT, evidence=[_evidence(1), _evidence(2)])
        report = CitationValidator().validate(result, REQUIRED_SECTIONS, evidence_pool=pool)

        check = next(item for item in report.checks if item.name == "evidence_is_cited")
        assert check.passed is True
        assert "[E3]" in check.message

    def test_bare_literature_claim_fails(self) -> None:
        content = GOOD_CONTENT + "\n研究表明 RAG 可以显著提升事实一致性。\n"
        report = CitationValidator().validate(_result(content), REQUIRED_SECTIONS)

        assert report.passed is False
        assert report.retryable is True
        assert _checks(report)["formal_claims_have_evidence"] is False

    def test_inference_label_is_allowed_without_marker(self) -> None:
        content = GOOD_CONTENT + "\n模型推断：多智能体协作可能进一步降低幻觉率。\n"
        report = CitationValidator().validate(_result(content), REQUIRED_SECTIONS)
        assert _checks(report)["formal_claims_have_evidence"] is True

    def test_missing_required_section_fails(self) -> None:
        content = GOOD_CONTENT.replace("方法", "流程")
        report = CitationValidator().validate(_result(content), REQUIRED_SECTIONS)

        assert report.passed is False
        assert report.retryable is True
        assert _checks(report)["required_sections_present"] is False

    def test_duplicate_paragraph_ratio_fails(self) -> None:
        duplicated = GOOD_CONTENT + "\n" + GOOD_CONTENT
        report = CitationValidator().validate(_result(duplicated), REQUIRED_SECTIONS)

        assert report.passed is False
        assert report.retryable is True
        assert _checks(report)["duplicate_paragraph_ratio"] is False

    def test_malformed_evidence_id_is_not_retryable(self) -> None:
        pool = [_evidence(1, malformed=True), _evidence(2)]
        report = CitationValidator().validate(
            _result(GOOD_CONTENT), REQUIRED_SECTIONS, evidence_pool=pool
        )

        assert report.passed is False
        assert report.retryable is False
        assert _checks(report).get("evidence_ids_valid") is False

    def test_metadata_evidence_without_ids_is_valid(self) -> None:
        """元数据证据按设计没有 doc_id/chunk_id，不能判为「标识缺失」。

        真实任务踩到过：引用库任务加了 `kind="metadata"` 证据后，
        `evidence_ids_valid` 立刻失败（`证据标识缺失: ['[E1]', '[E2]']`），
        任务转 `waiting_human`。

        元数据证据来自文献检索结果、不绑定知识库分块 ——
        填一个假 UUID 才能过检查的话，那「可回溯」就是假话（AGENTS §5 #28）。
        """
        pool = [
            Evidence(
                doc_id=None,
                chunk_id=None,
                source_url="https://doi.org/10.18653/v1/2024.eacl-demo.16",
                excerpt="《Ragas》— Es, Shahul，2024",
                kind="metadata",
            ),
            _evidence(2),
        ]
        report = CitationValidator().validate(
            _result(GOOD_CONTENT), REQUIRED_SECTIONS, evidence_pool=pool
        )

        assert _checks(report).get("evidence_ids_valid") is True

    def test_metadata_kind_does_not_excuse_missing_excerpt(self) -> None:
        """元数据证据仍然必须有 excerpt（契约层已保证，这里锁语义）。"""
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            Evidence(doc_id=None, chunk_id=None, excerpt="", kind="metadata")

    def test_ownership_violation_is_not_retryable(self) -> None:
        validator = CitationValidator(ownership_violation=True)
        report = validator.validate(_result(GOOD_CONTENT), REQUIRED_SECTIONS)

        assert report.passed is False
        assert report.retryable is False
        assert _checks(report).get("evidence_ownership") is False

    def test_marker_without_any_evidence_fails_resolution(self) -> None:
        result = _result("摘要\n本文综述评估方法 [E1]。\n\n方法\n无。\n\n结论\n无。", evidence=[])
        report = CitationValidator().validate(result, REQUIRED_SECTIONS)

        assert report.passed is False
        assert report.retryable is True
        assert _checks(report)["citation_markers_resolve"] is False
