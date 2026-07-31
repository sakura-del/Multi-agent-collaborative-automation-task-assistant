"""任务调度器单元测试：并行调度、优先级、闭环执行、失败重试。"""

import pytest

from app.orchestration.dag_engine import TaskDAG
from app.orchestration.scheduler import TaskScheduler


def _make_agent_factory():
    """创建 Agent 工厂：返回无 LLM 的降级 Agent（测试用）。

    ExecutorAgent 和 ValidatorAgent 在无 LLM 时均有降级路径，
    无需 MockLLM 即可完成闭环测试。
    """

    def factory(agent_type: str):
        from app.orchestration.agents.executor import ExecutorAgent
        from app.orchestration.agents.validator import ValidatorAgent

        if agent_type == "executor":
            return ExecutorAgent()
        if agent_type == "validator":
            # 宽松规则：最小长度 1，无必含关键词，确保降级输出能通过校验
            return ValidatorAgent(rules={"min_length": 1, "required_keywords": []})
        raise ValueError(f"未知 agent_type: {agent_type}")

    return factory


@pytest.mark.asyncio
class TestTaskSchedulerBasic:
    """调度器基础功能测试。"""

    async def test_run_empty_dag(self) -> None:
        """空 DAG 调度成功，结果为空。"""
        scheduler = TaskScheduler(agent_factory=_make_agent_factory())
        result = await scheduler.run(TaskDAG(), task_id="t1")
        assert result.total_nodes == 0
        assert result.success

    async def test_run_single_node(self) -> None:
        """单节点 DAG 完整执行（执行→校验→完成）。"""
        dag = TaskDAG()
        dag.add_node("n1", title="单任务", description="执行一个简单任务")
        scheduler = TaskScheduler(agent_factory=_make_agent_factory())
        result = await scheduler.run(dag, task_id="t1")
        assert result.success
        assert "n1" in result.completed
        assert result.completion_rate == 1.0

    async def test_run_sequential_chain(self) -> None:
        """顺序依赖链：n1→n2→n3，按拓扑序执行。"""
        dag = TaskDAG()
        for i in range(1, 4):
            dag.add_node(f"n{i}", title=f"任务{i}", description=f"描述{i}")
        dag.add_edge("n1", "n2")
        dag.add_edge("n2", "n3")
        scheduler = TaskScheduler(agent_factory=_make_agent_factory())
        result = await scheduler.run(dag, task_id="t1")
        assert result.success
        assert len(result.completed) == 3

    async def test_run_parallel_independent_nodes(self) -> None:
        """无依赖节点并行执行。"""
        dag = TaskDAG()
        dag.add_node("n1", title="并行任务1", description="描述1")
        dag.add_node("n2", title="并行任务2", description="描述2")
        dag.add_node("n3", title="并行任务3", description="描述3")
        scheduler = TaskScheduler(agent_factory=_make_agent_factory())
        result = await scheduler.run(dag, task_id="t1")
        assert result.success
        assert len(result.completed) == 3


@pytest.mark.asyncio
class TestTaskSchedulerFailure:
    """调度器失败与重试测试。"""

    async def test_node_failure_triggers_waiting_human(self) -> None:
        """节点执行失败且重试耗尽后转为人工干预。"""
        dag = TaskDAG()
        dag.add_node("n1", title="失败任务", description="必然失败")

        call_count = 0

        def failing_factory(agent_type: str):
            from app.orchestration.agents.validator import ValidatorAgent

            if agent_type == "executor":
                # 创建一个总是失败的 Agent
                from app.orchestration.base_agent import BaseAgent

                class _FailAgent(BaseAgent):
                    @property
                    def agent_type(self) -> str:
                        return "executor"

                    async def _run(self, input_data: dict) -> dict:
                        nonlocal call_count
                        call_count += 1
                        raise RuntimeError("模拟执行失败")

                return _FailAgent()
            return ValidatorAgent(rules={"min_length": 1, "required_keywords": []})

        scheduler = TaskScheduler(
            agent_factory=failing_factory,
            retry_policy=__import__(
                "app.orchestration.retry", fromlist=["RetryPolicy"]
            ).RetryPolicy(max_retries=1, backoff_times=[0.01]),
        )
        result = await scheduler.run(dag, task_id="t1")
        assert not result.success
        assert "n1" in result.waiting_human

    async def test_get_completed_nodes_after_run(self) -> None:
        """调度完成后可获取已完成节点集合。"""
        dag = TaskDAG()
        dag.add_node("n1", title="任务1", description="描述1")
        dag.add_node("n2", title="任务2", description="描述2")
        scheduler = TaskScheduler(agent_factory=_make_agent_factory())
        await scheduler.run(dag, task_id="t1")
        assert scheduler.get_completed_nodes() == {"n1", "n2"}

    async def test_get_state_snapshot(self) -> None:
        """状态快照包含所有节点的最终状态。"""
        dag = TaskDAG()
        dag.add_node("n1", title="任务1", description="描述1")
        scheduler = TaskScheduler(agent_factory=_make_agent_factory())
        await scheduler.run(dag, task_id="t1")
        snapshot = scheduler.get_state_snapshot()
        assert "n1" in snapshot
        assert snapshot["n1"] == "completed"


@pytest.mark.asyncio
class TestTaskSchedulerResume:
    """断点续传测试。"""

    async def test_resume_from_completed(self) -> None:
        """从已完成节点继续执行剩余子图。"""
        dag = TaskDAG()
        dag.add_node("n1", title="任务1", description="描述1")
        dag.add_node("n2", title="任务2", description="描述2")
        dag.add_edge("n1", "n2")
        scheduler = TaskScheduler(agent_factory=_make_agent_factory())
        # n1 已完成，仅执行 n2
        result = await scheduler.run(dag, task_id="t1", pre_completed={"n1"})
        assert "n1" not in result.completed  # n1 跳过
        assert "n2" in result.completed  # n2 执行完成
        assert result.success

    async def test_diamond_dependency(self) -> None:
        """菱形依赖：n1→n2, n1→n3, n2→n4, n3→n4。"""
        dag = TaskDAG()
        for i in range(1, 5):
            dag.add_node(f"n{i}", title=f"任务{i}", description=f"描述{i}")
        dag.add_edge("n1", "n2")
        dag.add_edge("n1", "n3")
        dag.add_edge("n2", "n4")
        dag.add_edge("n3", "n4")
        scheduler = TaskScheduler(agent_factory=_make_agent_factory())
        result = await scheduler.run(dag, task_id="t1")
        assert result.success
        assert len(result.completed) == 4
