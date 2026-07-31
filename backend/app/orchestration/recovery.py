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
from typing import Any

from app.core.logging import get_logger
from app.orchestration.dag_engine import TaskDAG

logger = get_logger(__name__)


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
    """

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
