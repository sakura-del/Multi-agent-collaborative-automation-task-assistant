"""可重启端到端任务执行集成测试（Plan 2 Task 7）。

覆盖：
1. HTTP 端到端：`POST /tasks` → WAITING_APPROVAL → approve → COMPLETED，
   规划由确定性 BaseLLM 适配器驱动，测试不手工构造 DAG；
2. 崩溃恢复：n1 提交后暂停观察者 → 销毁 runtime → 同库重建 → `recover()`，
   断言 n1 只执行一次、n2/n3 完成；
3. `RecoveryManager.load_state()` 把遗留 RUNNING/VALIDATING/RETRYING 节点重置为
   READY 并追加 task_recovery 事件。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.db.models  # noqa: F401  确保全部 ORM 注册到 Base.metadata
from app.db.base import Base
from app.db.models.task import Subtask, Task
from app.db.repositories.task import TaskRepository
from app.db.session import get_db
from app.orchestration.agents.planner import PlannerAgent
from app.orchestration.contracts import TaskPlan
from app.orchestration.events import InMemoryEventPublisher
from app.orchestration.recovery import RecoveryManager
from app.orchestration.runtime import RuntimeContainer
from app.orchestration.scheduler import NodeResult
from app.orchestration.services.task_execution import TaskExecution
from app.orchestration.task_state import SubtaskStatus
from tests.llm.deterministic_llm import PLAN_PAYLOAD, DeterministicPlanLLM
from tests.test_task_api import (
    _auth,
    _create,
    _PassingValidator,
    _RecordingExecutor,
    _register_and_login,
    _StaticReviewer,
    _wait_status,
)


class _PausingObserver:
    """包装数据库观察者：n1 结果提交后阻塞调度（模拟进程崩溃前的最后一刻）。"""

    def __init__(self, inner: Any, gate: asyncio.Event, node_id: str = "n1") -> None:
        self._inner = inner
        self._gate = gate
        self._node_id = node_id

    async def on_transition(
        self,
        task_id: str,
        node_id: str,
        old: SubtaskStatus,
        new: SubtaskStatus,
    ) -> None:
        await self._inner.on_transition(task_id, node_id, old, new)

    async def on_result(self, task_id: str, result: NodeResult) -> None:
        await self._inner.on_result(task_id, result)
        if result.node_id == self._node_id:
            await self._gate.wait()


class _PausingContainer(RuntimeContainer):
    """在观察者层暂停调度的运行时容器。"""

    def __init__(self, *args: Any, gate: asyncio.Event, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._gate = gate

    def create_observer(self):  # type: ignore[override]
        return _PausingObserver(super().create_observer(), self._gate)


# ===== 夹具 =====


@pytest.fixture
def recovery_env(tmp_path: Path) -> Iterator[Any]:
    """文件型 SQLite + TestClient portal 事件循环 + 运行时安装助手。"""
    from app.main import app

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'recovery.db'}",
        connect_args={"timeout": 10},
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    client.__enter__()

    async def _create_schema() -> None:
        async with engine.begin() as connection:
            await connection.exec_driver_sql("PRAGMA journal_mode=WAL")
            await connection.run_sync(Base.metadata.create_all)

    # 在 portal 事件循环内建表：与后续请求/后台任务共用同一连接池
    client.portal.call(_create_schema)

    installed: list[RuntimeContainer] = []

    def install_runtime(
        *,
        planner: Any | None = None,
        executor: Any | None = None,
        container_factory: Any | None = None,
    ) -> tuple[TaskExecution, RuntimeContainer]:
        agents = {
            "executor": executor or _RecordingExecutor(),
            "validator": _PassingValidator(),
            "reviewer": _StaticReviewer(),
        }

        def agent_factory(agent_type: str) -> Any:
            return agents[agent_type]

        factory = container_factory or RuntimeContainer
        container = factory(
            session_factory=session_factory,
            planner_factory=lambda: planner or PlannerAgent(llm=DeterministicPlanLLM()),
            agent_factory=agent_factory,
            event_publisher=InMemoryEventPublisher(),
        )
        execution = TaskExecution(container)
        app.state.container = container
        app.state.runtime = execution
        installed.append(container)
        return execution, container

    yield client, install_runtime, session_factory

    for container in installed:
        try:
            client.portal.call(container.close)
        except Exception:
            pass
    client.__exit__(None, None, None)
    app.dependency_overrides.clear()
    asyncio.run(engine.dispose())


# ===== 测试 =====


class TestHttpEndToEnd:
    def test_plan_approve_and_complete_without_manual_dag(self, recovery_env) -> None:
        """规划由确定性 LLM 适配器产出，HTTP 全链路跑到 COMPLETED。"""
        client, install_runtime, _ = recovery_env
        planner_llm = DeterministicPlanLLM()
        executor = _RecordingExecutor()
        install_runtime(planner=PlannerAgent(llm=planner_llm), executor=executor)
        token = _register_and_login(client, "e2e-alice")

        created = _create(client, token)
        assert created.status_code == 201
        task_id = created.json()["task_id"]

        waiting = _wait_status(client, token, task_id, {"waiting_approval"})
        assert waiting["plan_version"] == 1
        assert [subtask["node_id"] for subtask in waiting["subtasks"]] == [
            "n1",
            "n2",
            "n3",
            "n4",
            "n5",
        ]

        approved = client.post(
            f"/api/v1/tasks/{task_id}/plan/approve",
            json={"revision": 1},
            headers=_auth(token),
        )
        assert approved.status_code == 200

        completed = _wait_status(client, token, task_id, {"completed"})
        assert all(subtask["status"] == "completed" for subtask in completed["subtasks"])
        assert sorted(call["node_id"] for call in executor.calls) == [
            "n1",
            "n2",
            "n3",
            "n4",
            "n5",
        ]
        assert planner_llm.call_count == 1  # 计划来自 LLM，而非手工 DAG


class TestCrashRecovery:
    def test_recover_resumes_after_last_committed_node(self, recovery_env) -> None:
        """n1 提交后销毁 runtime，同库重建并 recover()：n1 不重跑，n2/n3 完成。"""
        client, install_runtime, _ = recovery_env
        gate = asyncio.Event()
        first_executor = _RecordingExecutor()
        _execution1, container1 = install_runtime(
            executor=first_executor,
            container_factory=partial(_PausingContainer, gate=gate),
        )
        token = _register_and_login(client, "crash-bob")
        task_id = _create(client, token).json()["task_id"]
        _wait_status(client, token, task_id, {"waiting_approval"})
        client.post(
            f"/api/v1/tasks/{task_id}/plan/approve",
            json={"revision": 1},
            headers=_auth(token),
        )

        # 等待 n1 提交（观察者随后阻塞，n2 不会开始）
        snapshot = _wait_subtask_status(client, token, task_id, "n1", "completed")
        assert first_executor.calls and first_executor.calls[0]["node_id"] == "n1"
        assert len(first_executor.calls) == 1

        # 模拟进程崩溃：取消被阻塞的后台执行任务
        client.portal.call(container1.close)
        crashed = client.get(f"/api/v1/tasks/{task_id}", headers=_auth(token)).json()
        assert crashed["status"] == "running"

        # 同库重建 runtime 并恢复
        second_executor = _RecordingExecutor()
        execution2, _container2 = install_runtime(executor=second_executor)
        client.portal.call(execution2.recover)

        recovered = _wait_status(client, token, task_id, {"completed"})
        assert sorted(call["node_id"] for call in second_executor.calls) == [
            "n2",
            "n3",
            "n4",
            "n5",
        ]

        by_node = {subtask["node_id"]: subtask for subtask in recovered["subtasks"]}
        assert by_node["n1"]["status"] == "completed"
        assert by_node["n1"]["retry_count"] == 0  # 仅一次尝试
        assert by_node["n2"]["retry_count"] == 0
        assert by_node["n3"]["retry_count"] == 0
        assert snapshot["status"] == "running"  # 崩溃时任务仍在执行中


class TestRecoveryStateLoading:
    def test_resets_abandoned_nodes_and_appends_event(self, recovery_env) -> None:
        """RecoveryManager 从数据库恢复：重置遗留运行态节点并追加恢复事件。"""
        client, _install_runtime, session_factory = recovery_env

        async def scenario() -> tuple[uuid.UUID, Any]:
            user_id = uuid.uuid4()
            plan = TaskPlan.model_validate(PLAN_PAYLOAD)
            dag = PlannerAgent.build_dag(plan)
            async with session_factory() as session:
                task = Task(
                    user_id=user_id,
                    title="恢复单测",
                    description="恢复单测",
                    task_type="literature_review",
                    status="running",
                    config={"trace_id": "trace-recovery"},
                )
                session.add(task)
                await session.flush()
                await TaskRepository(session).replace_plan(task, plan, dag)
                rows = (
                    (await session.execute(select(Subtask).where(Subtask.task_id == task.id)))
                    .scalars()
                    .all()
                )
                by_node = {row.node_id: row for row in rows}
                by_node["n1"].status = "completed"
                by_node["n2"].status = "running"
                by_node["n3"].status = "retrying"
                await session.commit()

                state = await RecoveryManager.load_state(session, task.id)
                await session.commit()
                return task.id, state

        async def read_statuses(task_id: uuid.UUID) -> dict[str, str]:
            async with session_factory() as session:
                rows = (
                    (await session.execute(select(Subtask).where(Subtask.task_id == task_id)))
                    .scalars()
                    .all()
                )
                return {row.node_id: row.status for row in rows}

        task_id, state = client.portal.call(scenario)

        assert state is not None
        assert state.pre_completed == {"n1"}
        assert state.reset_nodes == ["n2", "n3"]
        assert state.event is not None
        assert state.event.event_type == "task_recovery"
        assert state.event.payload["reset_nodes"] == ["n2", "n3"]

        statuses = client.portal.call(read_statuses, task_id)
        assert statuses == {
            "n1": "completed",
            "n2": "ready",
            "n3": "ready",
            "n4": "pending",
            "n5": "pending",
        }


def _wait_subtask_status(
    client: TestClient,
    token: str,
    task_id: str,
    node_id: str,
    expected: str,
    *,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """轮询直到指定子任务达到期望状态。"""
    import time

    deadline = time.time() + timeout
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = client.get(f"/api/v1/tasks/{task_id}", headers=_auth(token)).json()
        for subtask in last.get("subtasks", []):
            if subtask["node_id"] == node_id and subtask["status"] == expected:
                return last
        time.sleep(0.01)
    raise AssertionError(f"等待子任务 {node_id}={expected} 超时，当前: {last.get('subtasks')}")
