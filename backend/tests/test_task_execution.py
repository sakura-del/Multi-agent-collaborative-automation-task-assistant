"""TaskExecution 生命周期测试（Plan 2 Task 4）。

覆盖：
1. submit() 持久化 PLANNING 并返回 UUID；
2. 后台规划保存有效 TaskPlan 并进入 WAITING_APPROVAL；
3. 确认前不执行任何节点；
4. 确认后 RUNNING → COMPLETED；
5. 非法规划输出触发一次 schema 修复，仍失败进入 WAITING_HUMAN；
6. recover() 只重新调度执行中任务，并跳过已完成节点。
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.db.models  # noqa: F401  确保全部 ORM 注册到 Base.metadata
from app.core.exceptions import NotFoundException, ValidationException
from app.db.base import Base
from app.db.models.task import Subtask, Task
from app.db.repositories.task import TaskRepository
from app.orchestration.agents.planner import PlannerAgent
from app.orchestration.base_agent import BaseAgent
from app.orchestration.contracts import LiteratureReviewRequest, TaskStatus
from app.orchestration.runtime import RuntimeContainer
from app.orchestration.services.task_execution import TaskExecution

# ===== 测试替身 =====


def _plan_payload() -> dict[str, Any]:
    return {
        "version": 1,
        "topic": "检索增强生成证据核验",
        "research_questions": [
            "RAG 如何提升证据可靠性？",
            "如何度量证据可追溯性？",
            "多智能体协作如何影响综述质量？",
        ],
        "search_queries": ["RAG evidence verification"],
        "acceptance_criteria": ["引用可回溯"],
        "nodes": [
            {"id": "n1", "type": "search", "title": "检索文献", "dependencies": []},
            {"id": "n2", "type": "extract", "title": "提取证据", "dependencies": ["n1"]},
            {"id": "n3", "type": "write", "title": "撰写综述", "dependencies": ["n2"]},
            {"id": "n4", "type": "validate", "title": "校验引用", "dependencies": ["n3"]},
            {"id": "n5", "type": "review", "title": "汇总复盘", "dependencies": ["n4"]},
        ],
    }


class _DeterministicPlanner(BaseAgent):
    """确定性规划器：可阻塞（gate）、可连续失败（invalid）。"""

    def __init__(
        self,
        *,
        gate: asyncio.Event | None = None,
        invalid: bool = False,
        payload: dict[str, Any] | None = None,
    ) -> None:
        super().__init__()
        self.calls: list[dict[str, Any]] = []
        self._gate = gate
        self._invalid = invalid
        self._payload = payload or _plan_payload()

    @property
    def agent_type(self) -> str:
        return "planner"

    async def _run(self, input_data: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(dict(input_data))
        if self._gate is not None:
            await self._gate.wait()
        if self._invalid:
            raise ValueError("规划 schema 非法")
        from app.orchestration.contracts import TaskPlan

        plan = TaskPlan.model_validate(self._payload)
        return {
            "plan": plan.model_dump(mode="json"),
            "dag": PlannerAgent.build_dag(plan).to_dict(),
        }


class _RecordingExecutor(BaseAgent):
    """记录收到的节点上下文，返回确定性结果。"""

    def __init__(self, calls: list[dict[str, Any]]) -> None:
        super().__init__()
        self.calls = calls

    @property
    def agent_type(self) -> str:
        return "executor"

    async def _run(self, input_data: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(dict(input_data))
        return {"result": f"结果-{input_data['node_id']}"}


class _PassingValidator(BaseAgent):
    """永远通过的校验器。"""

    @property
    def agent_type(self) -> str:
        return "validator"

    async def _run(self, input_data: dict[str, Any]) -> dict[str, Any]:
        return {"passed": True, "score": 90.0, "checks": [], "retryable": False, "feedback": ""}


class _StaticReviewer(BaseAgent):
    """返回固定复盘报告。"""

    def __init__(self, calls: list[dict[str, Any]] | None = None) -> None:
        super().__init__()
        self.calls = calls if calls is not None else []

    @property
    def agent_type(self) -> str:
        return "reviewer"

    async def _run(self, input_data: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(dict(input_data))
        return {
            "trace_id": input_data.get("trace_id", ""),
            "summary": "任务执行成功，全部节点完成。",
            "stats": {"total_logs": 0, "error_count": 0},
            "root_cause": {},
        }


class _RecordingPublisher:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.events.append(event)


# ===== 夹具与工具 =====


async def _setup_engine(tmp_path: Path):
    """文件型 SQLite：每个 session 独立连接，避免 StaticPool 单连接事务串扰。"""
    db_path = tmp_path / "task-execution.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path.as_posix()}",
        connect_args={"timeout": 10},
    )
    async with engine.begin() as connection:
        await connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, factory


def _request() -> LiteratureReviewRequest:
    return LiteratureReviewRequest(
        topic="检索增强生成证据核验",
        keywords=["RAG", "证据核验"],
        max_documents=5,
    )


def _make_execution(
    factory,
    planner: BaseAgent,
    *,
    executor_calls: list[dict[str, Any]] | None = None,
    reviewer_calls: list[dict[str, Any]] | None = None,
    publisher: _RecordingPublisher | None = None,
) -> tuple[TaskExecution, RuntimeContainer]:
    executor = _RecordingExecutor(executor_calls if executor_calls is not None else [])
    validator = _PassingValidator()
    reviewer = _StaticReviewer(reviewer_calls)
    agents = {"executor": executor, "validator": validator, "reviewer": reviewer}

    def agent_factory(agent_type: str) -> Any:
        return agents[agent_type]

    container = RuntimeContainer(
        session_factory=factory,
        planner_factory=lambda: planner,
        agent_factory=agent_factory,
        event_publisher=publisher or _RecordingPublisher(),
    )
    return TaskExecution(container), container


async def _wait_for_status(
    execution: TaskExecution,
    user_id: uuid.UUID,
    task_id: uuid.UUID,
    expected: set[str],
    *,
    timeout: float = 5.0,
) -> Any:
    deadline = asyncio.get_running_loop().time() + timeout
    last_status = None
    while asyncio.get_running_loop().time() < deadline:
        snapshot = await execution.get_snapshot(user_id, task_id)
        last_status = snapshot.status
        if snapshot.status in expected:
            return snapshot
        await asyncio.sleep(0.01)
    raise AssertionError(f"等待状态 {expected} 超时，当前状态: {last_status}")


# ===== 测试 =====


@pytest.mark.asyncio
class TestSubmitAndPlan:
    async def test_submit_persists_planning_and_returns_uuid(self, tmp_path: Path) -> None:
        """submit() 返回 UUID，且提交后任务已持久化为 PLANNING。"""
        engine, factory = await _setup_engine(tmp_path)
        gate = asyncio.Event()
        planner = _DeterministicPlanner(gate=gate)
        execution, container = _make_execution(factory, planner)
        user_id = uuid.uuid4()
        try:
            task_id = await execution.submit(user_id, _request())
            assert isinstance(task_id, uuid.UUID)

            snapshot = await execution.get_snapshot(user_id, task_id)
            assert snapshot.status == TaskStatus.PLANNING.value
            assert snapshot.plan_version == 0

            gate.set()
            snapshot = await _wait_for_status(
                execution, user_id, task_id, {TaskStatus.WAITING_APPROVAL.value}
            )
            assert snapshot.plan_version == 1
            assert snapshot.plan is not None
            assert len(snapshot.subtasks) == 5
            assert snapshot.revision == 1
        finally:
            gate.set()
            await container.close()
            await engine.dispose()

    async def test_invalid_planner_repairs_once_then_waiting_human(self, tmp_path: Path) -> None:
        """非法规划输出触发且仅触发一次修复调用，仍失败则进入 WAITING_HUMAN。"""
        engine, factory = await _setup_engine(tmp_path)
        planner = _DeterministicPlanner(invalid=True)
        execution, container = _make_execution(factory, planner)
        user_id = uuid.uuid4()
        try:
            task_id = await execution.submit(user_id, _request())
            await _wait_for_status(execution, user_id, task_id, {TaskStatus.WAITING_HUMAN.value})
            assert len(planner.calls) == 2
            assert planner.calls[0].get("repair") is None
            assert planner.calls[1]["repair"]["error"]
        finally:
            await container.close()
            await engine.dispose()

    async def test_replan_recovers_from_planning_failure(self, tmp_path: Path) -> None:
        """规划失败后 `replan` 能真正重新规划。

        回归（2026-11-17 生产实测）：规划失败时没有任何子任务，前端原先发
        `retry_node n1` 只能 404，用户唯一能做的动作是坏的。
        """
        engine, factory = await _setup_engine(tmp_path)
        planner = _DeterministicPlanner(invalid=True)
        execution, container = _make_execution(factory, planner)
        user_id = uuid.uuid4()
        try:
            task_id = await execution.submit(user_id, _request())
            failed = await _wait_for_status(
                execution, user_id, task_id, {TaskStatus.WAITING_HUMAN.value}
            )
            assert failed.plan is None
            assert failed.subtasks == []

            from app.orchestration.contracts import TaskCommand, TaskControlRequest

            # 用户处理好上游模型后再点「重新规划」：这里让规划器恢复成功
            planner._invalid = False
            snapshot = await execution.control(
                user_id, task_id, TaskControlRequest(command=TaskCommand.REPLAN)
            )
            assert snapshot.status == TaskStatus.PLANNING.value
            assert snapshot.plan is None
            assert snapshot.subtasks == []

            ready = await _wait_for_status(
                execution, user_id, task_id, {TaskStatus.WAITING_APPROVAL.value}
            )
            assert ready.plan is not None
            assert ready.plan_version == 1
            assert len(ready.subtasks) == 5
        finally:
            await container.close()
            await engine.dispose()

    async def test_replan_from_waiting_approval_bumps_version(self, tmp_path: Path) -> None:
        """已有规划时重规划：旧 DAG 作废，版本号单调递增。"""
        engine, factory = await _setup_engine(tmp_path)
        planner = _DeterministicPlanner()
        execution, container = _make_execution(factory, planner)
        user_id = uuid.uuid4()
        try:
            task_id = await execution.submit(user_id, _request())
            waiting = await _wait_for_status(
                execution, user_id, task_id, {TaskStatus.WAITING_APPROVAL.value}
            )
            assert waiting.plan_version == 1

            from app.orchestration.contracts import TaskCommand, TaskControlRequest

            snapshot = await execution.control(
                user_id, task_id, TaskControlRequest(command=TaskCommand.REPLAN)
            )
            assert snapshot.status == TaskStatus.PLANNING.value
            # 旧规划与旧子任务不能留到新规划生成之前
            assert snapshot.plan is None
            assert snapshot.subtasks == []

            ready = await _wait_for_status(
                execution, user_id, task_id, {TaskStatus.WAITING_APPROVAL.value}
            )
            assert ready.plan_version == 2
            assert ready.plan is not None
            # TaskSnapshot.plan 是 dict（JSON 形态），版本号在 plan["version"]
            assert ready.plan["version"] == 2
            assert len(ready.subtasks) == 5

            # 新版本号才能确认：旧 revision 已失效
            with pytest.raises(ValidationException):
                await execution.approve_plan(user_id, task_id, 1)
        finally:
            await container.close()
            await engine.dispose()


@pytest.mark.asyncio
class TestApprovalAndExecution:
    async def test_nothing_executes_before_approval(self, tmp_path: Path) -> None:
        """WAITING_APPROVAL 状态下没有节点被执行。"""
        engine, factory = await _setup_engine(tmp_path)
        executor_calls: list[dict[str, Any]] = []
        planner = _DeterministicPlanner()
        execution, container = _make_execution(factory, planner, executor_calls=executor_calls)
        user_id = uuid.uuid4()
        try:
            task_id = await execution.submit(user_id, _request())
            await _wait_for_status(execution, user_id, task_id, {TaskStatus.WAITING_APPROVAL.value})
            await asyncio.sleep(0.05)
            assert executor_calls == []
        finally:
            await container.close()
            await engine.dispose()

    async def test_approval_runs_to_completion(self, tmp_path: Path) -> None:
        """确认后任务 RUNNING → COMPLETED，子任务全部完成并生成复盘报告。"""
        engine, factory = await _setup_engine(tmp_path)
        executor_calls: list[dict[str, Any]] = []
        reviewer_calls: list[dict[str, Any]] = []
        publisher = _RecordingPublisher()
        planner = _DeterministicPlanner()
        execution, container = _make_execution(
            factory,
            planner,
            executor_calls=executor_calls,
            reviewer_calls=reviewer_calls,
            publisher=publisher,
        )
        user_id = uuid.uuid4()
        try:
            task_id = await execution.submit(user_id, _request())
            waiting = await _wait_for_status(
                execution, user_id, task_id, {TaskStatus.WAITING_APPROVAL.value}
            )

            snapshot = await execution.approve_plan(user_id, task_id, waiting.revision)
            assert snapshot.status == TaskStatus.RUNNING.value

            completed = await _wait_for_status(
                execution, user_id, task_id, {TaskStatus.COMPLETED.value}
            )
            assert completed.review_report is not None
            assert completed.review_report["summary"]
            assert all(subtask.status == "completed" for subtask in completed.subtasks)
            assert sorted(call["node_id"] for call in executor_calls) == [
                "n1",
                "n2",
                "n3",
                "n4",
                "n5",
            ]
            assert len(reviewer_calls) == 1
            assert publisher.events
        finally:
            await container.close()
            await engine.dispose()

    async def test_approval_rejects_wrong_revision(self, tmp_path: Path) -> None:
        """规划版本不匹配时拒绝确认。"""
        engine, factory = await _setup_engine(tmp_path)
        execution, container = _make_execution(factory, _DeterministicPlanner())
        user_id = uuid.uuid4()
        try:
            task_id = await execution.submit(user_id, _request())
            await _wait_for_status(execution, user_id, task_id, {TaskStatus.WAITING_APPROVAL.value})
            with pytest.raises(ValidationException):
                await execution.approve_plan(user_id, task_id, 999)
        finally:
            await container.close()
            await engine.dispose()

    async def test_cancel_task(self, tmp_path: Path) -> None:
        """等待确认状态下可取消任务，取消后为终态。"""
        engine, factory = await _setup_engine(tmp_path)
        execution, container = _make_execution(factory, _DeterministicPlanner())
        user_id = uuid.uuid4()
        try:
            task_id = await execution.submit(user_id, _request())
            await _wait_for_status(execution, user_id, task_id, {TaskStatus.WAITING_APPROVAL.value})
            from app.orchestration.contracts import TaskCommand, TaskControlRequest

            snapshot = await execution.control(
                user_id, task_id, TaskControlRequest(command=TaskCommand.CANCEL)
            )
            assert snapshot.status == TaskStatus.CANCELLED.value
        finally:
            await container.close()
            await engine.dispose()

    async def test_events_after_is_owner_scoped(self, tmp_path: Path) -> None:
        """事件按递增序号回放，跨用户查询任务返回 404。"""
        engine, factory = await _setup_engine(tmp_path)
        execution, container = _make_execution(factory, _DeterministicPlanner())
        user_id, other_user = uuid.uuid4(), uuid.uuid4()
        try:
            task_id = await execution.submit(user_id, _request())
            waiting = await _wait_for_status(
                execution, user_id, task_id, {TaskStatus.WAITING_APPROVAL.value}
            )
            await execution.approve_plan(user_id, task_id, waiting.revision)
            await _wait_for_status(execution, user_id, task_id, {TaskStatus.COMPLETED.value})

            events = await execution.events_after(user_id, task_id, 0)
            event_ids = [event.event_id for event in events]
            assert event_ids == sorted(event_ids)
            assert event_ids[0] == 1
            assert "plan_ready" in {event.event_type for event in events}
            assert "task_completed" in {event.event_type for event in events}

            with pytest.raises(NotFoundException):
                await execution.events_after(other_user, task_id, 0)
            with pytest.raises(NotFoundException):
                await execution.get_snapshot(other_user, task_id)
        finally:
            await container.close()
            await engine.dispose()


@pytest.mark.asyncio
class TestRecovery:
    async def _seed_task(
        self,
        factory,
        *,
        user_id: uuid.UUID,
        status: TaskStatus,
        completed_nodes: set[str],
        title: str,
    ) -> uuid.UUID:
        plan_payload = _plan_payload()
        from app.orchestration.contracts import TaskPlan

        plan = TaskPlan.model_validate(plan_payload)
        dag = PlannerAgent.build_dag(plan)
        async with factory() as session:
            task = Task(
                user_id=user_id,
                title=title,
                description="恢复测试",
                task_type="literature_review",
                status=status.value,
                config={"trace_id": f"trace-{title}"},
            )
            session.add(task)
            await session.flush()
            await TaskRepository(session).replace_plan(task, plan, dag)
            rows = (
                (await session.execute(select(Subtask).where(Subtask.task_id == task.id)))
                .scalars()
                .all()
            )
            for row in rows:
                row.status = "completed" if row.node_id in completed_nodes else "pending"
            await session.commit()
            return task.id

    async def test_recover_only_schedules_active_tasks_and_skips_completed_nodes(
        self, tmp_path: Path
    ) -> None:
        """recover() 只恢复 RUNNING 任务，已完成任务不动，已完成节点不重跑。"""
        engine, factory = await _setup_engine(tmp_path)
        user_id = uuid.uuid4()
        executor_calls: list[dict[str, Any]] = []
        try:
            active_task_id = await self._seed_task(
                factory,
                user_id=user_id,
                status=TaskStatus.RUNNING,
                completed_nodes={"n1"},
                title="恢复中的任务",
            )
            finished_task_id = await self._seed_task(
                factory,
                user_id=user_id,
                status=TaskStatus.COMPLETED,
                completed_nodes={"n1", "n2", "n3", "n4", "n5"},
                title="已完成任务",
            )

            execution, container = _make_execution(
                factory, _DeterministicPlanner(), executor_calls=executor_calls
            )
            try:
                await execution.recover()
                snapshot = await _wait_for_status(
                    execution, user_id, active_task_id, {TaskStatus.COMPLETED.value}
                )
                assert snapshot.status == TaskStatus.COMPLETED.value

                # n1 已完成，只执行 n2/n3/n4/n5
                assert sorted(call["node_id"] for call in executor_calls) == [
                    "n2",
                    "n3",
                    "n4",
                    "n5",
                ]

                finished = await execution.get_snapshot(user_id, finished_task_id)
                assert finished.status == TaskStatus.COMPLETED.value
            finally:
                await container.close()
        finally:
            await engine.dispose()


class TestTransientRetry:
    """规划阶段瞬态故障重试（Plan 5 Task 8 实跑遇到上游 503 直接转人工）。"""

    async def test_retries_transient_5xx_then_succeeds(self) -> None:
        from app.orchestration.services.task_execution import TaskExecution

        calls = {"n": 0}

        class _Boom(Exception):
            status_code = 503

        async def op():
            calls["n"] += 1
            if calls["n"] < 3:
                raise _Boom("Service temporarily unavailable")
            return "ok"

        execution = TaskExecution.__new__(TaskExecution)
        result = await execution._retry_transient(
            op, task_id=uuid.UUID(int=0), stage="test", attempts=4, base_delay=0.0
        )

        assert result == "ok"
        assert calls["n"] == 3

    async def test_does_not_retry_configuration_errors(self) -> None:
        from app.orchestration.services.task_execution import TaskExecution

        calls = {"n": 0}

        class _AuthError(Exception):
            status_code = 401

        async def op():
            calls["n"] += 1
            raise _AuthError("Incorrect API key provided")

        execution = TaskExecution.__new__(TaskExecution)
        with pytest.raises(_AuthError):
            await execution._retry_transient(
                op, task_id=uuid.UUID(int=0), stage="test", attempts=4, base_delay=0.0
            )

        assert calls["n"] == 1, "401 不应重试"

    async def test_gives_up_after_attempts(self) -> None:
        from app.orchestration.services.task_execution import TaskExecution

        calls = {"n": 0}

        class _Boom(Exception):
            status_code = 503

        async def op():
            calls["n"] += 1
            raise _Boom("503")

        execution = TaskExecution.__new__(TaskExecution)
        with pytest.raises(_Boom):
            await execution._retry_transient(
                op, task_id=uuid.UUID(int=0), stage="test", attempts=3, base_delay=0.0
            )

        assert calls["n"] == 3

    async def test_human_action_error_is_not_retried(self) -> None:
        from app.core.exceptions import HumanActionRequiredError
        from app.orchestration.services.task_execution import TaskExecution

        calls = {"n": 0}

        async def op():
            calls["n"] += 1
            raise HumanActionRequiredError("规划契约非法")

        execution = TaskExecution.__new__(TaskExecution)
        with pytest.raises(HumanActionRequiredError):
            await execution._retry_transient(
                op, task_id=uuid.UUID(int=0), stage="test", attempts=4, base_delay=0.0
            )

        assert calls["n"] == 1


@pytest.mark.asyncio
class TestPlanningAndWaitingReasonPersistence:
    """M0-3/M0-4：规划/节点转人工原因持久化与阶段进度事件。"""

    async def test_planning_failure_persists_waiting_reason_in_snapshot_without_subtasks(
        self, tmp_path: Path
    ) -> None:
        engine, factory = await _setup_engine(tmp_path)
        planner = _DeterministicPlanner(invalid=True)
        execution, container = _make_execution(factory, planner)
        user_id = uuid.uuid4()
        try:
            task_id = await execution.submit(user_id, _request())
            await _wait_for_status(execution, user_id, task_id, {TaskStatus.WAITING_HUMAN.value})

            # 模拟进程重启：重新构造 TaskExecution 实例读库
            restarted = TaskExecution(container)
            snap = await restarted.get_snapshot(user_id, task_id)
            assert snap.status == TaskStatus.WAITING_HUMAN.value
            assert snap.subtasks == []
            assert snap.waiting_reason is not None and "规划失败" in snap.waiting_reason
            assert snap.error is None
            assert snap.workflow_preset == "literature_review"
        finally:
            await container.close()
            await engine.dispose()

    async def test_replan_clears_previous_waiting_reason_and_error(self, tmp_path: Path) -> None:
        from app.orchestration.contracts import TaskCommand, TaskControlRequest

        engine, factory = await _setup_engine(tmp_path)
        fail_planner = _DeterministicPlanner(invalid=True)
        execution, container = _make_execution(factory, fail_planner)
        user_id = uuid.uuid4()
        try:
            task_id = await execution.submit(user_id, _request())
            snap_waiting = await _wait_for_status(
                execution, user_id, task_id, {TaskStatus.WAITING_HUMAN.value}
            )
            assert snap_waiting.waiting_reason is not None

            # replan 切回 PLANNING 时立即清空旧原因，进入 WAITING_APPROVAL 后仍为 None
            gate = asyncio.Event()
            container._planner_factory = lambda: _DeterministicPlanner(gate=gate)
            replan_snap = await execution.control(
                user_id, task_id, TaskControlRequest(command=TaskCommand.REPLAN)
            )
            assert replan_snap.status == TaskStatus.PLANNING.value
            assert replan_snap.waiting_reason is None
            assert replan_snap.error is None

            gate.set()
            approved_snap = await _wait_for_status(
                execution, user_id, task_id, {TaskStatus.WAITING_APPROVAL.value}
            )
            assert approved_snap.waiting_reason is None
            assert approved_snap.error is None
        finally:
            await container.close()
            await engine.dispose()

    async def test_node_exhaustion_populates_snapshot_waiting_reason_and_event_reason(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.db.models.log import ExecutionLog
        from app.orchestration.retry import RetryPolicy
        from app.orchestration.scheduler import TaskScheduler

        class _AlwaysFailExecutor(BaseAgent):
            @property
            def agent_type(self) -> str:
                return "executor"

            async def _run(self, input_data: dict[str, Any]) -> dict[str, Any]:
                raise RuntimeError("上游节点执行超时")

        orig_init = TaskScheduler.__init__

        def _fast_retry_init(self: Any, *args: Any, **kwargs: Any) -> None:
            if kwargs.get("retry_policy") is None:
                kwargs["retry_policy"] = RetryPolicy(max_retries=1, backoff_times=[0.0])
            orig_init(self, *args, **kwargs)

        monkeypatch.setattr(TaskScheduler, "__init__", _fast_retry_init)

        engine, factory = await _setup_engine(tmp_path)
        planner = _DeterministicPlanner()
        publisher = _RecordingPublisher()
        container = RuntimeContainer(
            session_factory=factory,
            planner_factory=lambda: planner,
            agent_factory=lambda agent_type: (
                _AlwaysFailExecutor()
                if agent_type == "executor"
                else (_PassingValidator() if agent_type == "validator" else _StaticReviewer())
            ),
            event_publisher=publisher,
        )
        execution = TaskExecution(container)
        user_id = uuid.uuid4()
        try:
            task_id = await execution.submit(user_id, _request())
            await _wait_for_status(execution, user_id, task_id, {TaskStatus.WAITING_APPROVAL.value})
            await execution.approve_plan(user_id, task_id, 1)
            snap = await _wait_for_status(
                execution, user_id, task_id, {TaskStatus.WAITING_HUMAN.value}
            )
            assert snap.waiting_reason is not None and "上游节点执行超时" in snap.waiting_reason
            assert snap.error is None

            async with factory() as session:
                logs = (
                    (
                        await session.execute(
                            select(ExecutionLog).where(
                                ExecutionLog.task_id == task_id,
                                ExecutionLog.event_type == "task_waiting_human",
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                assert logs
                assert "上游节点执行超时" in str((logs[-1].context or {}).get("reason") or "")
        finally:
            await container.close()
            await engine.dispose()

    async def test_planning_emits_stage_progress_events(self, tmp_path: Path) -> None:
        engine, factory = await _setup_engine(tmp_path)
        planner = _DeterministicPlanner()
        publisher = _RecordingPublisher()
        execution, container = _make_execution(factory, planner, publisher=publisher)
        user_id = uuid.uuid4()
        try:
            task_id = await execution.submit(user_id, _request())
            await _wait_for_status(execution, user_id, task_id, {TaskStatus.WAITING_APPROVAL.value})

            stage_events = [e for e in publisher.events if e.event_type == "stage_progress"]
            assert stage_events, "规划过程应发出至少一条 stage_progress 事件"
            assert any(
                e.status == TaskStatus.PLANNING.value
                and e.subtask_id is None
                and e.payload.get("stage") == "planning_llm"
                and e.payload.get("message")
                for e in stage_events
            )
        finally:
            await container.close()
            await engine.dispose()

    async def test_retry_transient_emits_stage_progress_with_delay(self, tmp_path: Path) -> None:
        engine, factory = await _setup_engine(tmp_path)
        planner = _DeterministicPlanner()
        publisher = _RecordingPublisher()
        execution, container = _make_execution(factory, planner, publisher=publisher)
        user_id = uuid.uuid4()
        try:
            async with factory() as session:
                task = Task(
                    user_id=user_id,
                    title="瞬态重试事件测试",
                    description="d",
                    status=TaskStatus.PLANNING.value,
                    config={},
                )
                session.add(task)
                await session.commit()
                await session.refresh(task)
                task_id = task.id

            calls = {"n": 0}

            class _Boom(Exception):
                status_code = 503

            async def op() -> str:
                calls["n"] += 1
                if calls["n"] == 1:
                    raise _Boom("Service temporarily unavailable")
                return "ok"

            result = await execution._retry_transient(
                op, task_id=task_id, stage="planning", attempts=3, base_delay=0.01
            )
            assert result == "ok"

            retry_events = [
                e
                for e in publisher.events
                if e.event_type == "stage_progress" and e.payload.get("stage") == "planning_retry"
            ]
            assert len(retry_events) == 1
            evt = retry_events[0]
            assert evt.status == TaskStatus.PLANNING.value
            assert evt.subtask_id is None
            assert evt.payload["retry_delay_s"] == 0.01
            assert evt.payload["attempt"] == 1
        finally:
            await container.close()
            await engine.dispose()

    async def test_planning_emits_plan_progress_sequence(self, tmp_path: Path) -> None:
        """Issue #69：正常规划路径依次推送 binding_model → generating_plan → validating_contract → persisting_plan → plan_ready。"""
        engine, factory = await _setup_engine(tmp_path)
        planner = _DeterministicPlanner()
        publisher = _RecordingPublisher()
        execution, container = _make_execution(factory, planner, publisher=publisher)
        user_id = uuid.uuid4()
        try:
            task_id = await execution.submit(user_id, _request())
            await _wait_for_status(execution, user_id, task_id, {TaskStatus.WAITING_APPROVAL.value})

            progress_stages = [
                e.payload.get("stage") for e in publisher.events if e.event_type == "plan_progress"
            ]
            assert progress_stages == [
                "binding_model",
                "generating_plan",
                "validating_contract",
                "persisting_plan",
            ]
            ready_indices = [
                idx for idx, e in enumerate(publisher.events) if e.event_type == "plan_ready"
            ]
            last_progress_idx = max(
                idx for idx, e in enumerate(publisher.events) if e.event_type == "plan_progress"
            )
            assert ready_indices and ready_indices[-1] > last_progress_idx
        finally:
            await container.close()
            await engine.dispose()

    async def test_planning_repair_emits_repairing_plan_progress(self, tmp_path: Path) -> None:
        """Issue #69：首次规划契约校验失败触发修复时，推送 repairing_plan 事件。"""

        class _RepairablePlanner(BaseAgent):
            def __init__(self) -> None:
                super().__init__()
                self.calls = 0

            @property
            def agent_type(self) -> str:
                return "planner"

            async def _run(self, input_data: dict[str, Any]) -> dict[str, Any]:
                self.calls += 1
                if self.calls == 1:
                    # 首次缺文献契约核心节点（仅一个空 write），触发 validate_plan_contract 失败并进入修复
                    return {
                        "plan": {
                            "version": 1,
                            "topic": "非法空计划",
                            "research_questions": ["q1"],
                            "search_queries": ["k1"],
                            "acceptance_criteria": ["c1"],
                            "nodes": [
                                {
                                    "id": "n1",
                                    "type": "write",
                                    "title": "写",
                                    "dependencies": [],
                                    "input": {},
                                    "required_evidence": 0,
                                }
                            ],
                        },
                        "dag": {"nodes": [], "edges": []},
                    }
                from app.orchestration.contracts import TaskPlan

                plan = TaskPlan.model_validate(_plan_payload())
                return {
                    "plan": plan.model_dump(mode="json"),
                    "dag": PlannerAgent.build_dag(plan).to_dict(),
                }

        engine, factory = await _setup_engine(tmp_path)
        planner = _RepairablePlanner()
        publisher = _RecordingPublisher()
        execution, container = _make_execution(factory, planner, publisher=publisher)
        user_id = uuid.uuid4()
        try:
            task_id = await execution.submit(user_id, _request())
            await _wait_for_status(execution, user_id, task_id, {TaskStatus.WAITING_APPROVAL.value})

            repair_events = [
                e
                for e in publisher.events
                if e.event_type == "plan_progress" and e.payload.get("stage") == "repairing_plan"
            ]
            assert len(repair_events) == 1
            assert "自动修复" in str(repair_events[0].payload.get("message"))
        finally:
            await container.close()
            await engine.dispose()

    async def test_retry_transient_planning_emits_retrying_upstream_plan_progress(
        self, tmp_path: Path
    ) -> None:
        """Issue #69：planning 阶段上游瞬态重试时同步发出 plan_progress(retrying_upstream)。"""
        engine, factory = await _setup_engine(tmp_path)
        planner = _DeterministicPlanner()
        publisher = _RecordingPublisher()
        execution, container = _make_execution(factory, planner, publisher=publisher)
        user_id = uuid.uuid4()
        try:
            async with factory() as session:
                task = Task(
                    user_id=user_id,
                    title="规划瞬态重试事件测试",
                    description="d",
                    status=TaskStatus.PLANNING.value,
                    config={},
                )
                session.add(task)
                await session.commit()
                await session.refresh(task)
                task_id = task.id

            calls = {"n": 0}

            class _Boom(Exception):
                status_code = 503

            async def op() -> str:
                calls["n"] += 1
                if calls["n"] == 1:
                    raise _Boom("Service temporarily unavailable")
                return "ok"

            await execution._retry_transient(
                op, task_id=task_id, stage="planning", attempts=3, base_delay=0.01
            )

            retry_events = [
                e
                for e in publisher.events
                if e.event_type == "plan_progress" and e.payload.get("stage") == "retrying_upstream"
            ]
            assert len(retry_events) == 1
            evt = retry_events[0]
            assert evt.payload["attempt"] == 1
            assert evt.payload["max_attempts"] == 2
        finally:
            await container.close()
            await engine.dispose()
