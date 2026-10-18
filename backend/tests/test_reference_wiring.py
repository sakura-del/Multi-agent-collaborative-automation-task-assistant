"""引用真实性核验的配置与编排接线测试（Issue #3 T4 / 计划 §2.5、§2.6）。

覆盖：
1. 配置：`ReferenceVerificationConfig` 默认值、`config/default.yaml` 段、环境变量嵌套覆盖；
2. 运行时容器：惰性构造 `reference_verifier`、`enabled=False → None`、关闭时 aclose HTTP 客户端；
3. 工作流：核验结论经 `validate_node` 进入 `ValidationReport.checks`（advisory 不影响通过）；
4. 校验 Agent：`reference_verifications` 的透传与逐条容错，未提供时保持原行为。

**零真实网络**：所有外部元数据源均用 `_FakeMetadataSource`，HTTP 客户端从不真正建连。
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.config import ReferenceVerificationConfig, Settings, get_settings
from app.knowledge.services.citation_validator import REFERENCE_CHECK, CitationValidator
from app.knowledge.services.reference_verifier import ReferenceVerifier
from app.orchestration.agents.validator import ValidatorAgent
from app.orchestration.contracts import ExecutionResult, ValidationReport
from app.orchestration.workflows.literature_review import LiteratureReviewWorkflow
from app.tools.literature.models import MetadataRecord, ReferenceVerdict, ReferenceVerification

# ===== 测试替身 =====


class _FakeMetadataSource:
    """假元数据源：只实现 `MetadataSource` 协议，绝不发网络请求。"""

    def __init__(self, records: list[MetadataRecord] | None = None) -> None:
        self._records = list(records or [])
        self.lookups: list[str] = []
        self.searches: list[str] = []

    async def lookup_doi(self, doi: str) -> MetadataRecord | None:
        self.lookups.append(doi)
        return None

    async def search_by_title(self, title: str) -> list[MetadataRecord]:
        self.searches.append(title)
        return list(self._records)


class _FakeVerifier:
    """记录调用参数的假核验器（不访问网络，可注入固定结论或异常）。"""

    def __init__(
        self,
        verifications: list[ReferenceVerification] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.calls: list[list[Any]] = []
        self._verifications = list(verifications or [])
        self._error = error

    @property
    def intercept_unverified(self) -> bool:
        return False

    async def verify_many(self, candidates: Any) -> list[ReferenceVerification]:
        self.calls.append(list(candidates))
        if self._error is not None:
            raise self._error
        return list(self._verifications)


class _RecordingValidator:
    """记录输入载荷的假校验 Agent（返回固定报告，便于断言载荷形状）。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute(self, input_data: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(dict(input_data))
        return ValidationReport(
            passed=True, score=100.0, checks=[], retryable=False, feedback=""
        ).model_dump(mode="json")


class _RecordingHttpClient:
    """记录 `aclose()` 是否被调用的假 httpx 客户端。"""

    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class _StubLlm:
    """占位 LLM：校验 Agent 只做确定性引用校验时无需真实模型。"""


# ===== 夹具与工具 =====


@pytest.fixture
def apply_reference_config(monkeypatch):
    """把 `Settings.reference_verification` 临时替换为指定配置。"""

    def _apply(**overrides: Any) -> ReferenceVerificationConfig:
        config = ReferenceVerificationConfig(**overrides)
        monkeypatch.setattr(get_settings(), "reference_verification", config)
        return config

    return _apply


def _artifact(title: str = "Attention Is All You Need", **overrides: Any) -> dict[str, Any]:
    """构造检索节点 artifacts 条目（`LiteratureRecord.model_dump()` 形状）。"""
    payload: dict[str, Any] = {
        "source_id": "2401.00001",
        "title": title,
        "authors": ["Zhang, Wei"],
        "abstract": "摘要",
        "published_at": "2024-01-01T00:00:00+00:00",
        "source_url": "https://arxiv.org/abs/2401.00001",
        "pdf_url": "https://arxiv.org/pdf/2401.00001",
        "categories": ["cs.CL"],
    }
    payload.update(overrides)
    return payload


def _task() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        title="检索增强生成",
        description="综述 RAG 的证据核验方法",
        config={},
    )


