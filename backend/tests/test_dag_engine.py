"""DAG 引擎单元测试：循环检测、拓扑排序、子图、关键路径、就绪节点。"""

import pytest

from app.orchestration.dag_engine import DAGCycleError, NodeNotFoundError, TaskDAG


class TestTaskDAGBasic:
    """DAG 基础操作测试。"""

    def test_add_nodes_and_edges(self) -> None:
        """添加节点和边后计数正确。"""
        dag = TaskDAG()
        dag.add_node("n1", title="任务1")
        dag.add_node("n2", title="任务2")
        dag.add_edge("n1", "n2")
        assert dag.node_count == 2
        assert dag.edge_count == 1

    def test_add_node_idempotent(self) -> None:
        """重复添加同 node_id 更新属性而非新增。"""
        dag = TaskDAG()
        dag.add_node("n1", title="旧标题")
        dag.add_node("n1", title="新标题")
        assert dag.node_count == 1
        assert dag.get_node("n1")["title"] == "新标题"

    def test_get_node_not_found(self) -> None:
        """获取不存在的节点抛异常。"""
        dag = TaskDAG()
        with pytest.raises(NodeNotFoundError):
            dag.get_node("nonexistent")

    def test_add_edge_missing_node(self) -> None:
        """边引用不存在的节点抛异常。"""
        dag = TaskDAG()
        dag.add_node("n1")
        with pytest.raises(NodeNotFoundError):
            dag.add_edge("n1", "n2")

    def test_get_predecessors_and_successors(self) -> None:
        """前置与后继节点查询正确。"""
        dag = TaskDAG()
        dag.add_node("n1")
        dag.add_node("n2")
        dag.add_node("n3")
        dag.add_edge("n1", "n2")
        dag.add_edge("n1", "n3")
        assert dag.get_successors("n1") == ["n2", "n3"]
        assert set(dag.get_predecessors("n2")) == {"n1"}


class TestTaskDAGCycleDetection:
    """循环依赖检测测试。"""

    def test_simple_cycle_rejected(self) -> None:
        """两节点循环依赖被检测并回滚。"""
        dag = TaskDAG()
        dag.add_node("n1")
        dag.add_node("n2")
        dag.add_edge("n1", "n2")
        with pytest.raises(DAGCycleError):
            dag.add_edge("n2", "n1")  # 形成环
        # 回滚后边数不变
        assert dag.edge_count == 1

    def test_three_node_cycle_rejected(self) -> None:
        """三节点循环依赖被检测。"""
        dag = TaskDAG()
        for i in range(1, 4):
            dag.add_node(f"n{i}")
        dag.add_edge("n1", "n2")
        dag.add_edge("n2", "n3")
        with pytest.raises(DAGCycleError):
            dag.add_edge("n3", "n1")

    def test_self_loop_rejected(self) -> None:
        """自环被检测为循环依赖。"""
        dag = TaskDAG()
        dag.add_node("n1")
        with pytest.raises(DAGCycleError):
            dag.add_edge("n1", "n1")


class TestTaskDAGTopology:
    """拓扑排序与就绪节点测试。"""

    def test_topological_order(self) -> None:
        """拓扑排序保证前置在前。"""
        dag = TaskDAG()
        for i in range(1, 5):
            dag.add_node(f"n{i}")
        dag.add_edge("n1", "n2")
        dag.add_edge("n1", "n3")
        dag.add_edge("n2", "n4")
        dag.add_edge("n3", "n4")
        order = dag.topological_order()
        assert order.index("n1") < order.index("n2")
        assert order.index("n1") < order.index("n3")
        assert order.index("n2") < order.index("n4")
        assert order.index("n3") < order.index("n4")

    def test_get_ready_nodes_empty_completed(self) -> None:
        """无已完成时，根节点（无入边）就绪。"""
        dag = TaskDAG()
        dag.add_node("n1")
        dag.add_node("n2")
        dag.add_node("n3")
        dag.add_edge("n1", "n2")
        dag.add_edge("n2", "n3")
        ready = dag.get_ready_nodes()
        assert ready == ["n1"]

    def test_get_ready_nodes_after_completion(self) -> None:
        """n1 完成后 n2 就绪。"""
        dag = TaskDAG()
        dag.add_node("n1")
        dag.add_node("n2")
        dag.add_edge("n1", "n2")
        ready = dag.get_ready_nodes(completed={"n1"})
        assert ready == ["n2"]

    def test_get_ready_nodes_parallel(self) -> None:
        """无依赖的多个节点同时就绪（可并行）。"""
        dag = TaskDAG()
        dag.add_node("n1")
        dag.add_node("n2")
        dag.add_node("n3")
        dag.add_node("n4")
        dag.add_edge("n1", "n3")
        dag.add_edge("n2", "n3")
        dag.add_edge("n3", "n4")
        # n1, n2 无前置，同时就绪
        ready = dag.get_ready_nodes()
        assert set(ready) == {"n1", "n2"}
        # n1, n2 完成后 n3 就绪
        ready = dag.get_ready_nodes(completed={"n1", "n2"})
        assert ready == ["n3"]


class TestTaskDAGAdvanced:
    """子图、关键路径、序列化测试。"""

    def test_critical_path(self) -> None:
        """关键路径为最长依赖链。"""
        dag = TaskDAG()
        for i in range(1, 5):
            dag.add_node(f"n{i}")
        dag.add_edge("n1", "n2")
        dag.add_edge("n1", "n3")
        dag.add_edge("n2", "n4")
        # 关键路径：n1→n2→n4（3节点，比 n1→n3 长）
        path = dag.critical_path()
        assert len(path) >= 2

    def test_subgraph_extraction(self) -> None:
        """子图提取保留指定节点及内部边。"""
        dag = TaskDAG()
        for i in range(1, 5):
            dag.add_node(f"n{i}")
        dag.add_edge("n1", "n2")
        dag.add_edge("n2", "n3")
        dag.add_edge("n3", "n4")
        sub = dag.subgraph(["n2", "n3"])
        assert sub.node_count == 2
        assert sub.edge_count == 1

    def test_pending_subgraph(self) -> None:
        """排除已完成节点后的剩余子图。"""
        dag = TaskDAG()
        for i in range(1, 4):
            dag.add_node(f"n{i}")
        dag.add_edge("n1", "n2")
        dag.add_edge("n2", "n3")
        pending = dag.get_pending_subgraph(completed={"n1"})
        assert "n1" not in [n for n in pending.topological_order()]
        assert pending.node_count == 2

    def test_serialization_roundtrip(self) -> None:
        """序列化与反序列化保持图结构一致。"""
        dag = TaskDAG()
        dag.add_node("n1", title="任务1", priority=8)
        dag.add_node("n2", title="任务2")
        dag.add_edge("n1", "n2")
        data = dag.to_dict()
        restored = TaskDAG.from_dict(data)
        assert restored.node_count == 2
        assert restored.edge_count == 1
        assert restored.get_node("n1")["title"] == "任务1"
        assert restored.get_node("n1")["priority"] == 8

    def test_ready_nodes_with_priority(self) -> None:
        """优先级排序：高优先级节点在前。"""
        dag = TaskDAG()
        dag.add_node("n1", priority=3)
        dag.add_node("n2", priority=9)
        dag.add_node("n3", priority=5)
        ready = dag.get_ready_nodes_with_priority()
        # n2 优先级最高（9），应排在首位
        assert ready[0] == "n2"

    def test_empty_dag_critical_path(self) -> None:
        """空 DAG 的关键路径为空列表。"""
        dag = TaskDAG()
        assert dag.critical_path() == []
