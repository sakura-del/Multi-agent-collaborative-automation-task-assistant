"""规则校验必须区分内容角色（2026-10-27 review 长度上限缺陷）。

## 缺陷

真实任务 `bdeaa228` 的 `n7 review` 节点：

```
rules: 长度超限：10230 > 10000
```

超限的是 review 节点的**上游机械汇总**（`_summarize_dependencies`），
不是作者正文。它随上游节点数量与内容线性增长 → **必然超限**，
而且**重试无法修复**（重试只会重新汇总同样内容）。

这与 Issue #6 是同一类错误：把**作者级检查**施加到**机器汇总内容**上。
Issue #6 当时修了 `citation_validator` 的 `duplicate_paragraph_ratio`，
但 `ValidatorAgent._check_rules` 的 `max_length` **没跟着修** ——
`content_role` 只传给了引用校验，规则校验根本没用它。

## 为什么不是「调大 max_length」

调大只是把阈值推到更远处，上游再多两个节点照样超限。
根因是**对错误的对象施加了这条规则**：`max_length` 是「别写太长」的写作约束，
对机器汇总没有意义 —— 汇总该有多长就有多长。
"""

from __future__ import annotations

import pytest

from app.orchestration.agents.validator import ValidatorAgent
from app.orchestration.contracts import ExecutionResult

LONG_CONTENT = "很长的内容。" * 2000  # 12000 字符，超过默认 max_length=10000


class _FakeTask:
    """最小任务替身（`validate_node` 只读 id / config / user_id）。"""

    def __init__(self, user_id: object) -> None:
        self.id = "task-1"
        self.user_id = user_id
        self.config: dict[str, object] = {"validation_enabled": True}


def _agent(**rules: object) -> ValidatorAgent:
    return ValidatorAgent(llm=None, rules=dict(rules))


class TestRuleContentRole:
    def test_authored_content_still_limited(self) -> None:
        """作者正文仍然受 max_length 约束（不回归）。"""
        report = _agent(max_length=100).check_rules(LONG_CONTENT, content_role="authored")

        assert report["passed"] is False
        assert any("长度超限" in issue for issue in report["issues"])

    def test_aggregated_content_not_limited(self) -> None:
        """机械汇总不受 max_length 约束（本缺陷的核心修复）。"""
        report = _agent(max_length=100).check_rules(LONG_CONTENT, content_role="aggregated")

        assert report["passed"] is True
        assert report["issues"] == []

    def test_aggregated_still_checks_min_length(self) -> None:
        """`min_length` 对汇总内容仍然有意义（空汇总确实是问题）。"""
        report = _agent(min_length=50).check_rules("太短", content_role="aggregated")

        assert report["passed"] is False
        assert any("长度不足" in issue for issue in report["issues"])

    def test_aggregated_still_checks_keywords(self) -> None:
        """`required_keywords` 对汇总内容仍适用。"""
        report = _agent(required_keywords=["必须出现"]).check_rules(
            "没有那个词", content_role="aggregated"
        )

        assert report["passed"] is False
        assert any("缺失关键词" in issue for issue in report["issues"])

    def test_default_role_is_authored(self) -> None:
        """不传 content_role 时按 authored 从严处理（与既有行为一致）。"""
        report = _agent(max_length=100).check_rules(LONG_CONTENT)

        assert report["passed"] is False

    def test_unknown_role_falls_back_to_authored(self) -> None:
        """非法角色值一律按 authored（从严，不静默放宽）。"""
        report = _agent(max_length=100).check_rules(LONG_CONTENT, content_role="bogus")

        assert report["passed"] is False