def _verification(
    verdict: ReferenceVerdict,
    *,
    key: str = "attention is all you need",
    title: str = "Attention Is All You Need",
    reason: str = "理由",
) -> ReferenceVerification:
    return ReferenceVerification(reference_key=key, title=title, verdict=verdict, reason=reason)


def _validator(*, mode: str = "report", llm: Any | None = None) -> ValidatorAgent:
    """真实校验 Agent + 确定性引用校验器（不调用 LLM 语义分支）。"""
    return ValidatorAgent(
        llm=llm,
        rules={"min_length": 1, "required_keywords": []},
        citation_validator=CitationValidator(reference_mode=mode),  # type: ignore[arg-type]
    )


def _execution_payload(content: str = "文献综述正文，不含引用标记。") -> dict[str, Any]:
    return ExecutionResult(node_id="n1", content=content, duration_ms=1).model_dump(mode="json")


# ===== 1. 配置 =====


class TestReferenceVerificationConfig:
    """计划 §2.5：字段与默认值必须一一对应。"""

    def test_defaults_match_plan(self) -> None:
        config = get_settings().reference_verification

        assert isinstance(config, ReferenceVerificationConfig)
        assert config.enabled is True
        assert config.mode == "report"
        assert config.intercept_unverified is False
        assert config.similarity_threshold == 0.90
        assert config.year_tolerance == 1
        assert config.cache_ttl_seconds == 604800
        assert config.request_timeout_seconds == 20.0
        # R7：默认并发降到 2（未配 contact_email 时公共池约 1 req/s，4 并发必然 429）
        assert config.max_concurrency == 2
        assert config.total_timeout_seconds == 20.0
        # R6：离线清单任务默认不做外部核验（零外呼、可复现）
        assert config.verify_offline_tasks is False
        assert config.contact_email == ""
        assert config.user_agent == (
            "maa-literature-review/0.1 "
            "(+https://github.com/sakura-del/Multi-agent-collaborative-automation-task-assistant)"
        )
        assert config.max_retries == 1

    def test_default_yaml_section_matches_plan(self) -> None:
        from app.core.config import _load_yaml_defaults

        section = _load_yaml_defaults().get("reference_verification")
        assert isinstance(section, dict), "config/default.yaml 缺少 reference_verification 段"
        assert section["enabled"] is True
        assert section["mode"] == "report"
        assert section["intercept_unverified"] is False
        assert section["similarity_threshold"] == 0.90
        assert section["year_tolerance"] == 1
        assert section["cache_ttl_seconds"] == 604800
        assert section["request_timeout_seconds"] == 20.0
        assert section["max_concurrency"] == 2
        assert section["total_timeout_seconds"] == 20.0
        assert section["verify_offline_tasks"] is False
        assert section["contact_email"] == ""
        assert section["max_retries"] == 1
        assert "github.com/sakura-del" in section["user_agent"]

    def test_yaml_documents_polite_pool_hint(self) -> None:
        """R7：yaml 必须提示未配 contact_email 时的公共池限速与 polite pool 用法。"""
        from app.core.config import DEFAULT_CONFIG_PATH

        text = DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")
        section = text.split("reference_verification:", 1)[1]

        assert "polite pool" in section
        assert "REFERENCE_VERIFICATION__CONTACT_EMAIL" in section

    def test_env_nested_override(self, monkeypatch) -> None:
        """环境变量用 `__` 嵌套注入时应正确解析为子模型（需登记 field_validator）。"""
        monkeypatch.setenv("REFERENCE_VERIFICATION__ENABLED", "false")
        monkeypatch.setenv("REFERENCE_VERIFICATION__MODE", "enforce")
        monkeypatch.setenv("REFERENCE_VERIFICATION__SIMILARITY_THRESHOLD", "0.8")
        monkeypatch.setenv("REFERENCE_VERIFICATION__MAX_CONCURRENCY", "7")

        config = Settings().reference_verification

        assert config.enabled is False
        assert config.mode == "enforce"
        assert config.similarity_threshold == 0.8
        assert config.max_concurrency == 7

    def test_none_payload_falls_back_to_defaults(self) -> None:
        """显式传 None 由 `_ensure_dict` 收敛为 {}，退回字段默认值（不再抛校验错误）。"""
        config = Settings(reference_verification=None).reference_verification

        assert config.enabled is True
        assert config.mode == "report"


