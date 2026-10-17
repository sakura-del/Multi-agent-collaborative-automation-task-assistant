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
