"""校验内容角色（Issue #6）：机器汇总内容不得被作者级文本检查判失败。

背景（服务器实测）：`validate` 节点的内容是 `_summarize_dependencies()` 把依赖节点输出**机械拼接**的
结果；write 节点复述 extract 节点原文是正常写法，一旦复述比例 >20%，
`duplicate_paragraph_ratio` 必然失败且**重试无法修复**（约 40% 运行转人工）。

本文件锁定三条不可退让的性质：
1. 同一段 33% 重复的文本，作为 **authored** 仍必须失败（不许把检查整体关掉）；
2. 作为 **aggregated** 必须通过，且两项检查以「不适用」形态出现在报告里（不许静默删除）；
3. **完整性失败在 aggregated 下仍然失败**（标记不可解析等仍能拦截）。
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.knowledge.services.citation_validator import REQUIREMENT_CHECKS, CitationValidator
from app.orchestration.agents.validator import ValidatorAgent
from app.orchestration.contracts import (
    Evidence,
    EvidenceContext,
    ExecutionResult,
    PlanNode,
    TaskPlan,
    ValidationReport,
)
from app.orchestration.workflows.literature_review import LiteratureReviewWorkflow

P1 = (
    "RAG 评估不仅要考察最终答案，也要分别衡量检索内容与生成结果之间的关系，"
    "Ragas 提出一种无需参考答案的自动评估框架，旨在缺乏标准答案时仍能提供评估依据。"
)
P2 = (
    "在验证方式上，Ragas 介绍了 WikiEval 数据集，其中包含对上述三个维度的人工判断，"
    "并据此考察自动评估结果与人工判断的一致程度。"
)

#: 真实生产形态：extract 块 + write 块（write 复述了 extract 的两段）
EXTRACT_BLOCK = f"[n3] ## 中文 RAG 评估方法综述\n\n{P1}\n\n{P2}"
WRITE_BLOCK = f"[n4] ## 综述正文\n\n{P1}\n\n{P2}"
AGGREGATED = f"{EXTRACT_BLOCK}\n\n{WRITE_BLOCK}"

#: 6 段落、2 段完全重复 → 0.333 > 0.20 阈值
AGGREGATED_DUPLICATE_RATIO = 2 / 6


def _result(
    content: str, evidence: list[Evidence] | None = None, *, node_id: str = "n5"
) -> ExecutionResult:
    return ExecutionResult(
        node_id=node_id,
        content=content,
        evidence=list(evidence or []),
        duration_ms=1,
    )


def _checks(report: ValidationReport) -> dict[str, bool]:
    return {check.name: check.passed for check in report.checks}


def _messages(report: ValidationReport) -> dict[str, str]:
    return {check.name: check.message for check in report.checks}


class TestAggregatedContentRole:
    def test_fixture_reproduces_production_duplicate_ratio(self) -> None:
        """夹具必须真的复现生产里的重复比例，否则后面的断言没有意义。"""
        ratio = CitationValidator._duplicate_ratio(AGGREGATED)

        assert ratio > 0.20
        assert abs(ratio - AGGREGATED_DUPLICATE_RATIO) < 1e-9

    def test_cross_node_repetition_does_not_fail_aggregated_content(self) -> None:
        """Issue #6 主回归：汇总内容里的跨节点复述不是模型缺陷，不得判失败。"""
        report = CitationValidator().validate(
            _result(AGGREGATED), None, evidence_pool=[], content_role="aggregated"
        )

        assert report.passed is True
        assert _checks(report)["duplicate_paragraph_ratio"] is True
        assert _checks(report)["formal_claims_have_evidence"] is True
        # 必须显式说明「不适用」，而不是悄悄变成通过
        assert "汇总" in _messages(report)["duplicate_paragraph_ratio"]
        assert "汇总" in _messages(report)["formal_claims_have_evidence"]

    def test_same_text_as_authored_still_fails(self) -> None:
        """守卫：作者级内容上的重复检查必须保持原样生效。"""
        report = CitationValidator().validate(_result(AGGREGATED), None, evidence_pool=[])

        assert report.passed is False
        assert report.retryable is True
        assert _checks(report)["duplicate_paragraph_ratio"] is False

    def test_explicit_authored_role_matches_default(self) -> None:
        default = CitationValidator().validate(_result(AGGREGATED), None, evidence_pool=[])
        explicit = CitationValidator().validate(
            _result(AGGREGATED), None, evidence_pool=[], content_role="authored"
        )

        assert _checks(default) == _checks(explicit)

    def test_report_shape_is_unchanged_for_both_roles(self) -> None:
        for role in ("authored", "aggregated"):
            report = CitationValidator().validate(
                _result(AGGREGATED), None, evidence_pool=[], content_role=role
            )
            names = [check.name for check in report.checks]
            assert names[: len(REQUIREMENT_CHECKS)] == list(REQUIREMENT_CHECKS), role

    def test_integrity_failure_still_fails_when_aggregated(self) -> None:
        """汇总内容同样不能带着不可解析的引用标记过关。"""
        report = CitationValidator().validate(
            _result(f"{AGGREGATED}\n\n额外断言 [E9]"),
            None,
            evidence_pool=[],
            content_role="aggregated",
        )

        assert report.passed is False
        assert _checks(report)["citation_markers_resolve"] is False


