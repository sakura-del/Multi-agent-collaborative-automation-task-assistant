"""调度器闭环集成测试：拆解→执行→校验完整流程。

验收标准：调度器与四大 Agent 可完成"拆解→执行→校验"基本闭环。
此测试纳入 CI，确保核心流程不回归。
"""

import pytest

from app.orchestration.agents.executor import ExecutorAgent
from app.orchestration.agents.planner import PlannerAgent
from app.orchestration.agents.validator import ValidatorAgent
from app.orchestration.dag_engine import TaskDAG
from app.orchestration.recovery import RecoveryManager
from app.orchestration.scheduler import TaskScheduler


def _agent_factory(agent_type: str):
    """测试用 Agent 工厂：无 LLM 降级模式，无需 API Key。

    ExecutorAgent 无 LLM 时降级输出占位结果，
    ValidatorAgent 无 LLM 时降级为默认 80 分语义评分，
    两者配合可完成闭环（规则宽松确保通过）。
    """
    if agent_type == "executor":
        return ExecutorAgent()
    if agent_type == "validator":
        return ValidatorAgent(rules={"min_length": 1, "required_keywords": []})
    raise ValueError(f"未知 agent_type: {agent_type}")


@pytest.mark.asyncio
class TestClosedLoopIntegration:
    """拆解→执行→校验闭环集成测试。"""

    async def test_full_closed_loop(self) -> None:
        """完整闭环：规划拆解 → 调度器并行执行 → 校验通过。

        流程：
        1. PlannerAgent 将需求拆解为子任务 DAG
        2. TaskScheduler 按拓扑序调度执行
        3. 每个节点：ExecutorAgent 执行 → ValidatorAgent 校验
        4. 全部节点完成，闭环成功
        """
        # 步骤1：规划 Agent 拆解需求（无 LLM 降级模式，产出合法三节点 TaskPlan）
        planner = PlannerAgent()
        plan_result = await planner.execute(
            {"requirement": "生成一份市场分析报告并校验质量", "trace_id": "integration-1"}
        )
        assert "dag" in plan_result
        assert "subtasks" in plan_result

        # 步骤2：直接用规划结果构建 DAG（不手工造节点/边）
        dag = TaskDAG.from_dict(plan_result["dag"])
        assert dag.node_count == 3
        assert dag.topological_order() == ["n1", "n2", "n3"]

        # 步骤3：调度器执行 DAG（执行→校验闭环）
        scheduler = TaskScheduler(agent_factory=_agent_factory)
        result = await scheduler.run(dag, task_id="integration-1")

        # 验收：全部节点完成
        assert result.success, f"闭环失败，已完成: {result.completed}"
        assert len(result.completed) == 3
        assert result.completion_rate == 1.0

        # 每个节点都有执行结果和校验结果
        for nid in ["n1", "n2", "n3"]:
            nr = result.node_results[nid]
            assert nr.output is not None  # 执行有输出
            assert nr.validation is not None  # 校验有结果
            assert nr.validation["passed"]  # 校验通过

    async def test_closed_loop_with_parallel_execution(self) -> None:
        """闭环中无依赖节点并行执行。"""
        # 菱形 DAG：n1→n2, n1→n3, n2→n4, n3→n4
        dag = TaskDAG()
        dag.add_node("n1", title="需求分析", description="分析需求")
        dag.add_node("n2", title="方案设计A", description="设计方案A")
        dag.add_node("n3", title="方案设计B", description="设计方案B")
        dag.add_node("n4", title="综合报告", description="综合输出")
        dag.add_edge("n1", "n2")
        dag.add_edge("n1", "n3")
        dag.add_edge("n2", "n4")
        dag.add_edge("n3", "n4")

        scheduler = TaskScheduler(agent_factory=_agent_factory)
        result = await scheduler.run(dag, task_id="parallel-1")
        assert result.success
        assert len(result.completed) == 4

    async def test_closed_loop_with_breakpoint_resume(self) -> None:
        """闭环 + 断点续传：部分完成后中断，恢复后继续。"""
        dag = TaskDAG()
        for i in range(1, 4):
            dag.add_node(f"n{i}", title=f"任务{i}", description=f"描述{i}")
        dag.add_edge("n1", "n2")
        dag.add_edge("n2", "n3")

        # 第一次调度：执行全部
        scheduler1 = TaskScheduler(agent_factory=_agent_factory)
        result1 = await scheduler1.run(dag, task_id="resume-1")
        assert result1.success

        # 创建断点快照（模拟 n1 完成后中断）
        snapshot = RecoveryManager.create_snapshot(
            task_id="resume-1",
            dag=dag,
            completed_nodes={"n1"},
        )

        # 恢复并继续执行（从 n2 开始）
        restored_dag, completed = RecoveryManager.restore(snapshot)
        scheduler2 = TaskScheduler(agent_factory=_agent_factory)
        result2 = await scheduler2.run(restored_dag, task_id="resume-1", pre_completed=completed)
        # n2, n3 执行完成
        assert "n2" in result2.completed
        assert "n3" in result2.completed
        assert result2.success

    async def test_closed_loop_node_count_matches(self) -> None:
        """闭环执行的节点数与 DAG 节点数一致。"""
        dag = TaskDAG()
        for i in range(1, 6):
            dag.add_node(f"n{i}", title=f"任务{i}", description=f"描述{i}")
        # n1→n2, n1→n3, n2→n4, n3→n5
        dag.add_edge("n1", "n2")
        dag.add_edge("n1", "n3")
        dag.add_edge("n2", "n4")
        dag.add_edge("n3", "n5")

        scheduler = TaskScheduler(agent_factory=_agent_factory)
        result = await scheduler.run(dag, task_id="count-1")
        assert result.total_nodes == 5
        assert len(result.completed) == 5