# ===== 2. 运行时容器 =====


class TestRuntimeContainerReferenceVerifier:
    async def test_lazy_verifier_uses_configured_clients(self, apply_reference_config) -> None:
        from app.orchestration.runtime import RuntimeContainer
        from app.tools.literature.crossref import CrossrefClient
        from app.tools.literature.openalex import OpenAlexClient

        apply_reference_config(
            enabled=True,
            contact_email="team@example.com",
            cache_ttl_seconds=123,
            similarity_threshold=0.75,
            year_tolerance=2,
            max_concurrency=3,
            intercept_unverified=True,
        )
        container = RuntimeContainer()

        verifier = container.reference_verifier

        assert isinstance(verifier, ReferenceVerifier)
        # 惰性：同一实例被缓存，重复访问不重建
        assert container.reference_verifier is verifier
        crossref = verifier._crossref
        openalex = verifier._openalex
        assert isinstance(crossref, CrossrefClient)
        assert isinstance(openalex, OpenAlexClient)
        assert crossref._mailto == "team@example.com"
        assert openalex._mailto == "team@example.com"
        # HTTP 客户端保持惰性：未使用前不建连，避免核验开启即打网络
        assert crossref._client is None
        assert openalex._client is None
        assert verifier.intercept_unverified is True
        assert verifier._cache._ttl == 123

    def test_total_timeout_passed_to_verifier(self, apply_reference_config) -> None:
        """R4：核验总预算由配置传入核验器（超时降级 UNVERIFIED 由核验器内部兜住）。"""
        from app.orchestration.runtime import RuntimeContainer

        apply_reference_config(enabled=True, total_timeout_seconds=7.5)
        container = RuntimeContainer()

        verifier = container.reference_verifier

        assert verifier is not None
        # 属性命名以实现方为准（私有/公开两种命名都接受），取值必须来自配置
        value = getattr(verifier, "_total_timeout", None)
        if value is None:
            value = getattr(verifier, "total_timeout", None)
        assert value == 7.5

    def test_reference_cache_uses_dedicated_redis_client(self, apply_reference_config) -> None:
        """R5①：核验缓存用专用 Redis 客户端（1s 超时），不复用实时推送的全局单例。"""
        import redis.asyncio as aioredis

        from app.core.redis import RedisManager
        from app.orchestration.runtime import RuntimeContainer

        apply_reference_config(enabled=True)
        global_before = RedisManager._client

        container = RuntimeContainer()
        verifier = container.reference_verifier

        assert verifier is not None
        client = verifier._cache._redis
        assert isinstance(client, aioredis.Redis)
        assert client is not RedisManager._client
        # 构造核验器不得触碰实时推送用的全局单例（那条链路需要正常超时）
        assert RedisManager._client is global_before
        kwargs = client.connection_pool.connection_kwargs
        assert kwargs["socket_connect_timeout"] == 1.0
        assert kwargs["socket_timeout"] == 1.0
        assert kwargs["decode_responses"] is True

    def test_disabled_returns_none(self, apply_reference_config) -> None:
        from app.orchestration.runtime import RuntimeContainer

        apply_reference_config(enabled=False)
        container = RuntimeContainer()

        assert container.reference_verifier is None

    async def test_close_closes_http_clients_and_is_idempotent(
        self, apply_reference_config
    ) -> None:
        from app.orchestration.runtime import RuntimeContainer

        apply_reference_config(enabled=True)
        container = RuntimeContainer()
        verifier = container.reference_verifier
        assert verifier is not None
        crossref_recorder = _RecordingHttpClient()
        openalex_recorder = _RecordingHttpClient()
        verifier._crossref._client = crossref_recorder
        verifier._crossref._owns_client = True
        verifier._openalex._client = openalex_recorder
        verifier._openalex._owns_client = True

        await container.close()

        assert crossref_recorder.closed is True
        assert openalex_recorder.closed is True
        # 幂等：重复关闭不得抛异常
        await container.close()

    async def test_close_without_building_verifier_is_safe(self, apply_reference_config) -> None:
        from app.orchestration.runtime import RuntimeContainer

        apply_reference_config(enabled=True)
        container = RuntimeContainer()

        await container.close()

        assert container.reference_verifier is not None

    async def test_close_closes_dedicated_redis_client(
        self, apply_reference_config, monkeypatch
    ) -> None:
        """R5①：容器关闭时释放专用 Redis 客户端，且重复关闭幂等。"""
        from app.orchestration.runtime import RuntimeContainer

        apply_reference_config(enabled=True)
        container = RuntimeContainer()
        verifier = container.reference_verifier
        assert verifier is not None
        client = verifier._cache._redis
        closed: list[Any] = []

        async def _record_aclose(self: Any) -> None:
            closed.append(self)

        monkeypatch.setattr(type(client), "aclose", _record_aclose)

        await container.close()

        assert closed == [client]
        # 幂等：重复关闭不再触碰已释放的客户端
        await container.close()
        assert closed == [client]

    def test_reviewer_agent_receives_verifier(self, apply_reference_config) -> None:
        from app.orchestration.runtime import RuntimeContainer

        apply_reference_config(enabled=True)
        container = RuntimeContainer(llm=_StubLlm())

        agent = container.create_agent("reviewer")

        assert agent.reference_verifier is container.reference_verifier
        assert agent.reference_verifier is not None


