"""画像开关在**工作流**层生效的测试（2026-10-22 实验矩阵 Task 2）。

## 为什么必须有这组测试

四个实验画像（`single_agent_no_rag` / `multi_agent_no_rag` /
`multi_agent_vector_rag` / `multi_agent_hybrid_rerank`）要在同一套代码上
跑出**可测量的差异**，否则论文的「消融实验」章节不成立。

若开关只加进契约、工作流不读，四组会走完全相同的路径，
差异只剩模型采样噪声 —— 那是**假实验**。

## 三个开关的生效点

| 开关 | 生效点 | 关闭时的行为 |
| --- | --- | --- |
| `retrieval_mode=none` | `prepare_node` 取证据 | 不调 `knowledge_query`，evidence 为空 |
| `validation_enabled=False` | `validate_node` | **不调 validator**（不花 LLM），直接返回通过 |
| `review_enabled=False` | `_finish_reviewing` | **不调 reviewer**，用 write 输出作最终正文 |

## 为什么「关闭 = 跳过执行」而不是「不规划」

`REQUIRED_NODE_TYPES` 要求规划必须含 validate/review 节点（契约校验）。
若关闭就不规划，DAG 结构会变，实验对比不干净。
所以：**规划照旧、执行跳过**。
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.orchestration.contracts import (
    Evidence,
    ExecutionResult,
    LiteratureReviewRequest,
)
from app.orchestration.workflows.literature_review import LiteratureReviewWorkflow


class _SpyValidator:
    """记录调用次数的假校验器。"""

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        return {
            "passed": True,
            "score": 95.0,
            "checks": [],
            "retryable": False,
            "feedback": "ok",
        }


class _StubContextBuilder:
    """最小证据上下文构建器替身（只返回空证据集）。

    真实构建器需要 Chroma + 语料入库；本文件只验证**开关是否生效**，
    不需要真实检索内容。
    """

    def __init__(self) -> None:
        self.calls = 0

    async def build(self, *args: Any, **kwargs: Any) -> Any:
        from types import SimpleNamespace

        self.calls += 1
        return SimpleNamespace(text="", evidence=[], insufficient=True)


class _FakeTask:
    """最小任务替身（工作流只读这几个属性）。"""

    def __init__(self, **config: Any) -> None:
        self.id = uuid.uuid4()
        self.title = "检索增强生成综述"
        self.config = config
        self.keywords = ["RAG"]
        # `_build_context` 要求 user_id 是 UUID（owner-scoped 检索）
        self.user_id = uuid.uuid4()


def _result(node_id: str = "n1") -> ExecutionResult:
    return ExecutionResult(
        node_id=node_id,
        content="正文 [E1]",
        evidence=[Evidence(doc_id=uuid.uuid4(), chunk_id=uuid.uuid4(), excerpt="证据原文")],
        duration_ms=0,
        node_type="write",
    )


class TestValidationSwitch:
    @pytest.mark.asyncio
    async def test_validation_enabled_calls_validator(self) -> None:
        validator = _SpyValidator()
        workflow = LiteratureReviewWorkflow(
            validator=validator, context_builder=_StubContextBuilder()
        )
        task = _FakeTask(validation_enabled=True)

        report = await workflow.validate_node(task, _result())

        assert validator.calls == 1
        assert report.passed is True

    @pytest.mark.asyncio
    async def test_validation_disabled_skips_llm(self) -> None:
        """**核心断言**：关闭校验后**一次都不调** validator（省 LLM 成本）。"""
        validator = _SpyValidator()
        workflow = LiteratureReviewWorkflow(
            validator=validator, context_builder=_StubContextBuilder()
        )
        task = _FakeTask(validation_enabled=False)

        report = await workflow.validate_node(task, _result())

        assert validator.calls == 0, "关闭校验后不应调用校验 Agent"
        assert report.passed is True, "跳过校验应视为通过（否则任务会卡住）"

    @pytest.mark.asyncio
    async def test_disabled_validation_marks_skipped(self) -> None:
        """跳过时应在报告里留痕（否则事后无法判断「真通过」还是「没校验」）。"""
        workflow = LiteratureReviewWorkflow(
            validator=_SpyValidator(), context_builder=_StubContextBuilder()
        )
        task = _FakeTask(validation_enabled=False)

        report = await workflow.validate_node(task, _result())

        assert "跳过" in report.feedback, f"跳过的校验应留痕，实际 feedback={report.feedback!r}"


class TestRetrievalSwitch:
    @pytest.mark.asyncio
    async def test_retrieval_none_yields_no_evidence(self) -> None:
        """`retrieval_mode=none`：不检索 → evidence 为空。"""

        class _SpyKnowledge:
            def __init__(self) -> None:
                self.calls = 0

            async def search(self, *args: Any, **kwargs: Any) -> list[Any]:
                self.calls += 1
                return []

        knowledge = _SpyKnowledge()
        workflow = LiteratureReviewWorkflow(knowledge_query=knowledge)
        task = _FakeTask(retrieval_mode="none")

        prepared = await workflow.prepare_node(task, _node("extract"))

        assert prepared["evidence"] == []
        assert knowledge.calls == 0, "none 模式不应调用检索"

    @pytest.mark.asyncio
    async def test_retrieval_vector_calls_context_builder(self) -> None:
        """`vector` / `hybrid_rerank`：**调用证据上下文构建器**（即真检索）。

        注意：检索入口是 `context_builder.build(...)`，不是 `knowledge_query` ——
        后者是构建器内部依赖，工作流不直接调它。首版断言写错了对象
        （断言 `knowledge_query.calls`，永远为 0），已改正。
        """
        builder = _StubContextBuilder()
        workflow = LiteratureReviewWorkflow(context_builder=builder)
        task = _FakeTask(retrieval_mode="vector")

        await workflow.prepare_node(task, _node("extract"))

        assert builder.calls == 1, "vector 模式应调用证据上下文构建器"

    @pytest.mark.asyncio
    async def test_retrieval_none_does_not_call_context_builder(self) -> None:
        """对照：`none` 模式**一次都不调**构建器（这是消融的关键差异）。"""
        builder = _StubContextBuilder()
        workflow = LiteratureReviewWorkflow(context_builder=builder)
        task = _FakeTask(retrieval_mode="none")

        await workflow.prepare_node(task, _node("extract"))

        assert builder.calls == 0, "none 模式不应调用检索"


class TestRequestCarriesSwitches:
    def test_contract_exposes_all_three(self) -> None:
        request = LiteratureReviewRequest(
            topic="检索增强生成综述",
            keywords=["RAG"],
            retrieval_mode="none",
            validation_enabled=False,
            review_enabled=False,
        )

        assert request.retrieval_mode == "none"
        assert request.validation_enabled is False
        assert request.review_enabled is False


def _node(node_type: str) -> Any:
    from app.orchestration.contracts import PlanNode

    return PlanNode(id="n1", type=node_type, title="测试节点")
