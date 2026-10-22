"""TaskExecutionExecutor 审批与画像开关测试（2026-10-22 实验矩阵）。

## 本文件锁定两个真实缺陷

### 缺陷 1：审批逻辑导致任务永远卡在 waiting_approval（**实测踩到**）

首版：

```python
snapshot = await execution.get_snapshot(...)      # submit 后是 planning
while snapshot.status == WAITING_APPROVAL:        # 条件不成立
    await execution.approve_plan(...)
    break
while ...:                                        # 只等终态，从不审批
```

后果：服务器实测任务创建后卡 8 分钟不动，`pilot-8` 第一个 run 就挂住。

修法：审批并入主轮询循环，见到 `waiting_approval` 就批、**只批一次**。

### 缺陷 2：画像开关没传给请求（消融失效）

`LiteratureReviewRequest` 不传 `retrieval_mode` 等字段时用默认值（全开），
四组实验会走同一条路径 —— 论文的消融章节不成立。
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from evaluation.models import EvaluationTopic, ExperimentConfig
from evaluation.runner import RunSpec, TaskExecutionExecutor


def _profile(**overrides: Any) -> ExperimentConfig:
    base: dict[str, Any] = {
        "name": "single_agent_no_rag",
        "agent_mode": "single",
        "retrieval_mode": "none",
        "validation_enabled": False,
        "review_enabled": False,
    }
    base.update(overrides)
    return ExperimentConfig(**base)


def _topic() -> EvaluationTopic:
    return EvaluationTopic(
        topic_id="ai-rag-eval",
        domain="ai",
        topic_zh="检索增强生成系统的质量评估方法",
        query_en="retrieval augmented generation quality evaluation metrics",
        corpus_manifest="evaluation/datasets/corpora/ai.json",
    )


def _spec(profile: ExperimentConfig | None = None) -> RunSpec:
    return RunSpec(
        run_id="r1",
        profile=profile or _profile(),
        topic=_topic(),
        repetition=1,
    )


class _Snapshot:
    def __init__(self, status: str, revision: int = 1) -> None:
        self.status = status
        self.revision = revision
        self.review_report: dict[str, Any] = {}
        self.subtasks: list[Any] = []


class _ScriptedExecution:
    """按脚本依次返回快照状态；记录审批调用。"""

    def __init__(self, statuses: list[str]) -> None:
        self._statuses = statuses
        self._index = 0
        self.submitted: list[Any] = []
        self.approvals: list[int] = []
        self._task_id = uuid.uuid4()

    async def submit(self, user_id: Any, request: Any) -> uuid.UUID:
        self.submitted.append(request)
        return self._task_id

    async def get_snapshot(self, user_id: Any, task_id: Any) -> _Snapshot:
        status = self._statuses[min(self._index, len(self._statuses) - 1)]
        self._index += 1
        return _Snapshot(status)

    async def approve_plan(self, user_id: Any, task_id: Any, revision: int) -> None:
        self.approvals.append(revision)

    async def events_after(self, user_id: Any, task_id: Any, event_id: int) -> list[Any]:
        return []


class TestApprovalLoop:
    @pytest.mark.asyncio
    async def test_approves_after_planning(self) -> None:
        """**核心回归**：submit 后先是 planning，进入 waiting_approval 时必须审批。"""
        execution = _ScriptedExecution(["planning", "waiting_approval", "completed"])
        executor = TaskExecutionExecutor(
            execution, user_id=uuid.uuid4(), poll_interval=0.0, timeout=5.0
        )

        outcome = await executor.execute(_spec())

        assert execution.approvals == [1], "进入 waiting_approval 后必须审批一次，否则任务永远卡住"
        assert outcome.status == "completed"

    @pytest.mark.asyncio
    async def test_approves_only_once(self) -> None:
        """只批一次：重复审批会被服务端拒绝（revision 已过期）。"""
        execution = _ScriptedExecution(
            ["planning", "waiting_approval", "waiting_approval", "completed"]
        )
        executor = TaskExecutionExecutor(
            execution, user_id=uuid.uuid4(), poll_interval=0.0, timeout=5.0
        )

        await executor.execute(_spec())

        assert len(execution.approvals) == 1

    @pytest.mark.asyncio
    async def test_no_approval_when_already_terminal(self) -> None:
        execution = _ScriptedExecution(["completed"])
        executor = TaskExecutionExecutor(
            execution, user_id=uuid.uuid4(), poll_interval=0.0, timeout=5.0
        )

        await executor.execute(_spec())

        assert execution.approvals == []

    @pytest.mark.asyncio
    async def test_timeout_returns_non_completed_status(self) -> None:
        """超时不能抛异常（实验要记录失败而不是崩掉整批）。"""
        execution = _ScriptedExecution(["running"])
        executor = TaskExecutionExecutor(
            execution, user_id=uuid.uuid4(), poll_interval=0.0, timeout=0.0
        )

        outcome = await executor.execute(_spec())

        assert outcome.status == "running"
        assert outcome.error


class TestProfileSwitchesPassedToRequest:
    @pytest.mark.asyncio
    async def test_retrieval_mode_passed(self) -> None:
        execution = _ScriptedExecution(["completed"])
        executor = TaskExecutionExecutor(
            execution, user_id=uuid.uuid4(), poll_interval=0.0, timeout=5.0
        )

        await executor.execute(_spec(_profile(retrieval_mode="none")))

        assert execution.submitted[0].retrieval_mode == "none"

    @pytest.mark.asyncio
    async def test_validation_flag_passed(self) -> None:
        execution = _ScriptedExecution(["completed"])
        executor = TaskExecutionExecutor(
            execution, user_id=uuid.uuid4(), poll_interval=0.0, timeout=5.0
        )

        await executor.execute(_spec(_profile(validation_enabled=False)))

        assert execution.submitted[0].validation_enabled is False

    @pytest.mark.asyncio
    async def test_review_flag_passed(self) -> None:
        execution = _ScriptedExecution(["completed"])
        executor = TaskExecutionExecutor(
            execution, user_id=uuid.uuid4(), poll_interval=0.0, timeout=5.0
        )

        await executor.execute(_spec(_profile(review_enabled=False)))

        assert execution.submitted[0].review_enabled is False

    @pytest.mark.asyncio
    async def test_full_profile_passes_all_enabled(self) -> None:
        """`full` 画像（全开）必须真的传 True/hybrid_rerank。"""
        execution = _ScriptedExecution(["completed"])
        executor = TaskExecutionExecutor(
            execution, user_id=uuid.uuid4(), poll_interval=0.0, timeout=5.0
        )

        await executor.execute(
            _spec(
                _profile(
                    name="multi_agent_hybrid_rerank",
                    agent_mode="multi",
                    retrieval_mode="hybrid_rerank",
                    validation_enabled=True,
                    review_enabled=True,
                )
            )
        )

        request = execution.submitted[0]
        assert request.retrieval_mode == "hybrid_rerank"
        assert request.validation_enabled is True
        assert request.review_enabled is True