# ===== 3. 工作流 validate_node =====


class TestWorkflowReferenceWiring:
    async def test_verdicts_enter_validation_report_checks(self) -> None:
        """核验结论必须经校验 Agent 进入 ValidationReport.checks（advisory 不误伤）。"""
        verifier = _FakeVerifier(
            [
                _verification(ReferenceVerdict.NOT_FOUND, key="k1", title="伪造文献"),
                _verification(ReferenceVerdict.VERIFIED, key="k2"),
            ]
        )
        workflow = LiteratureReviewWorkflow(validator=_validator(), reference_verifier=verifier)
        result = ExecutionResult(
            node_id="n1",
            content="文献综述正文，不含引用标记。",
            artifacts=[_artifact()],
            duration_ms=1,
        )

        report = await workflow.validate_node(_task(), result)

        assert isinstance(report, ValidationReport)
        check = next(item for item in report.checks if item.name == REFERENCE_CHECK)
        assert check.advisory is True
        assert check.passed is False
        assert "引用核验" in check.message
        assert "未找到 1 条" in check.message
        assert "已验证 1 条" in check.message
        # advisory 检查不参与通过判定
        assert report.passed is True
        assert [candidate.title for candidate in verifier.calls[0]] == ["Attention Is All You Need"]

    async def test_real_verifier_with_fake_source_reaches_report(self) -> None:
        """真核验器 + 假元数据源（零网络）：命中即「已验证」。"""
        source = _FakeMetadataSource(
            [
                MetadataRecord(
                    source="crossref",
                    title="Attention Is All You Need",
                    authors=["Zhang, Wei"],
                    year=2024,
                )
            ]
        )
        verifier = ReferenceVerifier(crossref=source, max_concurrency=2)
        workflow = LiteratureReviewWorkflow(validator=_validator(), reference_verifier=verifier)
        result = ExecutionResult(
            node_id="n1",
            content="文献综述正文，不含引用标记。",
            artifacts=[_artifact()],
            duration_ms=1,
        )

        report = await workflow.validate_node(_task(), result)

        check = next(item for item in report.checks if item.name == REFERENCE_CHECK)
        assert check.passed is True
        assert "已验证 1 条" in check.message
        assert source.searches == ["Attention Is All You Need"]

    async def test_empty_candidates_skip_verification(self) -> None:
        """artifacts 解析不出候选时不得外呼，且载荷形状保持不变。"""
        verifier = _FakeVerifier()
        validator = _RecordingValidator()
        workflow = LiteratureReviewWorkflow(validator=validator, reference_verifier=verifier)
        result = ExecutionResult(
            node_id="n1",
            content="无 artifacts",
            artifacts=[{"abstract": "缺标题，无法构造候选"}],
            duration_ms=1,
        )

        await workflow.validate_node(_task(), result)

        assert verifier.calls == []
        assert "reference_verifications" not in validator.calls[0]

    async def test_without_verifier_payload_unchanged(self) -> None:
        validator = _RecordingValidator()
        workflow = LiteratureReviewWorkflow(validator=validator, reference_verifier=None)
        result = ExecutionResult(
            node_id="n1", content="正文", artifacts=[_artifact()], duration_ms=1
        )

        await workflow.validate_node(_task(), result)

        assert "reference_verifications" not in validator.calls[0]

    async def test_verifier_failure_does_not_fail_validation(self) -> None:
        """核验异常必须被兜住：校验节点仍返回报告，只是没有核验检查。"""
        verifier = _FakeVerifier(error=RuntimeError("核验器炸了"))
        workflow = LiteratureReviewWorkflow(validator=_validator(), reference_verifier=verifier)
        result = ExecutionResult(
            node_id="n1",
            content="文献综述正文，不含引用标记。",
            artifacts=[_artifact()],
            duration_ms=1,
        )

        report = await workflow.validate_node(_task(), result)

        assert isinstance(report, ValidationReport)
        assert all(item.name != REFERENCE_CHECK for item in report.checks)


