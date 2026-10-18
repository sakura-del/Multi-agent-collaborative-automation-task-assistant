"""引用真实性校验检查测试（Issue #3 / 计划 §2.4）。

覆盖三态判定并入 `ValidationReport.checks` 的行为：
- `off` 模式不追加检查（现有报告形状不变）
- `report` 模式为 advisory：记录结论但不影响 passed / retryable / score
- `enforce` 模式未通过则报告失败且可重试
- `unverified`（外部源不可用降级态）永不判失败
"""

from __future__ import annotations

from uuid import uuid4

from app.knowledge.services.citation_validator import (
    REFERENCE_CHECK,
    CitationValidator,
)
from app.orchestration.contracts import Evidence, ExecutionResult, ValidationCheck
from app.tools.literature.models import (
    MetadataRecord,
    ReferenceVerdict,
    ReferenceVerification,
)

REQUIRED_SECTIONS = ("摘要", "方法", "结论")

GOOD_CONTENT = """摘要
本文综述检索增强生成的评估方法 [E1]。

方法
我们比较了检索质量与证据可追溯性指标 [E2]。

结论
证据绑定可以提升综述可信度 [E1][E2]。
"""


def _evidence(index: int) -> Evidence:
    return Evidence(
        doc_id=uuid4(),
        chunk_id=uuid4(),
        source_url=f"https://arxiv.org/abs/2401.1234{index}",
        excerpt=f"证据片段 {index}",
    )


def _result(content: str = GOOD_CONTENT) -> ExecutionResult:
    return ExecutionResult(
        node_id="n3",
        content=content,
        evidence=[_evidence(1), _evidence(2)],
        duration_ms=1,
    )


def _verification(
    key: str,
    verdict: ReferenceVerdict,
    *,
    title: str | None = None,
    reason: str = "",
    similarity: float = 0.0,
    cached: bool = False,
) -> ReferenceVerification:
    matched = None
    if verdict in {ReferenceVerdict.VERIFIED, ReferenceVerdict.METADATA_MISMATCH}:
        matched = MetadataRecord(
            source="crossref",
            doi=key if key.startswith("10.") else None,
            title=title or key,
            authors=["Alice Zhang"],
            year=2024,
        )
    return ReferenceVerification(
        reference_key=key,
        title=title or key,
        verdict=verdict,
        reason=reason,
        similarity=similarity,
        matched=matched,
        cached=cached,
    )


def _checks(report) -> dict[str, bool]:
    return {check.name: check.passed for check in report.checks}


def _reference_check(report) -> ValidationCheck | None:
    for check in report.checks:
        if check.name == REFERENCE_CHECK:
            return check
    return None


VERIFIED = [
    _verification("10.1000/abc", ReferenceVerdict.VERIFIED, title="RAG 评估方法"),
    _verification("10.1000/def", ReferenceVerdict.VERIFIED, title="证据可追溯性"),
]


class TestReferenceCheckModes:
    def test_off_mode_adds_no_reference_check(self) -> None:
        """默认 off：即使传入核验结果也不追加检查，保持既有报告形状。"""
        report = CitationValidator().validate(
            _result(), REQUIRED_SECTIONS, reference_verifications=VERIFIED
        )

        assert _reference_check(report) is None
        assert report.passed is True
        assert report.score == 100

    def test_report_mode_records_verified_check(self) -> None:
        validator = CitationValidator(reference_mode="report")
        report = validator.validate(_result(), REQUIRED_SECTIONS, reference_verifications=VERIFIED)

        check = _reference_check(report)
        assert check is not None
        assert check.passed is True
        assert check.advisory is True
        assert "已验证 2" in check.message
        assert report.passed is True
        assert report.retryable is False

    def test_report_mode_does_not_fail_report_on_not_found(self) -> None:
        """advisory 语义：结论记录在 checks 里，但不影响 passed / retryable / score。"""
        verifications = [
            *VERIFIED,
            _verification(
                "10.9999/ghost",
                ReferenceVerdict.NOT_FOUND,
                title="不存在的文献",
                reason="DOI 与标题检索均无命中",
            ),
        ]
        report = CitationValidator(reference_mode="report").validate(
            _result(), REQUIRED_SECTIONS, reference_verifications=verifications
        )

        check = _reference_check(report)
        assert check is not None
        assert check.passed is False
        assert check.advisory is True
        assert "未找到 1" in check.message
        assert "不存在的文献" in check.message
        assert report.passed is True
        assert report.retryable is False
        assert report.score == 100

    def test_enforce_mode_fails_report_on_not_found(self) -> None:
        verifications = [
            *VERIFIED,
            _verification("10.9999/ghost", ReferenceVerdict.NOT_FOUND, title="不存在的文献"),
        ]
        report = CitationValidator(reference_mode="enforce").validate(
            _result(), REQUIRED_SECTIONS, reference_verifications=verifications
        )

        check = _reference_check(report)
        assert check is not None
        assert check.passed is False
        assert check.advisory is False
        assert report.passed is False
        assert report.retryable is True
        assert report.score < 100

    def test_enforce_mode_passes_when_all_verified(self) -> None:
        report = CitationValidator(reference_mode="enforce").validate(
            _result(), REQUIRED_SECTIONS, reference_verifications=VERIFIED
        )

        assert report.passed is True
        assert report.score == 100

    def test_missing_verifications_skips_check_even_in_enforce_mode(self) -> None:
        """未接入核验（None）时不得凭空产生检查，否则会误伤老流程。"""
        report = CitationValidator(reference_mode="enforce").validate(_result(), REQUIRED_SECTIONS)

        assert _reference_check(report) is None
        assert report.passed is True


