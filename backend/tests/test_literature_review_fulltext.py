"""文献综述工作流的 OA 全文抓取接入测试（全文获取 Phase A · Task 7）。

覆盖计划 Task 7 Step 1 的场景与装配层零外呼约束：

1. 全文开启且非离线/非引用库：检索节点调用全文服务一次，
   `ExecutionResult.stats["fulltext_summary"]` 与 `FulltextStats.as_dict()` 自洽，
   并随 `LiteratureNodeExecutor` 的 `execution_result` 进入节点 output（落库形状）。
2. 全部失败：检索节点仍返回 completed 形状（content/artifacts 不变），
   `succeeded == 0` 且有降级 warning；证据准备仍走既有 metadata 证据。
3. 离线数据集 / 引用库 / `full_text=false` / 未注入服务：一次都不调用全文服务（零外呼）。
4. 全文服务抛异常：不拖垮检索节点，只记 warning（按全失败处理）。
5. 装配层：非 OA 场景不构造全文服务（零外呼）；全局开关关闭时不构造。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.knowledge.services.fulltext_service import FulltextService, FulltextStats
from app.orchestration.contracts import EvidenceContext, ExecutionResult, TaskPlan
from app.orchestration.runtime import RuntimeContainer
from app.orchestration.services.task_execution import TaskExecution
from app.orchestration.workflows.literature_review import (
    FULLTEXT_SUMMARY_KEY,
    FULLTEXT_WARNING_ALL_FAILED,
    FULLTEXT_WARNING_FAILED,
    FULLTEXT_WARNING_PARTIAL,
    LiteratureNodeExecutor,
    LiteratureReviewWorkflow,
)
from app.tools.literature.models import LiteratureRecord, LiteratureSearch
from tests.test_literature_workflow import USER_ID, _literature_plan_payload, _record

SEARCH_NODE_ID = "n1"
EXTRACT_NODE_ID = "n2"


# ===== 夹具与替身 =====


def _plan() -> TaskPlan:
    return TaskPlan.model_validate(_literature_plan_payload())


def _node(node_id: str) -> Any:
    return next(node for node in _plan().nodes if node.id == node_id)


def _task(config: dict[str, Any] | None = None) -> Any:
    return SimpleNamespace(
        id=uuid4(),
        user_id=USER_ID,
        title="检索增强生成的评估方法",
        description="系统梳理评估方法与证据可追溯性",
        task_type="literature_review",
        config={"trace_id": "trace-fulltext", **(config or {})},
    )


def _workflow(
    *,
    source: Any,
    fulltext_service: Any = None,
    context_builder: Any = None,
) -> LiteratureReviewWorkflow:
    return LiteratureReviewWorkflow(
        context_builder=context_builder,
        source=source,
        fulltext_service=fulltext_service,
    ).use_plan(_plan())


class _FakeSource:
    def __init__(self, records: list[LiteratureRecord]) -> None:
        self._records = list(records)
        self.calls: list[LiteratureSearch] = []

    async def search(self, search: LiteratureSearch) -> list[LiteratureRecord]:
        self.calls.append(search)
        return list(self._records)


class _FakeFulltextService:
    def __init__(
        self, stats: FulltextStats | None = None, *, error: Exception | None = None
    ) -> None:
        self.stats = stats or FulltextStats()
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def enrich(self, user_id: UUID, records: list[LiteratureRecord]) -> FulltextStats:
        self.calls.append({"user_id": user_id, "records": list(records)})
        if self.error is not None:
            raise self.error
        return self.stats


class _FakeContextBuilder:
    """空证据上下文：让证据池只保留检索节点的 metadata 证据（降级路径）。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def build(self, user_id: UUID, query: str, *, top_k: int = 8) -> EvidenceContext:
        self.calls.append(query)
        return EvidenceContext(text="", evidence=[], insufficient=True, query=query)


class _StubLlm:
    """占位 LLM：装配工作流只需对象存在，测试不发起模型调用。"""


# ===== 1. 检索后抓取 + 统计 =====