# ===== 4. 校验 Agent 透传与容错 =====


class TestValidatorReferencePassthrough:
    async def test_valid_entries_passed_through_in_enforce_mode(self) -> None:
        agent = _validator(mode="enforce")
        input_data = {
            "title": "校验",
            "result": "文献综述正文，不含引用标记。",
            "execution_result": _execution_payload(),
            "reference_verifications": [
                _verification(ReferenceVerdict.NOT_FOUND, key="k1", title="伪造文献").model_dump(
                    mode="json"
                ),
                _verification(ReferenceVerdict.VERIFIED, key="k2").model_dump(mode="json"),
                # 非法条目：schema 不符 → 跳过，不得整体失败
                {"reference_key": "bad", "title": "", "verdict": "not-a-verdict"},
                "完全不是字典",
            ],
            "trace_id": "t1",
        }

        report = await agent.execute(input_data)

        check = next(item for item in report["checks"] if item["name"] == REFERENCE_CHECK)
        assert check["advisory"] is False
        assert check["passed"] is False
        assert "未找到 1 条" in check["message"]
        assert "已验证 1 条" in check["message"]

    async def test_absent_verifications_keep_legacy_report(self) -> None:
        agent = _validator(mode="enforce")
        report = await agent.execute(
            {
                "title": "校验",
                "result": "文献综述正文，不含引用标记。",
                "execution_result": _execution_payload(),
                "trace_id": "t1",
            }
        )

        assert all(item["name"] != REFERENCE_CHECK for item in report["checks"])

    async def test_malformed_container_is_ignored(self) -> None:
        agent = _validator(mode="enforce")
        report = await agent.execute(
            {
                "title": "校验",
                "result": "文献综述正文，不含引用标记。",
                "execution_result": _execution_payload(),
                "reference_verifications": "不是列表",
                "trace_id": "t1",
            }
        )

        assert all(item["name"] != REFERENCE_CHECK for item in report["checks"])


# ===== 5. TaskExecution 接线（模式与核验器透传） =====