class TestValidateUsesContentRole:
    """`validate()` 主流程必须把 content_role 传给规则校验。"""

    async def test_validate_aggregated_over_limit_passes_rule_check(self) -> None:
        report = await _agent(max_length=100).execute(
            {
                "title": "复盘",
                "result": LONG_CONTENT,
                "content_role": "aggregated",
                "node_id": "n7",
            }
        )

        rule_check = report["rule_check"]
        assert rule_check["passed"] is True

    async def test_validate_authored_over_limit_fails_rule_check(self) -> None:
        report = await _agent(max_length=100).execute(
            {
                "title": "撰写",
                "result": LONG_CONTENT,
                "content_role": "authored",
                "node_id": "n5",
            }
        )

        rule_check = report["rule_check"]
        assert rule_check["passed"] is False
        assert any("长度超限" in issue for issue in rule_check["issues"])

    async def test_review_node_regression_real_length(self) -> None:
        """复现真实任务场景：10230 字符的 review 汇总不该被判失败。"""
        content = "综述汇总内容。" * 1462  # ≈10230 字符
        report = await _agent().execute(
            {
                "title": "终审综述",
                "result": content,
                "content_role": "aggregated",
                "node_id": "n7",
            }
        )

        assert report["rule_check"]["passed"] is True
        assert not any("长度超限" in issue for issue in report["rule_check"]["issues"])


class TestRealTaskPayloadShape:
    """锁住生产载荷形状（AGENTS §5 #33）。

    `content_role` 由工作流写入 `input_data`，校验 Agent 从
    `input_data["content_role"]` 读。这个形状必须与真实调用一致。
    """

    async def test_content_role_read_from_input_data(self) -> None:
        agent = _agent(max_length=10)
        payload = {
            "title": "复盘",
            "result": "长" * 100,
            "content_role": "aggregated",
            "node_id": "n7",
            "trace_id": "t-1",
        }
        report = await agent.execute(payload)

        assert report["rule_check"]["passed"] is True

    async def test_missing_content_role_defaults_to_authored(self) -> None:
        agent = _agent(max_length=10)
        payload = {"title": "复盘", "result": "长" * 100, "node_id": "n7"}
        report = await agent.execute(payload)

        assert report["rule_check"]["passed"] is False


@pytest.mark.parametrize("role", ["aggregated", "authored"])
def test_check_rules_is_pure_and_idempotent(role: str) -> None:
    agent = _agent(max_length=100)
    first = agent.check_rules(LONG_CONTENT, content_role=role)  # type: ignore[arg-type]
    second = agent.check_rules(LONG_CONTENT, content_role=role)  # type: ignore[arg-type]

    assert first == second


class TestWorkflowPassesRoleToValidator:
    """端到端锁住：`review` 节点的 `content_role` 必须真的到达规则校验。

    这是「假绿」防线（AGENTS §5 #33）：单测 `check_rules(role="aggregated")`
    全绿，但工作流若没把角色传下去，真实任务照样失败。

    已知隐患：`_node_type()` 查不到时返回**空串**，
    `content_role_for_node("")` 按 `authored` 从严 → 缺陷复发。
    """

    def test_review_node_maps_to_aggregated(self) -> None:
        from app.orchestration.workflows.literature_review import content_role_for_node

        assert content_role_for_node("review") == "aggregated"
        assert content_role_for_node("validate") == "aggregated"
        assert content_role_for_node("search") == "aggregated"

    def test_write_node_maps_to_authored(self) -> None:
        from app.orchestration.workflows.literature_review import content_role_for_node

        assert content_role_for_node("write") == "authored"
        assert content_role_for_node("extract") == "authored"

    def test_empty_node_type_falls_back_to_authored(self) -> None:
        """`_node_type()` 返回空串时按 authored 处理 —— 这是**已知隐患**。

        它意味着「角色推断失败」会退回到严格检查。本测试锁住当前行为，
        提醒后来者：修 `_node_type` 的返回兜底要同步更新这里。
        """
        from app.orchestration.workflows.literature_review import content_role_for_node

        assert content_role_for_node("") == "authored"

    async def test_validator_receives_aggregated_for_review(self) -> None:
        """工作流 → 校验器：review 节点的长汇总必须通过规则校验。"""
        import uuid

        from app.orchestration.workflows.literature_review import LiteratureReviewWorkflow

        validator = ValidatorAgent(llm=None, rules={"min_length": 1, "required_keywords": []})
        workflow = LiteratureReviewWorkflow(validator=validator)

        long_content = "汇总。" * 2000  # 6000 字符
        result = ExecutionResult(
            node_id="n7",
            content=long_content,
            duration_ms=0,
            node_type="review",
        )
        # 无 plan 绑定 → `_node_type` 走 prepared/plan 都查不到，
        # 只能靠 result.node_type。这里验证它确实被用上。
        report = await workflow.validate_node(_FakeTask(uuid.uuid4()), result)

        assert report.passed is True, f"review 汇总不该失败：{report.feedback}"

    async def test_search_node_skips_llm_semantic_check(self) -> None:
        """确定性 search 节点不走 LLM 语义评审，避免引用库模式死循环重试转人工（Issue #58）。"""
        import json

        from app.llm.models import LLMResponse, MessageRole, Usage
        from tests.llm.mock_llm import MockLLM

        hostile_llm = MockLLM(
            responses=[
                LLMResponse(
                    content=json.dumps(
                        {
                            "logic_consistent": False,
                            "factual_accurate": False,
                            "issues": ["文献条数少于预期"],
                            "suggestions": ["补充检索"],
                        }
                    ),
                    finish_reason="stop",
                    usage=Usage(),
                    role=MessageRole.ASSISTANT,
                )
            ]
        )
        validator = ValidatorAgent(
            llm=hostile_llm,
            rules={"min_length": 1, "required_keywords": []},
        )
        report = await validator.execute(
            {
                "node_id": "n1",
                "title": "检索引用库核心文献",
                "result": "- RAG Paper (doi:10.48550/arxiv.2005.11401) https://doi.org/10.48550/arxiv.2005.11401",
                "content_role": "aggregated",
                "execution_result": {
                    "node_id": "n1",
                    "content": "- RAG Paper",
                    "duration_ms": 0,
                    "node_type": "search",
                },
            }
        )

        assert report["passed"] is True
        assert hostile_llm.call_count == 0