class TestSearchFulltextEnrichment:
    async def test_enriches_after_search_and_records_summary_stats(self) -> None:
        records = [_record("2401.12341v1"), _record("2401.12342v1")]
        source = _FakeSource(records)
        fulltext = _FakeFulltextService(FulltextStats(attempted=2, succeeded=2))

        result = await _workflow(source=source, fulltext_service=fulltext).execute_node(
            _task(), _node(SEARCH_NODE_ID)
        )

        assert len(source.calls) == 1
        assert len(fulltext.calls) == 1
        assert fulltext.calls[0]["user_id"] == USER_ID
        assert [item.source_id for item in fulltext.calls[0]["records"]] == [
            "2401.12341v1",
            "2401.12342v1",
        ]
        assert result.stats == {
            FULLTEXT_SUMMARY_KEY: {
                "attempted": 2,
                "succeeded": 2,
                "fallback_metadata": 0,
                "scanned_skipped": 0,
                "failed": 0,
            }
        }
        assert result.warnings == []
        # 全文抓取是增量增强：检索结果本身不回退
        assert len(result.artifacts) == 2

    async def test_partial_fallback_warns_but_keeps_search_result(self) -> None:
        records = [_record(f"2401.1234{index}v1") for index in range(3)]
        fulltext = _FakeFulltextService(
            FulltextStats(attempted=3, succeeded=1, fallback_metadata=1, scanned_skipped=1)
        )

        result = await _workflow(
            source=_FakeSource(records), fulltext_service=fulltext
        ).execute_node(_task(), _node(SEARCH_NODE_ID))

        summary = result.stats[FULLTEXT_SUMMARY_KEY]
        assert summary["succeeded"] == 1
        assert (
            summary["attempted"]
            == summary["succeeded"]
            + summary["fallback_metadata"]
            + summary["scanned_skipped"]
            + summary["failed"]
        )
        assert FULLTEXT_WARNING_PARTIAL in result.warnings
        assert len(result.artifacts) == 3

    async def test_executor_output_keeps_summary_for_persistence(self) -> None:
        fulltext = _FakeFulltextService(FulltextStats(attempted=1, succeeded=1))
        workflow = _workflow(
            source=_FakeSource([_record("2401.12341v1")]), fulltext_service=fulltext
        )
        executor = LiteratureNodeExecutor(workflow, _task())

        output = await executor._run({"node_id": SEARCH_NODE_ID, "dependency_outputs": {}})

        assert output["status"] == "completed"
        persisted = output["execution_result"]
        assert persisted["stats"][FULLTEXT_SUMMARY_KEY]["succeeded"] == 1


# ===== 2. 全失败仍可完成：metadata 证据兜底 =====


class TestFulltextFailureFallback:
    async def test_all_failed_falls_back_to_metadata_evidence(self) -> None:
        records = [_record("2401.12341v1"), _record("2401.12342v1")]
        fulltext = _FakeFulltextService(FulltextStats(attempted=2, fallback_metadata=2))
        builder = _FakeContextBuilder()
        workflow = _workflow(
            source=_FakeSource(records),
            fulltext_service=fulltext,
            context_builder=builder,
        )
        task = _task()

        search_result = await workflow.execute_node(task, _node(SEARCH_NODE_ID))

        assert isinstance(search_result, ExecutionResult)
        assert search_result.content
        assert search_result.artifacts
        assert search_result.stats[FULLTEXT_SUMMARY_KEY]["succeeded"] == 0
        assert FULLTEXT_WARNING_ALL_FAILED in search_result.warnings

        # 证据准备与未接入全文时完全一致：检索 artifacts → metadata 证据
        prepared = await workflow.prepare_node(
            task,
            _node(EXTRACT_NODE_ID),
            dependency_outputs={SEARCH_NODE_ID: search_result.model_dump(mode="json")},
        )
        assert [item["kind"] for item in prepared["evidence"]] == ["metadata", "metadata"]
        assert "metadata" in prepared["evidence_context"]

    async def test_service_error_does_not_break_search_node(self) -> None:
        fulltext = _FakeFulltextService(error=RuntimeError("boom"))

        result = await _workflow(
            source=_FakeSource([_record("2401.12341v1")]), fulltext_service=fulltext
        ).execute_node(_task(), _node(SEARCH_NODE_ID))

        assert result.content
        assert FULLTEXT_WARNING_FAILED in result.warnings
        assert FULLTEXT_SUMMARY_KEY not in result.stats