class TestTaskExecutionWiring:
    def _task_stub(self, config: dict[str, Any] | None = None) -> SimpleNamespace:
        from tests.test_task_execution import _plan_payload

        return SimpleNamespace(
            id=uuid.uuid4(),
            task_type="literature_review",
            title="检索增强生成",
            description="综述 RAG 的证据核验方法",
            plan=_plan_payload(),
            config=config if config is not None else {},
        )

    def _execution(self, *, llm: Any | None = None):
        from app.orchestration.runtime import RuntimeContainer
        from app.orchestration.services.task_execution import TaskExecution

        container = RuntimeContainer(llm=llm or _StubLlm(), knowledge_query=object())
        return container, TaskExecution(container)

    def test_workflow_uses_report_mode_and_verifier(self, apply_reference_config) -> None:
        apply_reference_config(enabled=True, mode="report")
        container, execution = self._execution()

        workflow = execution._literature_execution_workflow(self._task_stub())

        assert workflow is not None
        assert workflow._validator._citation_validator._reference_mode == "report"
        assert workflow._reference_verifier is container.reference_verifier

    def test_disabled_config_uses_off_mode_and_no_verifier(self, apply_reference_config) -> None:
        apply_reference_config(enabled=False, mode="enforce")
        container, execution = self._execution()

        workflow = execution._literature_execution_workflow(self._task_stub())

        assert workflow is not None
        assert workflow._validator._citation_validator._reference_mode == "off"
        assert workflow._reference_verifier is None
        assert container.reference_verifier is None

    # ===== R6：离线任务零外呼（校验路径 + 复盘路径，计划 §2.9） =====
    # 离线演示使用冻结语料（已人工核验），必须零外呼、可复现；
    # 需要展示核验时显式打开 `verify_offline_tasks`。

    def test_offline_task_skips_validation_verifier(self, apply_reference_config) -> None:
        apply_reference_config(enabled=True, verify_offline_tasks=False)
        container, execution = self._execution()

        workflow = execution._literature_execution_workflow(
            self._task_stub({"offline_dataset": True})
        )

        assert workflow is not None
        assert workflow._reference_verifier is None
        # 核验能力本身仍启用，只是离线任务不注入
        assert container.reference_verifier is not None

    def test_offline_task_verifies_when_switch_enabled(self, apply_reference_config) -> None:
        apply_reference_config(enabled=True, verify_offline_tasks=True)
        container, execution = self._execution()

        workflow = execution._literature_execution_workflow(
            self._task_stub({"offline_dataset": True})
        )

        assert workflow is not None
        assert workflow._reference_verifier is container.reference_verifier
        assert workflow._reference_verifier is not None

    def test_online_task_keeps_verifier(self, apply_reference_config) -> None:
        apply_reference_config(enabled=True, verify_offline_tasks=False)
        container, execution = self._execution()

        workflow = execution._literature_execution_workflow(self._task_stub({}))

        assert workflow is not None
        assert workflow._reference_verifier is container.reference_verifier

    def test_offline_reviewer_drops_verifier(self, apply_reference_config) -> None:
        """R6 复盘路径：离线任务的复盘 Agent 不得保留核验器（否则约 10 条外呼）。"""
        apply_reference_config(enabled=True, verify_offline_tasks=False)
        _, execution = self._execution()
        reviewer = SimpleNamespace(reference_verifier=object())

        execution._apply_reference_verification(
            reviewer, self._task_stub({"offline_dataset": True})
        )

        assert reviewer.reference_verifier is None

    def test_offline_reviewer_keeps_verifier_when_switch_enabled(
        self, apply_reference_config
    ) -> None:
        apply_reference_config(enabled=True, verify_offline_tasks=True)
        _, execution = self._execution()
        sentinel = object()
        reviewer = SimpleNamespace(reference_verifier=sentinel)

        execution._apply_reference_verification(
            reviewer, self._task_stub({"offline_dataset": True})
        )

        assert reviewer.reference_verifier is sentinel

    def test_online_reviewer_keeps_verifier(self, apply_reference_config) -> None:
        apply_reference_config(enabled=True, verify_offline_tasks=False)
        _, execution = self._execution()
        sentinel = object()
        reviewer = SimpleNamespace(reference_verifier=sentinel)

        execution._apply_reference_verification(reviewer, self._task_stub({}))

        assert reviewer.reference_verifier is sentinel

    def test_reviewer_without_verifier_attribute_is_tolerated(self, apply_reference_config) -> None:
        """注入式测试替身 / 老 Agent 没有该属性时不得抛异常。"""
        apply_reference_config(enabled=True, verify_offline_tasks=False)
        _, execution = self._execution()

        execution._apply_reference_verification(
            SimpleNamespace(), self._task_stub({"offline_dataset": True})
        )
