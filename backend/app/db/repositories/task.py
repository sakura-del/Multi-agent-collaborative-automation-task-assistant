"""任务 Repository：owner-scoped 任务、DAG 与子任务持久化（Plan 2 Task 2）。

核心约定：
- 所有查询必须携带 user_id 过滤，跨用户访问一律返回"不存在"；
- 状态流转使用条件 UPDATE 原子执行，当前状态不在允许集合内时抛
  IllegalTaskStateError，避免并发下的非法跳转；
- 本层只 add/flush，不 commit，事务边界由 service 层控制。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select, update

from app.db.models.task import Subtask, Task, TaskDag
from app.db.repositories.base import BaseRepository
from app.orchestration.contracts import TaskPlan, TaskStatus
from app.orchestration.dag_engine import TaskDAG

# 任务进入终态时需要写入 completed_at 的目标状态
_TERMINAL_STATUSES = frozenset({TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED})


class IllegalTaskStateError(Exception):
    """任务当前状态不在允许集合内，拒绝状态转换。"""


class TaskRepository(BaseRepository[Task]):
    """Task 的 owner-scoped 数据访问与原子状态流转。"""

    model = Task

    async def get_for_user(self, user_id: UUID, task_id: UUID) -> Task | None:
        """查询某用户的任务；不属于该用户时返回 None（与不存在同语义）。"""
        result = await self.db.execute(
            select(Task).where(Task.id == task_id, Task.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def list_for_user(self, user_id: UUID, *, skip: int = 0, limit: int = 20) -> list[Task]:
        """分页列出某用户的任务，按创建时间倒序。"""
        safe_limit = max(1, min(limit, 100))
        result = await self.db.execute(
            select(Task)
            .where(Task.user_id == user_id)
            .order_by(Task.created_at.desc(), Task.id.desc())
            .offset(max(0, skip))
            .limit(safe_limit)
        )
        return list(result.scalars().all())

    async def lock_for_update(self, task_id: UUID) -> Task:
        """锁定任务行（SELECT ... FOR UPDATE），用于后台执行前的并发保护。

        SQLite 测试环境会忽略 FOR UPDATE；PostgreSQL 下由数据库串行化。
        """
        result = await self.db.execute(select(Task).where(Task.id == task_id).with_for_update())
        task = result.scalar_one_or_none()
        if task is None:
            raise LookupError(f"任务不存在: {task_id}")
        return task

    async def replace_plan(self, task: Task, plan: TaskPlan, dag: TaskDAG) -> None:
        """用新版本规划整体替换 DAG 与子任务。

        调用方负责事务提交；本方法保证旧子任务先删除并 flush，
        避免 (task_id, node_id) 唯一约束在新旧节点同 ID 时冲突。
        """
        task.plan = plan.model_dump(mode="json")
        task.plan_version = plan.version

        graph = dag.to_dict()
        existing_dag = await self.db.get(TaskDag, task.id)
        if existing_dag is None:
            self.db.add(TaskDag(task_id=task.id, graph=graph))
        else:
            existing_dag.graph = graph

        await self.db.execute(delete(Subtask).where(Subtask.task_id == task.id))
        await self.db.flush()

        for node in plan.nodes:
            self.db.add(
                Subtask(
                    task_id=task.id,
                    node_id=node.id,
                    title=node.title,
                    description=f"{node.type}: {node.title}",
                    status="pending",
                    input_data=dict(node.input),
                )
            )
        await self.db.flush()

    async def transition(
        self,
        task: Task,
        expected: set[TaskStatus],
        target: TaskStatus,
    ) -> None:
        """原子状态转换：仅当数据库中的当前状态属于 expected 时才更新。

        传入的 task 对象可能已过期，因此以条件 UPDATE 的 rowcount 判定成功，
        成功后再把内存对象同步到新状态。
        """
        expected_values = {status.value for status in expected}
        now = datetime.now(UTC)
        values: dict[str, Any] = {"status": target.value}
        if target is TaskStatus.RUNNING and task.started_at is None:
            values["started_at"] = now
        if target in _TERMINAL_STATUSES and task.completed_at is None:
            values["completed_at"] = now

        # 用 Core UPDATE 直接操作表，避免 ORM-enabled update 使 onupdate 列过期
        table = Task.__table__
        statement = (
            update(table)  # type: ignore[arg-type]
            .where(table.c.id == task.id, table.c.status.in_(expected_values))
            .values(**values)
        )
        result = await self.db.execute(statement)
        # Core UPDATE 运行期返回 CursorResult（有 rowcount），mypy 只看到 Result
        if result.rowcount != 1:  # type: ignore[attr-defined]
            raise IllegalTaskStateError(
                f"任务 {task.id} 当前状态 {task.status!r} 不在允许集合 "
                f"{sorted(expected_values)} 内，拒绝转换到 {target.value}"
            )

        task.status = target.value
        if "started_at" in values:
            task.started_at = values["started_at"]
        if "completed_at" in values:
            task.completed_at = values["completed_at"]

    async def completed_node_ids(self, task_id: UUID) -> set[str]:
        """返回任务下所有已完成子任务的 node_id 集合。"""
        result = await self.db.execute(
            select(Subtask.node_id).where(
                Subtask.task_id == task_id,
                Subtask.status == "completed",
            )
        )
        return set(result.scalars().all())
