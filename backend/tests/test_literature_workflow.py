"""文献综述工作流与证据约束执行测试（Plan 3 Task 3）。

覆盖：
- Planner 产出固定文献契约计划：3–10 个研究问题、五类节点齐全、review 为终端节点、
  每个 write 节点依赖 extract 节点；
- 契约不满足时只做一次修复请求，仍失败抛 `HumanActionRequiredError`；
- Executor 只能引用传入证据（`[E1]`/`[E2]`），臆造 `[E9]` 在持久化前被拒；
- 工作流五个缝：plan / prepare_node / execute_node / validate_node / review；
- Prompt 经既有 PromptManager 渲染，且包含不可信文本与「模型推断」约束。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.core.exceptions import EvidenceCitationError, HumanActionRequiredError
from app.llm.models import LLMResponse, MessageRole, Usage
from app.orchestration.agents.executor import ExecutorAgent
from app.orchestration.agents.planner import PlannerAgent
from app.orchestration.contracts import (
    Evidence,
    EvidenceContext,
    ExecutionResult,
    ReviewReport,
    ValidationCheck,
    ValidationReport,
)
from app.orchestration.workflows.literature_review import (
    LiteratureReviewWorkflow,
    render_executor_prompt,
    render_planner_prompt,
)
from app.tools.literature.models import LiteratureRecord, LiteratureSearch
from tests.llm.mock_llm import MockLLM, make_text_response

USER_ID = UUID("33333333-3333-3333-3333-333333333333")


# ===== 计划负载 =====


def _literature_plan_payload() -> dict[str, Any]:
    return {
        "version": 1,
        "topic": "检索增强生成的评估方法",
        "research_questions": [
            "RAG 的评估指标有哪些？",
            "如何度量证据可追溯性？",
            "多智能体协作如何影响质量？",
        ],
        "search_queries": ["retrieval augmented generation evaluation"],
        "acceptance_criteria": ["引用可回溯"],
        "nodes": [
            {"id": "n1", "type": "search", "title": "检索文献", "dependencies": []},
            {"id": "n2", "type": "extract", "title": "提取证据", "dependencies": ["n1"]},
            {"id": "n3", "type": "write", "title": "撰写初稿", "dependencies": ["n2"]},
            {"id": "n4", "type": "validate", "title": "校验引用", "dependencies": ["n3"]},
            {"id": "n5", "type": "review", "title": "汇总复盘", "dependencies": ["n4"]},
        ],
    }


def _incomplete_plan_payload() -> dict[str, Any]:
    payload = _literature_plan_payload()
    payload["nodes"] = payload["nodes"][:3]  # 缺少 validate / review
    return payload


def _llm_response(payload: dict[str, Any]) -> LLMResponse:
    import json

    return LLMResponse(
        content=json.dumps(payload, ensure_ascii=False),
        finish_reason="stop",
        usage=Usage(),
        role=MessageRole.ASSISTANT,
    )


def _task(topic: str = "检索增强生成的评估方法") -> Any:
    return SimpleNamespace(
        id=uuid4(),
        user_id=USER_ID,
        title=topic,
        description=f"{topic}：系统梳理评估方法与证据可追溯性",
        task_type="literature_review",
        config={"trace_id": "trace-lit"},
    )


def _evidence(index: int = 1) -> Evidence:
    return Evidence(
        doc_id=uuid4(),
        chunk_id=uuid4(),
        source_url=f"https://arxiv.org/abs/2401.1234{index}",
        excerpt=f"证据片段 {index}",
    )


class _FakeContextBuilder:
    def __init__(self, evidence: list[Evidence]) -> None:
        self._evidence = evidence
        self.calls: list[dict[str, Any]] = []

    async def build(self, user_id: UUID, query: str, *, top_k: int = 8) -> EvidenceContext:
        self.calls.append({"user_id": user_id, "query": query, "top_k": top_k})
        text = "\n\n".join(
            f"[E{index}] doc={item.doc_id} chunk={item.chunk_id}\n{item.excerpt}"
            for index, item in enumerate(self._evidence, start=1)
        )
        return EvidenceContext(
            text=text,
            evidence=list(self._evidence),
            insufficient=len(self._evidence) < 2,
            query=query,
        )


class _FakeArxivSource:
    def __init__(self, records: list[LiteratureRecord]) -> None:
        self._records = records
        self.calls: list[LiteratureSearch] = []

    async def search(self, search: LiteratureSearch) -> list[LiteratureRecord]:
        self.calls.append(search)
        return list(self._records)


def _record(source_id: str) -> LiteratureRecord:
    return LiteratureRecord(
        source_id=source_id,
        title=f"Paper {source_id}",
        authors=["Alice"],
        abstract="abstract",
        published_at="2024-01-15T12:00:00Z",
        source_url=f"https://arxiv.org/abs/{source_id}",
        pdf_url=f"https://arxiv.org/pdf/{source_id}",
        categories=["cs.CL"],
    )


class _FakeValidatingAgent:
    def __init__(self, report: ValidationReport) -> None:
        self._report = report
        self.calls: list[dict[str, Any]] = []

    async def execute(self, input_data: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(input_data)
        return self._report.model_dump(mode="json")


class _FakeReviewingAgent:
    def __init__(self, report: ReviewReport) -> None:
        self._report = report
        self.calls: list[dict[str, Any]] = []

    async def execute(self, input_data: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(input_data)
        return self._report.model_dump(mode="json")


def _validation_report() -> ValidationReport:
    return ValidationReport(
        passed=True,
        score=90.0,
        checks=[ValidationCheck(name="citation_markers_resolve", passed=True, message="ok")],
        retryable=False,
        feedback="",
    )


def _review_report() -> ReviewReport:
    return ReviewReport(
        final_markdown="# 综述\n\n内容 [E1]",
        bibliography=[{"source_id": "2401.12341"}],
        scores={"quality": 0.9},
        issues=[],
        stats={},
    )


# ===== 计划契约 =====


class TestLiteraturePlanContract:
    async def test_deterministic_planner_satisfies_contract(self) -> None:
        workflow = LiteratureReviewWorkflow(planner=PlannerAgent())
        plan = await workflow.plan(_task())

        assert 3 <= len(plan.research_questions) <= 10
        dag = PlannerAgent.build_dag(plan)
        types = {node.type for node in plan.nodes}
        assert {"search", "extract", "write", "validate", "review"} <= types

        reviews = [node for node in plan.nodes if node.type == "review"]
        assert reviews, "缺少 review 节点"
        assert any(not dag.get_successors(node.id) for node in reviews)

        for node in plan.nodes:
            if node.type != "write":
                continue
            predecessors = dag.get_predecessors(node.id)
            assert any(
                dag.get_node(pred).get("type") == "extract" for pred in predecessors
            ), f"write 节点 {node.id} 未依赖 extract 节点"

    async def test_llm_plan_satisfies_contract(self) -> None:
        llm = MockLLM(responses=[_llm_response(_literature_plan_payload())])
        workflow = LiteratureReviewWorkflow(planner=PlannerAgent(llm=llm))
        plan = await workflow.plan(_task())

        assert [node.id for node in plan.nodes] == ["n1", "n2", "n3", "n4", "n5"]
        assert llm.call_count == 1

    async def test_invalid_plan_repairs_once_then_requires_human(self) -> None:
        llm = MockLLM(
            responses=[
                _llm_response(_incomplete_plan_payload()),
                _llm_response(_incomplete_plan_payload()),
            ]
        )
        workflow = LiteratureReviewWorkflow(planner=PlannerAgent(llm=llm))

        with pytest.raises(HumanActionRequiredError):
            await workflow.plan(_task())

        assert llm.call_count == 2
        second_user_prompt = llm.call_history[1]["messages"][-1].content
        assert "修复" in second_user_prompt or "schema" in second_user_prompt

    async def test_generic_planner_path_stays_three_nodes(self) -> None:
        """未指定文献契约时保持 Plan 2 的三节点降级计划（退出验收依赖）。"""
        result = await PlannerAgent().execute({"requirement": "写一份报告"})
        assert len(result["plan"]["nodes"]) == 3
        assert result["dag"]["topological_order"] == ["n1", "n2", "n3"]


# ===== 证据约束执行 =====


class TestEvidenceConstrainedExecution:
    async def test_executor_keeps_only_provided_evidence(self) -> None:
        evidence = [_evidence(1), _evidence(2)]
        llm = MockLLM(
            responses=[make_text_response("RAG 提升可靠性 [E1]；多智能体协作补充 [E2]。")]
        )
        executor = ExecutorAgent(llm=llm)

        result = await executor.execute_result(
            node_id="n3",
            title="撰写初稿",
            description="write: 撰写初稿",
            evidence=evidence,
            evidence_text="[E1] 证据片段 1\n\n[E2] 证据片段 2",
        )

        assert isinstance(result, ExecutionResult)
        assert [item.chunk_id for item in result.evidence] == [
            evidence[0].chunk_id,
            evidence[1].chunk_id,
        ]
        assert "[E1]" in result.content and "[E2]" in result.content
        assert result.warnings == []

    async def test_executor_rejects_invented_marker(self) -> None:
        evidence = [_evidence(1), _evidence(2)]
        llm = MockLLM(responses=[make_text_response("结论需要 [E9] 支撑。")])
        executor = ExecutorAgent(llm=llm)

        with pytest.raises(EvidenceCitationError):
            await executor.execute_result(
                node_id="n3",
                title="撰写初稿",
                description="write: 撰写初稿",
                evidence=evidence,
                evidence_text="[E1] 证据片段 1",
            )

    async def test_workflow_execute_node_uses_scoped_context(self) -> None:
        evidence = [_evidence(1), _evidence(2)]
        builder = _FakeContextBuilder(evidence)
        llm = MockLLM(responses=[make_text_response("综述结论 [E1][E2]")])
        workflow = LiteratureReviewWorkflow(
            planner=PlannerAgent(),
            executor=ExecutorAgent(llm=llm),
            context_builder=builder,
        )
        task = _task()
        plan = await workflow.plan(task)
        write_node = next(node for node in plan.nodes if node.type == "write")

        result = await workflow.execute_node(task, write_node)

        assert builder.calls and builder.calls[0]["user_id"] == USER_ID
        assert isinstance(result, ExecutionResult)
        assert len(result.evidence) == 2
        assert result.node_id == write_node.id

    async def test_prepare_node_flags_insufficient_evidence(self) -> None:
        builder = _FakeContextBuilder([_evidence(1)])
        workflow = LiteratureReviewWorkflow(planner=PlannerAgent(), context_builder=builder)
        task = _task()
        plan = await workflow.plan(task)
        write_node = next(node for node in plan.nodes if node.type == "write")

        prepared = await workflow.prepare_node(task, write_node)

        assert prepared["insufficient_evidence"] is True
        assert prepared["node_type"] == "write"
        assert "[E1]" in prepared["evidence_context"]

    async def test_search_node_uses_arxiv_source(self) -> None:
        source = _FakeArxivSource([_record("2401.12341v1"), _record("2401.12342v1")])
        workflow = LiteratureReviewWorkflow(planner=PlannerAgent(), source=source)
        task = _task()
        plan = await workflow.plan(task)
        search_node = next(node for node in plan.nodes if node.type == "search")

        result = await workflow.execute_node(task, search_node)

        assert len(source.calls) == 1
        assert source.calls[0].query
        assert "2401.12341v1" in result.content
        assert len(result.artifacts) == 2


class TestSearchMaxResultsClamping:
    """`max_results` 必须被**夹紧**到契约区间，而不是让任务失败。

    真实缺陷（2026-10-21 浏览器 E2E 暴露）：规划节点参数由 LLM 自由生成，
    它把 `max_results` 写成 60（用户要求"找 60 篇"时很自然），而
    `LiteratureSearch.max_results` 的契约是 `le=50` → pydantic 校验错 →
    执行器重试 4 次（换策略也改不了这个数字）→ 节点转 `waiting_human`，
    任务永远跑不到 completed，用户在界面上只看到"卡住"。

    正确行为：超出区间时**夹到最近合法值**并继续（PRISMA/可用性都接受
    "最多取 50 条"），而不是让整篇综述失败。
    """

    async def test_clamps_max_results_above_contract_limit(self) -> None:
        from app.tools.literature.models import MAX_SEARCH_RESULTS

        source = _FakeArxivSource([_record("2401.12341v1")])
        workflow = LiteratureReviewWorkflow(planner=PlannerAgent(), source=source)
        task = _task()
        plan = await workflow.plan(task)
        search_node = next(node for node in plan.nodes if node.type == "search")
        search_node.input = {**search_node.input, "max_results": 60}

        await workflow.execute_node(task, search_node)

        assert source.calls[0].max_results == MAX_SEARCH_RESULTS

    async def test_clamps_zero_and_negative_to_one(self) -> None:
        source = _FakeArxivSource([_record("2401.12341v1")])
        workflow = LiteratureReviewWorkflow(planner=PlannerAgent(), source=source)
        task = _task()
        plan = await workflow.plan(task)
        search_node = next(node for node in plan.nodes if node.type == "search")

        for bad in (0, -5):
            search_node.input = {**search_node.input, "max_results": bad}
            await workflow.execute_node(task, search_node)
            assert source.calls[-1].max_results == 1, f"max_results={bad} 应夹到 1"

    async def test_tolerates_non_numeric_max_results(self) -> None:
        """LLM 可能给字符串或噪声值 —— 退回默认值，不要崩。"""
        source = _FakeArxivSource([_record("2401.12341v1")])
        workflow = LiteratureReviewWorkflow(planner=PlannerAgent(), source=source)
        task = _task()
        plan = await workflow.plan(task)
        search_node = next(node for node in plan.nodes if node.type == "search")
        search_node.input = {**search_node.input, "max_results": "很多"}

        await workflow.execute_node(task, search_node)

        assert 1 <= source.calls[-1].max_results <= 50

    async def test_respects_valid_value_unchanged(self) -> None:
        source = _FakeArxivSource([_record("2401.12341v1")])
        workflow = LiteratureReviewWorkflow(planner=PlannerAgent(), source=source)
        task = _task()
        plan = await workflow.plan(task)
        search_node = next(node for node in plan.nodes if node.type == "search")
        search_node.input = {**search_node.input, "max_results": 7}

        await workflow.execute_node(task, search_node)

        assert source.calls[0].max_results == 7


# ===== 校验与复盘缝 =====


class TestWorkflowValidationAndReview:
    async def test_validate_node_returns_report(self) -> None:
        validator = _FakeValidatingAgent(_validation_report())
        workflow = LiteratureReviewWorkflow(planner=PlannerAgent(), validator=validator)
        task = _task()
        result = ExecutionResult(node_id="n3", content="内容 [E1]", duration_ms=5)

        report = await workflow.validate_node(task, result)

        assert isinstance(report, ValidationReport)
        assert report.passed is True
        assert validator.calls[0]["node_id"] == "n3"

    async def test_review_returns_report(self) -> None:
        reviewer = _FakeReviewingAgent(_review_report())
        workflow = LiteratureReviewWorkflow(planner=PlannerAgent(), reviewer=reviewer)
        task = _task()
        results = [ExecutionResult(node_id="n3", content="内容 [E1]", duration_ms=5)]

        report = await workflow.review(task, results)

        assert isinstance(report, ReviewReport)
        assert report.final_markdown.startswith("# 综述")
        assert reviewer.calls[0]["results"][0]["node_id"] == "n3"
        assert reviewer.calls[0]["task_id"] == str(task.id)

    async def test_review_requires_results(self) -> None:
        workflow = LiteratureReviewWorkflow(planner=PlannerAgent(), reviewer=None)
        with pytest.raises(HumanActionRequiredError):
            await workflow.review(_task(), [])


# ===== Prompt 渲染 =====


class TestPromptRendering:
    def test_planner_prompt_renders_through_prompt_manager(self) -> None:
        prompt = render_planner_prompt("检索增强生成的评估方法")
        assert "检索增强生成的评估方法" in prompt
        assert "search" in prompt and "review" in prompt
        assert "JSON" in prompt

    def test_planner_prompt_includes_repair_section(self) -> None:
        prompt = render_planner_prompt(
            "检索增强生成的评估方法", repair_section="上一次输出缺少 review 节点"
        )
        assert "缺少 review 节点" in prompt

    def test_executor_prompt_states_untrusted_text_and_inference_label(self) -> None:
        prompt = render_executor_prompt(
            title="撰写初稿",
            description="write: 撰写初稿",
            evidence_text="[E1] 证据片段",
        )
        assert "不可信" in prompt
        assert "模型推断" in prompt
        assert "[E1] 证据片段" in prompt


class TestDependencyMarkerRebasing:
    """汇总/写节点的依赖文本必须重定位 `[E{n}]`，否则引用无法解析（Exit Gate 实跑发现）。"""

    def _evidence(self, doc_id, chunk_id, excerpt="x"):
        from app.orchestration.contracts import Evidence

        return Evidence(
            doc_id=doc_id,
            chunk_id=chunk_id,
            source_url="https://arxiv.org/abs/2401.00001v1",
            excerpt=excerpt,
        )

    def test_rebases_markers_to_target_pool(self) -> None:
        from app.orchestration.workflows.literature_review import LiteratureReviewWorkflow

        dep_evidence = [self._evidence(uuid4(), uuid4()), self._evidence(uuid4(), uuid4())]
        target_evidence = [dep_evidence[1], self._evidence(uuid4(), uuid4())]
        workflow = LiteratureReviewWorkflow()
        workflow._prepared["n2"] = {
            "evidence": [item.model_dump(mode="json") for item in dep_evidence]
        }

        rebased = workflow._rebased_dependency_outputs(
            {"n2": "见 [E1] 与 [E2] 及 [E9]"}, target_evidence
        )

        assert rebased["n2"] == "见 与 [E1] 及"
        assert "[E2]" not in rebased["n2"]

    def test_summarize_strips_foreign_markers(self) -> None:
        from app.orchestration.workflows.literature_review import LiteratureReviewWorkflow

        dep_evidence = [self._evidence(uuid4(), uuid4())]
        workflow = LiteratureReviewWorkflow()
        workflow._prepared["n3"] = {
            "evidence": [item.model_dump(mode="json") for item in dep_evidence]
        }
        content = workflow._summarize_dependencies({"n3": "正文 [E1] 结束"}, [])
        assert "[E1]" not in content
        assert "正文" in content
        # 带目标证据时可重定位
        content2 = workflow._summarize_dependencies(
            {"n3": "正文 [E1] 结束"}, target_evidence=dep_evidence
        )
        assert "[E1]" in content2
