"""断点续传与异常恢复单元测试。"""

from app.orchestration.dag_engine import TaskDAG
from app.orchestration.recovery import RecoveryManager, ScheduleSnapshot


class TestScheduleSnapshot:
    """快照序列化测试。"""

    def test_to_json_and_from_json_roundtrip(self) -> None:
        """快照序列化与反序列化保持数据一致。"""
        dag = TaskDAG()
        dag.add_node("n1", title="任务1")
        dag.add_node("n2", title="任务2")
        dag.add_edge("n1", "n2")
        snapshot = ScheduleSnapshot(
            task_id="t1",
            dag=dag.to_dict(),
            completed_nodes=["n1"],
            node_states={"n1": "completed", "n2": "pending"},
        )
        json_str = snapshot.to_json()
        restored = ScheduleSnapshot.from_json(json_str)
        assert restored.task_id == "t1"
        assert restored.completed_nodes == ["n1"]
        assert restored.node_states["n1"] == "completed"
        assert len(restored.dag["nodes"]) == 2


class TestRecoveryManager:
    """恢复管理器测试。"""

    def test_create_snapshot(self) -> None:
        """创建快照包含完整调度状态。"""
        dag = TaskDAG()
        dag.add_node("n1", title="任务1")
        dag.add_node("n2", title="任务2")
        dag.add_edge("n1", "n2")
        snapshot = RecoveryManager.create_snapshot(
            task_id="t1",
            dag=dag,
            completed_nodes={"n1"},
            node_states={"n1": "completed"},
        )
        assert snapshot.task_id == "t1"
        assert snapshot.completed_nodes == ["n1"]
        assert len(snapshot.dag["nodes"]) == 2

    def test_restore_returns_dag_and_completed(self) -> None:
        """恢复返回 DAG 和已完成集合。"""
        dag = TaskDAG()
        dag.add_node("n1", title="任务1")
        dag.add_node("n2", title="任务2")
        dag.add_node("n3", title="任务3")
        dag.add_edge("n1", "n2")
        dag.add_edge("n2", "n3")
        snapshot = RecoveryManager.create_snapshot(
            task_id="t1", dag=dag, completed_nodes={"n1", "n2"}
        )
        restored_dag, completed = RecoveryManager.restore(snapshot)
        assert completed == {"n1", "n2"}
        assert restored_dag.node_count == 3

    def test_get_resume_nodes(self) -> None:
        """获取待恢复节点列表（排除已完成）。"""
        dag = TaskDAG()
        dag.add_node("n1", title="任务1")
        dag.add_node("n2", title="任务2")
        dag.add_node("n3", title="任务3")
        dag.add_edge("n1", "n2")
        dag.add_edge("n2", "n3")
        snapshot = RecoveryManager.create_snapshot(task_id="t1", dag=dag, completed_nodes={"n1"})
        resume = RecoveryManager.get_resume_nodes(snapshot)
        assert resume == ["n2", "n3"]

    def test_is_resumable_with_pending(self) -> None:
        """有未完成节点时可恢复。"""
        dag = TaskDAG()
        dag.add_node("n1", title="任务1")
        dag.add_node("n2", title="任务2")
        snapshot = RecoveryManager.create_snapshot(task_id="t1", dag=dag, completed_nodes={"n1"})
        assert RecoveryManager.is_resumable(snapshot)

    def test_is_resumable_all_completed(self) -> None:
        """全部完成时不可恢复（无需恢复）。"""
        dag = TaskDAG()
        dag.add_node("n1", title="任务1")
        snapshot = RecoveryManager.create_snapshot(task_id="t1", dag=dag, completed_nodes={"n1"})
        assert not RecoveryManager.is_resumable(snapshot)

    def test_restore_after_serialization(self) -> None:
        """序列化→反序列化→恢复的完整断点续传流程。"""
        dag = TaskDAG()
        for i in range(1, 4):
            dag.add_node(f"n{i}", title=f"任务{i}")
        dag.add_edge("n1", "n2")
        dag.add_edge("n2", "n3")
        # 创建快照并序列化
        snapshot = RecoveryManager.create_snapshot(task_id="t1", dag=dag, completed_nodes={"n1"})
        json_data = snapshot.to_json()
        # 反序列化并恢复
        restored_snapshot = ScheduleSnapshot.from_json(json_data)
        restored_dag, completed = RecoveryManager.restore(restored_snapshot)
        assert completed == {"n1"}
        assert restored_dag.node_count == 3
        # 待恢复节点
        resume = RecoveryManager.get_resume_nodes(restored_snapshot)
        assert resume == ["n2", "n3"]