# ===== 3. 零外呼：离线 / 引用库 / 关闭 / 未注入 =====


class TestFulltextZeroExternalCalls:
    @pytest.mark.parametrize(
        "config",
        [
            {"full_text": False},
            {"offline_dataset": True},
            {"reference_library": True},
        ],
    )
    async def test_disabled_modes_never_call_fulltext_service(self, config: dict[str, Any]) -> None:
        fulltext = _FakeFulltextService(FulltextStats(attempted=1, succeeded=1))

        result = await _workflow(
            source=_FakeSource([_record("2401.12341v1")]), fulltext_service=fulltext
        ).execute_node(_task(config), _node(SEARCH_NODE_ID))

        assert fulltext.calls == []
        assert result.stats == {}

    async def test_missing_service_never_calls_anything(self) -> None:
        result = await _workflow(source=_FakeSource([_record("2401.12341v1")])).execute_node(
            _task(), _node(SEARCH_NODE_ID)
        )

        assert result.stats == {}


# ===== 4. 装配层：任务级开关与全局开关（零外呼） =====


class TestTaskExecutionFulltextWiring:
    def _task_stub(self, config: dict[str, Any] | None = None) -> Any:
        from tests.test_task_execution import _plan_payload

        return SimpleNamespace(
            id=uuid4(),
            task_type="literature_review",
            title="检索增强生成",
            description="综述 RAG 的证据核验方法",
            plan=_plan_payload(),
            config=config if config is not None else {},
        )

    def _execution(self) -> TaskExecution:
        container = RuntimeContainer(llm=_StubLlm(), knowledge_query=object())
        return TaskExecution(container)

    @pytest.mark.parametrize(
        "config",
        [
            {"full_text": False},
            {"offline_dataset": True},
            {"reference_library": True},
        ],
    )
    def test_skips_service_for_offline_library_and_disabled(self, config: dict[str, Any]) -> None:
        execution = self._execution()

        assert execution._create_fulltext_service(self._task_stub(config), object()) == (None, None)

    def test_skips_service_for_non_literature_task(self) -> None:
        execution = self._execution()
        task = self._task_stub({})
        task.task_type = "chat"

        assert execution._create_fulltext_service(task, object()) == (None, None)

    def test_honors_global_switch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.orchestration.services import task_execution as module

        monkeypatch.setattr(
            module,
            "get_settings",
            lambda: SimpleNamespace(fulltext=SimpleNamespace(enabled=False)),
        )
        execution = self._execution()

        assert execution._create_fulltext_service(self._task_stub({}), object()) == (None, None)

    def test_constructs_service_for_online_task(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.knowledge.services import factory as kb_factory
        from app.orchestration.services import task_execution as module

        monkeypatch.setattr(kb_factory, "get_storage", lambda: SimpleNamespace())
        monkeypatch.setattr(kb_factory, "get_index_manager", lambda: SimpleNamespace())
        monkeypatch.setattr(kb_factory, "get_kb", lambda: SimpleNamespace())
        monkeypatch.setattr(
            module,
            "get_settings",
            lambda: SimpleNamespace(fulltext=SimpleNamespace(enabled=True)),
        )
        execution = self._execution()

        service, resolver = execution._create_fulltext_service(self._task_stub({}), object())

        assert isinstance(service, FulltextService)
        assert resolver is not None

    def test_workflow_receives_injected_service(self) -> None:
        execution = self._execution()
        sentinel = object()

        workflow = execution._literature_execution_workflow(
            self._task_stub({}), fulltext_service=sentinel
        )

        assert workflow is not None
        assert workflow._fulltext_service is sentinel