class TestDegradedUnverified:
    def test_unverified_never_fails_in_report_mode(self) -> None:
        verifications = [
            _verification(
                "10.1000/abc",
                ReferenceVerdict.UNVERIFIED,
                title="RAG 评估方法",
                reason="CrossRef 超时，OpenAlex 不可用",
            )
        ]
        report = CitationValidator(reference_mode="report").validate(
            _result(), REQUIRED_SECTIONS, reference_verifications=verifications
        )

        check = _reference_check(report)
        assert check is not None
        assert check.passed is True
        assert "未核验 1" in check.message
        assert report.passed is True

    def test_unverified_never_fails_in_enforce_mode(self) -> None:
        """外部源不可用不得判失败或阻塞任务（Issue #3 实现要点 6）。"""
        verifications = [
            _verification("10.1000/abc", ReferenceVerdict.UNVERIFIED, title="RAG 评估方法")
        ]
        report = CitationValidator(reference_mode="enforce").validate(
            _result(), REQUIRED_SECTIONS, reference_verifications=verifications
        )

        check = _reference_check(report)
        assert check is not None
        assert check.passed is True
        assert report.passed is True
        assert report.retryable is False

    def test_unverified_mixed_with_verified_still_counts(self) -> None:
        verifications = [
            *VERIFIED,
            _verification("10.1000/xyz", ReferenceVerdict.UNVERIFIED, title="未核验文献"),
        ]
        report = CitationValidator(reference_mode="enforce").validate(
            _result(), REQUIRED_SECTIONS, reference_verifications=verifications
        )

        check = _reference_check(report)
        assert check is not None
        assert check.passed is True
        assert "已验证 2" in check.message
        assert "未核验 1" in check.message


class TestMetadataMismatch:
    def test_mismatch_fails_check_and_reports_reason(self) -> None:
        verifications = [
            _verification(
                "10.1000/abc",
                ReferenceVerdict.METADATA_MISMATCH,
                title="被改错的标题",
                reason="标题相似度 0.41 低于阈值 0.90",
                similarity=0.41,
            )
        ]
        report = CitationValidator(reference_mode="enforce").validate(
            _result(), REQUIRED_SECTIONS, reference_verifications=verifications
        )

        check = _reference_check(report)
        assert check is not None
        assert check.passed is False
        assert "元数据不符 1" in check.message
        assert "被改错的标题" in check.message

    def test_advisory_check_failure_lands_in_feedback(self) -> None:
        """report 模式下问题也应进入 feedback，便于人工与导出报告审计。"""
        verifications = [
            _verification(
                "10.1000/abc",
                ReferenceVerdict.METADATA_MISMATCH,
                title="被改错的标题",
                reason="作者不一致",
            )
        ]
        report = CitationValidator(reference_mode="report").validate(
            _result(), REQUIRED_SECTIONS, reference_verifications=verifications
        )

        assert report.feedback
        assert "被改错的标题" in report.feedback

    def test_problem_titles_are_capped(self) -> None:
        verifications = [
            _verification(f"10.9999/{i}", ReferenceVerdict.NOT_FOUND, title=f"伪造文献{i}")
            for i in range(5)
        ]
        report = CitationValidator(reference_mode="enforce").validate(
            _result(), REQUIRED_SECTIONS, reference_verifications=verifications
        )

        check = _reference_check(report)
        assert check is not None
        assert "伪造文献0" in check.message
        assert "伪造文献4" not in check.message


class TestCheckHelperDirectly:
    def test_helper_returns_passing_check_for_all_verified(self) -> None:
        check = CitationValidator.check_reference_authenticity(VERIFIED, advisory=False)

        assert check.name == REFERENCE_CHECK
        assert check.passed is True
        assert check.advisory is False
        assert "已验证 2" in check.message

    def test_helper_advisory_flag_is_propagated(self) -> None:
        verifications = [
            _verification("10.9999/ghost", ReferenceVerdict.NOT_FOUND, title="伪造文献")
        ]
        check = CitationValidator.check_reference_authenticity(verifications, advisory=True)

        assert check.passed is False
        assert check.advisory is True

    def test_helper_handles_empty_input(self) -> None:
        check = CitationValidator.check_reference_authenticity([], advisory=True)

        assert check.passed is True
        assert "已验证 0" in check.message


class TestContractBackwardCompatibility:
    def test_validation_check_advisory_defaults_to_false(self) -> None:
        """advisory 必须是可选字段：历史持久化负载（无该键）仍可反序列化。"""
        check = ValidationCheck(name="legacy", passed=True, message="旧数据")

        assert check.advisory is False
        assert check.model_dump()["advisory"] is False
