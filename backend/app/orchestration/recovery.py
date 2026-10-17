"""断点续传与异常恢复：状态持久化 + 重启恢复 + 从断点继续。

职责：
- 将调度状态（DAG 结构 + 已完成节点 + 节点状态快照）序列化持久化
- 服务重启后从持久化数据恢复调度上下文
- 从最后成功节点继续执行剩余子图，不重复已完成任务

设计要点：
- 持久化结构对齐数据库 TaskDag.graph + Subtask.status
- 恢复时重建 TaskDAG 和已完成集合，直接传入 scheduler.run(pre_completed=...)
- 未完成节点的状态机恢复为 PENDING（从断点重新调度）
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.core.logging import get_logger
from app.orchestration.dag_engine import TaskDAG

if TYPE_CHECKING:  # 仅类型检查：运行期按需导入，避免 orchestration ↔ db 循环依赖
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.orchestration.contracts import TaskEvent

logger = get_logger(__name__)

# 上次进程遗留的运行态节点：恢复时统一重置为 READY，避免调度器误判
ABANDONED_NODE_STATUSES = frozenset({"running", "validating", "retrying"})


@dataclass
class ScheduleSnapshot:
    """调度状态快照：可序列化的断点续传数据。

    结构对齐数据库：dag 对应 TaskDag.graph，node_states 对应 Subtask.status。
    """

    task_id: str
    dag: dict[str, Any]  # TaskDAG.to_dict() 结构
    completed_nodes: list[str] = field(default_factory=list)
    node_states: dict[str, str] = field(default_factory=dict)  # node_id → status

    def to_json(self) -> str:
        """序列化为 JSON 字符串，供持久化存储。"""
        return json.dumps(asdict(self), ensure_ascii=False, default=str)

    @classmethod
    def from_json(cls, data: str) -> ScheduleSnapshot:
        """从 JSON 反序列化，用于重启恢复。"""
        obj = json.loads(data)
        return cls(
            task_id=obj["task_id"],
            dag=obj["dag"],
            completed_nodes=obj.get("completed_nodes", []),
            node_states=obj.get("node_states", {}),
        )


@dataclass
class RecoveryState:
    """从数据库恢复出的调度上下文。

    - `dag`：TaskDag.graph 还原的 DAG；
    - `pre_completed`：已完成节点，传给 `Scheduler.run(pre_completed=...)`；
    - `reset_nodes`：被重置为 READY 的遗留运行态节点；
    - `event`：追加的 `task_recovery` 事件（无重置时为 None）。
    """

    dag: TaskDAG
    pre_completed: set[str]
    reset_nodes: list[str] = field(default_factory=list)
    event: TaskEvent | None = None


class RecoveryManager:
    """断点续传管理器：持久化与恢复调度状态。

    用法：
        # 持久化（调度中断或完成时）
        snapshot = RecoveryManager.create_snapshot(task_id, dag, scheduler)
        await storage.save(snapshot.to_json())

        # 恢复（服务重启时）
        data = await storage.load(task_id)
        snapshot = ScheduleSnapshot.from_json(data)
        dag, completed = RecoveryManager.restore(snapshot)
        await scheduler.run(dag, task_id=task_id, pre_completed=completed)

        # 生产路径（PostgreSQL 为事实来源）
        state = await RecoveryManager.load_state(session, task_id)
        await scheduler.run(state.dag, task_id=task_id, pre_completed=state.pre_completed)
    """

    @staticmethod
    async def load_state(
        session: AsyncSession,
        task_id: UUID,
        *,
        append_event: bool = True,
    ) -> RecoveryState | None:
        """从数据库恢复调度上下文：TaskDag.graph + Subtask 状态。

        - 已完成子任务 → `pre_completed`（Scheduler 直接跳过，不重复执行）；
        - 遗留 RUNNING / VALIDATING / RETRYING 节点 → 重置为 READY；
        - 发生重置时追加一条 `task_recovery` 事件（由调用方提交后发布）。

        返回 None 表示任务或 DAG 缺失，调用方应转入人工处理。
        """
        from sqlalchemy import select

        from app.db.models.task import Subtask, Task, TaskDag
        from app.db.repositories.event import EventRepository
        from app.db.repositories.task import TaskRepository
        from app.orchestration.task_state import SubtaskStatus

        task = await session.get(Task, task_id)
        if task is None:
            return None
        dag_row = await session.get(TaskDag, task_id)
        if dag_row is None or not dag_row.graph:
            return None

        dag = TaskDAG.from_dict(dag_row.graph)
        repo = TaskRepository(session)
        completed = await repo.completed_node_ids(task_id)

        rows = (
            (await session.execute(select(Subtask).where(Subtask.task_id == task_id)))
            .scalars()
            .all()
        )
        reset_nodes: list[str] = []
        for row in rows:
            if row.status in ABANDONED_NODE_STATUSES:
                row.status = SubtaskStatus.READY.value
                reset_nodes.append(row.node_id)
        reset_nodes.sort()

        event: TaskEvent | None = None
        if append_event and reset_nodes:
            event = await EventRepository(session).append(
                task_id,
                event_type="task_recovery",
                status=task.status,
                payload={
                    "reset_nodes": reset_nodes,
                    "completed": sorted(completed),
                },
            )

        logger.info(
            "recovery_state_loaded",
            task_id=str(task_id),
            completed=len(completed),
            reset=len(reset_nodes),
        )
        return RecoveryState(
            dag=dag,
            pre_completed=completed,
            reset_nodes=reset_nodes,
            event=event,
        )

    @staticmethod
    def create_snapshot(
        task_id: str,
        dag: TaskDAG,
        completed_nodes: set[str],
        node_states: dict[str, str] | None = None,
    ) -> ScheduleSnapshot:
        """从当前调度状态创建快照。"""
        snapshot = ScheduleSnapshot(
            task_id=task_id,
            dag=dag.to_dict(),
            completed_nodes=sorted(completed_nodes),
            node_states=node_states or {},
        )
        logger.info(
            "snapshot_created",
            task_id=task_id,
            completed=len(snapshot.completed_nodes),
            total=dag.node_count,
        )
        return snapshot

    @staticmethod
    def restore(snapshot: ScheduleSnapshot) -> tuple[TaskDAG, set[str]]:
        """从快照恢复 DAG 与已完成节点集合。

        返回 (dag, completed)，直接传入 scheduler.run(pre_completed=completed)。
        """
        dag = TaskDAG.from_dict(snapshot.dag)
        completed = set(snapshot.completed_nodes)

        # 提取未完成子图（断点续传：仅调度剩余节点）
        pending_dag = dag.get_pending_subgraph(completed)

        logger.info(
            "snapshot_restored",
            task_id=snapshot.task_id,
            total_nodes=dag.node_count,
            completed=len(completed),
            pending=pending_dag.node_count,
        )
        # 返回完整 DAG 和已完成集合，调度器内部按 completed 过滤就绪节点
        return dag, completed

    @staticmethod
    def get_resume_nodes(snapshot: ScheduleSnapshot) -> list[str]:
        """获取需要恢复执行的节点列表（排除已完成）。

        用于重启后向调度器报告待恢复任务。
        """
        dag = TaskDAG.from_dict(snapshot.dag)
        completed = set(snapshot.completed_nodes)
        return [n for n in dag.topological_order() if n not in completed]

    @staticmethod
    def is_resumable(snapshot: ScheduleSnapshot) -> bool:
        """判断快照是否可恢复（有未完成节点且 DAG 合法）。"""
        try:
            dag = TaskDAG.from_dict(snapshot.dag)
        except Exception:
            return False
        completed = set(snapshot.completed_nodes)
        return len(completed) < dag.node_count
