"""DAG 任务编排引擎：基于 networkx 的有向无环图。

职责：
- 节点/边管理：添加子任务节点与依赖边
- 循环检测：拒绝非法图结构，保证 DAG 性质
- 拓扑排序：计算执行顺序
- 子图提取：按节点集合裁剪子图
- 关键路径分析：识别最长依赖链，优化调度
- 就绪节点查询：获取前置全部完成的可执行节点

设计要点：
- networkx.DiGraph 作为底层图结构，避免重复造轮子（计算优化）
- 节点属性存储子任务元数据（title/description/agent_type/priority）
- 所有写操作先校验无环再执行，保证图始终合法（防御式编程）
"""

from __future__ import annotations

from typing import Any

import networkx as nx

from app.core.logging import get_logger

logger = get_logger(__name__)


class DAGCycleError(Exception):
    """DAG 存在循环依赖，图结构非法。"""


class NodeNotFoundError(KeyError):
    """操作的节点不存在。"""


class TaskDAG:
    """任务 DAG 图：管理子任务节点与依赖关系。

    用法：
        dag = TaskDAG()
        dag.add_node("n1", title="收集数据", agent_type="executor")
        dag.add_node("n2", title="撰写报告", agent_type="executor")
        dag.add_edge("n1", "n2")  # n1 完成后才能执行 n2
        ready = dag.get_ready_nodes()  # ["n1"]，n1 无前置依赖
    """

    def __init__(self) -> None:
        self._graph: nx.DiGraph = nx.DiGraph()

    @property
    def graph(self) -> nx.DiGraph:
        """底层 networkx 图（只读访问，外部不应修改）。"""
        return self._graph

    @property
    def node_count(self) -> int:
        return self._graph.number_of_nodes()

    @property
    def edge_count(self) -> int:
        return self._graph.number_of_edges()

    def add_node(self, node_id: str, **attrs: Any) -> None:
        """添加节点，携带子任务元数据属性。

        重复添加同 node_id 会更新属性（幂等）。
        """
        self._graph.add_node(node_id, **attrs)

    def add_edge(self, from_node: str, to_node: str) -> None:
        """添加依赖边：from_node 完成后才能执行 to_node。

        添加后立即检测循环，非法则回滚并抛异常。
        """
        if from_node not in self._graph or to_node not in self._graph:
            missing = {from_node, to_node} - set(self._graph.nodes)
            raise NodeNotFoundError(f"节点不存在: {missing}")
        self._graph.add_edge(from_node, to_node)
        if not nx.is_directed_acyclic_graph(self._graph):
            self._graph.remove_edge(from_node, to_node)
            raise DAGCycleError(f"添加边 {from_node}→{to_node} 会产生循环依赖，已回滚")

    def has_node(self, node_id: str) -> bool:
        return node_id in self._graph

    def get_node(self, node_id: str) -> dict[str, Any]:
        """获取节点属性。"""
        if node_id not in self._graph:
            raise NodeNotFoundError(node_id)
        return dict(self._graph.nodes[node_id])

    def get_predecessors(self, node_id: str) -> list[str]:
        """获取直接前置节点列表。"""
        return list(self._graph.predecessors(node_id))

    def get_successors(self, node_id: str) -> list[str]:
        """获取直接后继节点列表。"""
        return list(self._graph.successors(node_id))

    def topological_order(self) -> list[str]:
        """返回拓扑排序后的节点列表。

        循环依赖时抛 DAGCycleError（正常使用不应触发，add_edge 已防护）。
        """
        if not nx.is_directed_acyclic_graph(self._graph):
            raise DAGCycleError("图存在循环依赖，无法拓扑排序")
        return list(nx.topological_sort(self._graph))

    def get_ready_nodes(self, completed: set[str] | None = None) -> list[str]:
        """获取当前可执行的节点：所有前置均已完成。

        参数 completed 为已完成的节点集合，未指定时取图中无入边的根节点。
        """
        completed = completed or set()
        ready: list[str] = []
        for node_id in self._graph.nodes:
            if node_id in completed:
                continue
            preds = set(self.get_predecessors(node_id))
            if preds.issubset(completed):
                ready.append(node_id)
        return ready

    def get_ready_nodes_with_priority(self, completed: set[str] | None = None) -> list[str]:
        """获取就绪节点并按优先级排序。

        优先级 = 紧急度(节点priority属性) × 依赖深度(后继链长度)
        高优先级节点先执行，缩短关键路径。
        """
        ready = self.get_ready_nodes(completed)

        # 依赖深度：该节点到叶节点的最长路径长度，越深越优先
        def priority_key(nid: str) -> tuple[int, int]:
            attrs = self.get_node(nid)
            urgency = attrs.get("priority", 5)
            depth = self._dependency_depth(nid)
            return (-(urgency * (1 + depth)), nid)  # 负号降序

        return sorted(ready, key=priority_key)

    def _dependency_depth(self, node_id: str) -> int:
        """计算节点到叶节点的最长路径长度（依赖深度）。"""
        descendants = list(nx.descendants(self._graph, node_id))
        if not descendants:
            return 0
        # 最长路径长度
        sub = self._graph.subgraph([node_id, *descendants])
        try:
            return nx.dag_longest_path_length(sub)
        except nx.NetworkXError:
            return 0

    def critical_path(self) -> list[str]:
        """关键路径：图中最长依赖链，决定任务最短完成时间。

        调度器优先分配关键路径上的节点以缩短总耗时。
        """
        if self.node_count == 0:
            return []
        if not nx.is_directed_acyclic_graph(self._graph):
            raise DAGCycleError("图存在循环依赖")
        return nx.dag_longest_path(self._graph)

    def subgraph(self, nodes: list[str]) -> TaskDAG:
        """提取子图：仅保留指定节点及其内部边。

        用于断点续传时提取未完成部分的子图。
        """
        sub = self._graph.subgraph(nodes).copy()
        new_dag = TaskDAG()
        new_dag._graph = sub
        return new_dag

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典，对齐数据库 TaskDag.graph 结构。"""
        return {
            "nodes": [{"node_id": nid, **attrs} for nid, attrs in self._graph.nodes(data=True)],
            "edges": [{"from": u, "to": v} for u, v in self._graph.edges()],
            "topological_order": self.topological_order(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskDAG:
        """从字典反序列化，用于从数据库恢复 DAG。"""
        dag = cls()
        for node in data.get("nodes", []):
            # 复制后 pop，避免修改原始数据（快照可能被多次恢复）
            node_data = dict(node)
            nid = node_data.pop("node_id")
            dag.add_node(nid, **node_data)
        for edge in data.get("edges", []):
            dag.add_edge(edge["from"], edge["to"])
        return dag

    def get_pending_subgraph(self, completed: set[str]) -> TaskDAG:
        """提取未完成部分的子图（排除已完成节点）。

        断点续传时，从最后成功节点继续执行剩余子图。
        """
        pending = [n for n in self._graph.nodes if n not in completed]
        return self.subgraph(pending)
