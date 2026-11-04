"""任务 Repository：owner-scoped 任务、DAG 与子任务持久化（Plan 2 Task 2）。

核心约定：
- 所有查询必须携带 user_id 过滤，跨用户访问一律返回"不存在"；
- 状态流转使用条件 UPDATE 原子执行，当前状态不在允许集合内时抛
  IllegalTaskStateError，避免并发下的非法跳转；
- 本层只 add/flush，不 commit，事务边界由 service 层控制。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, or_, select, update

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
        """查询某用户的任务；不属于该用户时返回 None（与不存在同语义）。

        **语义：仅 owner**（不认工作区共享）。需要双轨可见性时用 `get_visible`。
        """
        result = await self.db.execute(
            select(Task).where(Task.id == task_id, Task.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def list_for_user(self, user_id: UUID, *, skip: int = 0, limit: int = 20) -> list[Task]:
        """分页列出某用户的任务，按创建时间倒序。

        **语义：仅 owner**（不含工作区共享任务）。内部调用点（编排层按 task_id
        取任务）依赖这个语义，不要改成 `list_visible`。
        """
        safe_limit = max(1, min(limit, 100))
        result = await self.db.execute(
            select(Task)
            .where(Task.user_id == user_id)
            .order_by(Task.created_at.desc(), Task.id.desc())
            .offset(max(0, skip))
            .limit(safe_limit)
        )
        return list(result.scalars().all())

    # ===== 归属双轨（Phase 2 团队工作区）=====

    @staticmethod
    def _visible_clause(user_id: UUID, workspace_ids: Sequence[UUID]) -> Any:
        """可见性条件：个人任务（workspace_id IS NULL 且是我的）
        或 工作区任务（workspace_id 在我的工作区列表里）。

        ## 关于 workspace_id IS NULL（重要，别当冗余删掉）

        在当前 OR 结构里它**不是正确性必需**：同事的私人任务 user_id
        不等于我、workspace_id 又是 NULL（不在我的工作区列表里），两个分支
        都不匹配 —— 实测把 is_(None) 删掉后 9 条可见性测试**全绿**。

        保留它是为了**表达意图**：这条分支的语义是「我的个人任务」，
        不是「我创建的任何任务」。将来若引入「任务转移归属」或「工作区任务
        也保留 owner 但需区分个人/共享」，少了这个条件就会静默泄漏。

        > 已试过写一条「能区分它」的回归测试，结论是**在当前语义下不可能**
        > （两个分支天然互斥）。按 AGENTS #13，抓不住的断言不留在测试里装门面，
        > 改为在这里写清理由。
        """
        personal = and_(Task.user_id == user_id, Task.workspace_id.is_(None))
        if not workspace_ids:
            return personal
        return or_(personal, Task.workspace_id.in_(list(workspace_ids)))

    async def get_visible(
        self, user_id: UUID, workspace_ids: Sequence[UUID], task_id: UUID
    ) -> Task | None:
        """按双轨可见性取任务；不可见返回 `None`（端点层转 404）。"""
        result = await self.db.execute(
            select(Task).where(
                Task.id == task_id,
                self._visible_clause(user_id, workspace_ids),
            )
        )
        return result.scalar_one_or_none()

    async def list_summaries(
        self,
        user_id: UUID,
        workspace_ids: Sequence[UUID] = (),
        *,
        skip: int = 0,
        limit: int = 20,
    ) -> Any:
        """**单条 SQL** 只取列表页需要的列（id/status/title/plan_version/时间戳）。

        为什么不走 `list_visible` + 快照：列表页用不到 subtasks / plan /
        final_output，旧路径对每个任务还要补 2 条查询。远程库上每条 SQL
        有 ~380ms 往返，20 个任务就是 34 秒（2026-11-04 实测）。
        可见性与排序语义与 `list_visible` 完全一致（复用 `_visible_clause`，
        排序键带 `id`，AGENTS #14）。
        """
        safe_limit = max(1, min(limit, 100))
        stmt = (
            select(
                Task.id,
                Task.status,
                Task.title,
                Task.plan_version,
                Task.created_at,
                Task.updated_at,
            )
            .where(self._visible_clause(user_id, workspace_ids))
            .order_by(Task.created_at.desc(), Task.id.desc())
            .offset(max(0, skip))
            .limit(safe_limit)
        )
        return (await self.db.execute(stmt)).all()

    async def list_visible(
        self,
        user_id: UUID,
        workspace_ids: Sequence[UUID],
        *,
        skip: int = 0,
        limit: int = 20,
    ) -> list[Task]:
        """列出可见任务（个人 + 工作区），按创建时间倒序。

        排序键带 `id`：同事务写入的行 `created_at` 相同（AGENTS #14）。
        """
        safe_limit = max(1, min(limit, 100))
        result = await self.db.execute(
            select(Task)
            .where(self._visible_clause(user_id, workspace_ids))
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