class TestReferenceLibraryPlanClamp:
    """引用库模式下的计划阈值钳制（Issue #58）。"""

    def test_clamp_plan_caps_required_evidence_and_search_targets(self) -> None:
        from app.orchestration.contracts import PlanNode, TaskPlan
        from app.orchestration.workflows.literature_review import clamp_plan_for_reference_library

        plan = TaskPlan(
            version=1,
            topic="RAG 引用库闭环综述",
            research_questions=["问题一？", "问题二？", "问题三？"],
            search_queries=["RAG"],
            acceptance_criteria=["可回溯引用"],
            nodes=[
                PlanNode(
                    id="n1",
                    type="search",
                    title="检索核心文献",
                    dependencies=[],
                    input={"queries": ["RAG"], "target_count": 12, "max_results": 15},
                    required_evidence=12,
                ),
                PlanNode(
                    id="n2",
                    type="extract",
                    title="抽取证据",
                    dependencies=["n1"],
                    input={"total_target": 20},
                    required_evidence=8,
                ),
                PlanNode(
                    id="n3",
                    type="write",
                    title="撰写综述",
                    dependencies=["n2"],
                    input={"reference_count": 20},
                    required_evidence=20,
                ),
                PlanNode(
                    id="n4",
                    type="validate",
                    title="校验引用",
                    dependencies=["n3"],
                    input={},
                    required_evidence=0,
                ),
                PlanNode(
                    id="n5",
                    type="review",
                    title="终审综述",
                    dependencies=["n4"],
                    input={},
                    required_evidence=10,
                ),
            ],
        )

        clamped = clamp_plan_for_reference_library(plan, 2)
        by_id = {node.id: node for node in clamped.nodes}

        assert by_id["n1"].required_evidence == 2
        assert by_id["n1"].input["target_count"] == 2
        assert by_id["n1"].input["max_results"] == 2
        assert by_id["n2"].required_evidence == 2
        assert by_id["n2"].input["total_target"] == 2
        assert by_id["n3"].required_evidence == 2
        assert by_id["n3"].input["reference_count"] == 2
        # 原为 0 的节点保持 0
        assert by_id["n4"].required_evidence == 0
        assert by_id["n5"].required_evidence == 2
