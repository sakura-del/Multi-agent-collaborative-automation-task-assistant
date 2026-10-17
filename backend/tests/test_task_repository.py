"""任务/事件 Repository 测试（Plan 2 Task 2）。

覆盖：
- owner-scoped 的 get / list（跨用户一律返回空）
- 原子状态流转：允许集合内成功，集合外抛 IllegalTaskStateError
- 规划替换：TaskPlan、TaskDAG、SUBTASK 在同一事务内整体替换
- 已完成节点查询
- 事件追加的递增序号、owner-scoped 回放与排序
- PostgreSQL 集成标记下的并发事件序号分配
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import delete, select

from app.db.models.log import ExecutionLog
from app.db.models.task import Subtask, Task, TaskDag
from app.db.models.user import User
from app.db.repositories.event import EventRepository
from app.db.repositories.task import IllegalTaskStateError, TaskRepository
from app.orchestration.contracts import PlanNode, TaskPlan, TaskStatus
from app.orchestration.dag_engine import TaskDAG


def _make_task(
    user_id: uuid.UUID,
    *,
    status: str = TaskStatus.CREATED.value,
    title: str = "文献综述任务",
) -> Task:
    return Task(user_id=user_id, title=title, description="测试任务", status=status)


def _make_plan(*, version: int = 1, nodes: list[PlanNode] | None = None) -> TaskPlan:
    return TaskPlan(
        version=version,
        topic="检索增强生成与证据核验",
        research_questions=["RAG 与 Self-RAG 的证据核验差异是什么？"],
        search_queries=["RAG evidence verification"],
        acceptance_criteria=["所有引用可回溯"],
        nodes=nodes
        or [
            PlanNode(id="n1", type="search", title="检索文献"),
            PlanNode(id="n2", type="write", title="撰写综述", dependencies=["n1"]),
        ],
    )


def _dag_from_plan(plan: TaskPlan) -> TaskDAG:
    dag = TaskDAG()
    for node in plan.nodes:
        dag.add_node(node.id, title=node.title, type=node.type)
    for node in plan.nodes:
        for dependency in node.dependencies:
            dag.add_edge(dependency, node.id)
    return dag


class TestTaskRepository:
    async def test_get_and_list_are_owner_scoped(self, db_session) -> None:
        repo = TaskRepository(db_session)
        owner, other = uuid.uuid4(), uuid.uuid4()
        first = await repo.create(_make_task(owner, title="任务一"))
        second = await repo.create(_make_task(owner, title="任务二"))
        await db_session.commit()

        assert (await repo.get_for_user(owner, first.id)).id == first.id  # type: ignore[union-attr]
        assert await repo.get_for_user(other, first.id) is None

        listed = await repo.list_for_user(owner)
        assert {task.id for task in listed} == {first.id, second.id}
        assert await repo.list_for_user(other) == []

        page = await repo.list_for_user(owner, skip=1, limit=1)
        assert len(page) == 1

    async def test_transition_allows_expected_state(self, db_session) -> None:
        repo = TaskRepository(db_session)
        task = await repo.create(_make_task(uuid.uuid4(), status=TaskStatus.CREATED.value))
        await db_session.flush()

        await repo.transition(task, {TaskStatus.CREATED}, TaskStatus.PLANNING)
        assert task.status == TaskStatus.PLANNING.value

        await repo.transition(task, {TaskStatus.PLANNING}, TaskStatus.WAITING_APPROVAL)
        await repo.transition(task, {TaskStatus.WAITING_APPROVAL}, TaskStatus.RUNNING)
        assert task.status == TaskStatus.RUNNING.value
        assert task.started_at is not None

        await repo.transition(task, {TaskStatus.RUNNING}, TaskStatus.COMPLETED)
        assert task.status == TaskStatus.COMPLETED.value
        assert task.completed_at is not None

    async def test_transition_rejects_illegal_state(self, db_session) -> None:
        repo = TaskRepository(db_session)
        task = await repo.create(_make_task(uuid.uuid4(), status=TaskStatus.CREATED.value))
        await db_session.flush()

        with pytest.raises(IllegalTaskStateError):
            await repo.transition(task, {TaskStatus.RUNNING}, TaskStatus.REVIEWING)
        assert task.status == TaskStatus.CREATED.value

    async def test_replace_plan_rewrites_dag_and_subtasks(self, db_session) -> None:
        repo = TaskRepository(db_session)
        task = await repo.create(_make_task(uuid.uuid4()))
        db_session.add(
            Subtask(
                task_id=task.id,
                node_id="legacy",
                title="旧节点",
                description="旧版本",
                status="completed",
            )
        )
        await db_session.flush()

        plan_v1 = _make_plan(version=1)
        dag_v1 = _dag_from_plan(plan_v1)
        await repo.replace_plan(task, plan_v1, dag_v1)
        await db_session.flush()

        assert task.plan_version == 1
        assert task.plan is not None
        assert task.plan["topic"] == plan_v1.topic

        subtasks = (
            (await db_session.execute(select(Subtask).where(Subtask.task_id == task.id)))
            .scalars()
            .all()
        )
        assert {subtask.node_id for subtask in subtasks} == {"n1", "n2"}
        assert all(subtask.status == "pending" for subtask in subtasks)

        stored_dag = await db_session.get(TaskDag, task.id)
        assert stored_dag is not None
        assert stored_dag.graph == dag_v1.to_dict()

        plan_v2 = _make_plan(
            version=2,
            nodes=[
                PlanNode(id="n1", type="search", title="检索文献"),
                PlanNode(id="n2", type="extract", title="提取证据", dependencies=["n1"]),
                PlanNode(id="n3", type="write", title="撰写综述", dependencies=["n2"]),
            ],
        )
        await repo.replace_plan(task, plan_v2, _dag_from_plan(plan_v2))
        await db_session.flush()

        assert task.plan_version == 2
        subtasks_v2 = (
            (await db_session.execute(select(Subtask).where(Subtask.task_id == task.id)))
            .scalars()
            .all()
        )
        assert {subtask.node_id for subtask in subtasks_v2} == {"n1", "n2", "n3"}

    async def test_completed_node_ids(self, db_session) -> None:
        repo = TaskRepository(db_session)
        task = await repo.create(_make_task(uuid.uuid4()))
        await db_session.flush()
        db_session.add_all(
            [
                Subtask(
                    task_id=task.id,
                    node_id="n1",
                    title="已完成",
                    description="done",
                    status="completed",
                ),
                Subtask(
                    task_id=task.id,
                    node_id="n2",
                    title="执行中",
                    description="running",
                    status="running",
                ),
                Subtask(
                    task_id=task.id,
                    node_id="n3",
                    title="已完成",
                    description="done",
                    status="completed",
                ),
            ]
        )
        await db_session.flush()

        assert await repo.completed_node_ids(task.id) == {"n1", "n3"}

    async def test_lock_for_update_returns_task(self, db_session) -> None:
        repo = TaskRepository(db_session)
        task = await repo.create(_make_task(uuid.uuid4()))
        await db_session.commit()

        locked = await repo.lock_for_update(task.id)
        assert locked.id == task.id


class TestEventRepository:
    async def test_append_allocates_increasing_event_seq(self, db_session) -> None:
        owner = uuid.uuid4()
        task_repo = TaskRepository(db_session)
        task = await task_repo.create(_make_task(owner, status=TaskStatus.PLANNING.value))
        await db_session.flush()

        repo = EventRepository(db_session)
        first = await repo.append(
            task.id,
            event_type="task_status",
            status=TaskStatus.PLANNING.value,
            payload={"from": "created", "to": "planning"},
        )
        second = await repo.append(
            task.id,
            event_type="node_started",
            status="running",
            attempt=1,
            payload={"node_id": "n1"},
        )
        third = await repo.append(task.id, event_type="node_completed", status="completed")
        await db_session.commit()

        assert [first.event_id, second.event_id, third.event_id] == [1, 2, 3]
        assert second.payload == {"node_id": "n1"}

        logs = await repo.events_after(owner, task.id, 0)
        assert [log.event_seq for log in logs] == [1, 2, 3]
        assert logs[0].message == "task_status"
        assert logs[1].attempt == 1
        assert logs[1].context == {"status": "running", "node_id": "n1"}

    async def test_events_after_is_owner_scoped_and_ordered(self, db_session) -> None:
        owner, other = uuid.uuid4(), uuid.uuid4()
        task_repo = TaskRepository(db_session)
        task = await task_repo.create(_make_task(owner))
        await db_session.flush()

        repo = EventRepository(db_session)
        await repo.append(task.id, event_type="a", status="created")
        await repo.append(task.id, event_type="b", status="planning")
        await repo.append(task.id, event_type="c", status="running")
        await db_session.commit()

        events = await repo.events_after(owner, task.id, 0)
        assert [log.event_seq for log in events] == [1, 2, 3]
        assert [log.event_type for log in events] == ["a", "b", "c"]

        tail = await repo.events_after(owner, task.id, 1)
        assert [log.event_seq for log in tail] == [2, 3]

        assert await repo.events_after(other, task.id, 0) == []

        converted = EventRepository.to_event(events[1])
        assert converted.event_id == 2
        assert converted.status == "planning"
        assert converted.payload == {}

    async def test_append_rejects_unknown_task(self, db_session) -> None:
        repo = EventRepository(db_session)
        with pytest.raises(LookupError):
            await repo.append(uuid.uuid4(), event_type="log", status="created")


_POSTGRES_URL = os.environ.get("DATABASE__URL", "")
requires_postgres = pytest.mark.skipif(
    not _POSTGRES_URL.startswith("postgresql"),
    reason="需要 PostgreSQL 集成环境（DATABASE__URL 指向 PostgreSQL）",
)


@pytest.mark.integration
@requires_postgres
async def test_event_seq_concurrent_allocation_on_postgres() -> None:
    """两个并发 session 同时 append 时，event_seq 必须唯一且连续。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(_POSTGRES_URL)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    user_id = uuid.uuid4()
    task_id: uuid.UUID | None = None
    suffix = uuid.uuid4().hex[:8]
    try:
        async with session_factory() as setup:
            setup.add(
                User(
                    id=user_id,
                    username=f"concurrent_{suffix}",
                    email=f"concurrent_{suffix}@test.com",
                    password_hash="not-used",
                    role="user",
                )
            )
            await setup.flush()
            task = Task(
                user_id=user_id,
                title="并发事件任务",
                description="并发序号测试",
                status=TaskStatus.RUNNING.value,
            )
            setup.add(task)
            await setup.commit()
            task_id = task.id

        async def append_once() -> int:
            async with session_factory() as session:
                event = await EventRepository(session).append(
                    task_id, event_type="concurrent", status="running"
                )
                await session.commit()
                return event.event_id

        results = await asyncio.gather(append_once(), append_once())
        assert sorted(results) == [1, 2]

        async with session_factory() as check:
            logs = await EventRepository(check).events_after(user_id, task_id, 0)
            assert [log.event_seq for log in logs] == [1, 2]
    finally:
        async with session_factory() as cleanup:
            if task_id is not None:
                await cleanup.execute(delete(ExecutionLog).where(ExecutionLog.task_id == task_id))
                await cleanup.execute(delete(Task).where(Task.id == task_id))
            await cleanup.execute(delete(User).where(User.id == user_id))
            await cleanup.commit()
        await engine.dispose()