class TestValidatorAgentForwardsContentRole:
    def _input(self, *, content_role: Any = "aggregated") -> dict[str, Any]:
        payload: dict[str, Any] = {
            "node_id": "n5",
            "title": "校验综述",
            "result": AGGREGATED,
            "evidence_pool": [],
            "execution_result": _result(AGGREGATED).model_dump(mode="json"),
        }
        if content_role is not None:
            payload["content_role"] = content_role
        return payload

    def test_aggregated_role_reaches_citation_validator(self) -> None:
        agent = ValidatorAgent(citation_validator=CitationValidator())

        report = agent._run_citation_checks(self._input(content_role="aggregated"))

        assert report is not None
        assert report.passed is True
        assert "汇总" in _messages(report)["duplicate_paragraph_ratio"]

    def test_missing_role_defaults_to_authored(self) -> None:
        agent = ValidatorAgent(citation_validator=CitationValidator())

        report = agent._run_citation_checks(self._input(content_role=None))

        assert report is not None
        assert report.passed is False
        assert _checks(report)["duplicate_paragraph_ratio"] is False

    def test_invalid_role_falls_back_to_authored(self) -> None:
        """脏载荷不得让校验崩掉，也不得悄悄放宽检查。"""
        agent = ValidatorAgent(citation_validator=CitationValidator())

        report = agent._run_citation_checks(self._input(content_role="nonsense"))

        assert report is not None
        assert report.passed is False
        assert _checks(report)["duplicate_paragraph_ratio"] is False


class _Task:
    """工作流校验缝所需的最小任务对象。"""

    def __init__(self) -> None:
        self.id = uuid4()
        self.user_id = uuid4()
        self.title = "检索增强生成的评估方法"
        self.config: dict[str, Any] = {"trace_id": "trace-issue-6"}


class _FakeValidatorAgent:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute(self, input_data: dict[str, Any]) -> ValidationReport:
        self.calls.append(dict(input_data))
        return ValidationReport(passed=True, score=100.0, retryable=False, checks=[], feedback="")


class _FakeContextBuilder:
    async def build(self, user_id: Any, query: str, *, top_k: int = 8) -> EvidenceContext:
        evidence = [
            Evidence(doc_id=uuid4(), chunk_id=uuid4(), source_url="https://x/1", excerpt="片段 1"),
            Evidence(doc_id=uuid4(), chunk_id=uuid4(), source_url="https://x/2", excerpt="片段 2"),
        ]
        return EvidenceContext(
            text="[E1] 片段 1\n\n[E2] 片段 2", evidence=evidence, insufficient=False, query=query
        )


class TestWorkflowContentRole:
    async def test_validate_node_marks_aggregated_content(self) -> None:
        """validate/review 节点输出是机械汇总 → 必须标记为 aggregated。"""
        validator = _FakeValidatorAgent()
        workflow = LiteratureReviewWorkflow(validator=validator)
        task = _Task()
        node = PlanNode(id="n5", type="validate", title="校验综述", dependencies=["n4"])
        await workflow.prepare_node(task, node)

        await workflow.validate_node(task, _result(AGGREGATED, []))

        assert validator.calls[0]["content_role"] == "aggregated"

    async def test_write_node_is_authored(self) -> None:
        """extract/write 节点是作者文本 → 必须保持 authored（检查照常生效）。"""
        validator = _FakeValidatorAgent()
        workflow = LiteratureReviewWorkflow(
            validator=validator, context_builder=_FakeContextBuilder()
        )
        task = _Task()
        node = PlanNode(id="n4", type="write", title="撰写综述", dependencies=["n3"])
        await workflow.prepare_node(task, node)

        await workflow.validate_node(task, _result(AGGREGATED, [], node_id="n4"))

        assert validator.calls[0]["content_role"] == "authored"

    async def test_search_node_is_aggregated(self) -> None:
        """search 节点输出是检索结果列表，同样不是作者文本。"""
        validator = _FakeValidatorAgent()
        workflow = LiteratureReviewWorkflow(validator=validator)
        task = _Task()
        node = PlanNode(id="n1", type="search", title="检索文献")
        await workflow.prepare_node(task, node)

        await workflow.validate_node(task, _result("- 论文 A\n\n- 论文 B", [], node_id="n1"))

        assert validator.calls[0]["content_role"] == "aggregated"

    async def test_node_type_resolved_from_bound_plan(self) -> None:
        """`validate` 节点在 execute_node 里不走 prepare_node，类型必须能从绑定计划解析。"""
        validator = _FakeValidatorAgent()
        workflow = LiteratureReviewWorkflow(validator=validator)
        task = _Task()
        plan = TaskPlan(
            version=1,
            topic="检索增强生成的评估方法",
            research_questions=["RAG 如何评估"],
            search_queries=["RAG evaluation"],
            acceptance_criteria=["引用可追溯"],
            nodes=[
                PlanNode(id="n3", type="extract", title="抽取"),
                PlanNode(id="n4", type="write", title="撰写", dependencies=["n3"]),
                PlanNode(id="n5", type="validate", title="校验", dependencies=["n4"]),
            ],
        )
        workflow.use_plan(plan)

        await workflow.validate_node(task, _result(AGGREGATED, [], node_id="n5"))
        await workflow.validate_node(task, _result(AGGREGATED, [], node_id="n4"))

        assert validator.calls[0]["content_role"] == "aggregated"
        assert validator.calls[1]["content_role"] == "authored"

    async def test_unknown_node_type_stays_strict(self) -> None:
        """无法判定类型时按 authored 从严，不许静默放宽检查。"""
        validator = _FakeValidatorAgent()
        workflow = LiteratureReviewWorkflow(validator=validator)
        task = _Task()

        await workflow.validate_node(task, _result(AGGREGATED, [], node_id="n9"))

        assert validator.calls[0]["content_role"] == "authored"
