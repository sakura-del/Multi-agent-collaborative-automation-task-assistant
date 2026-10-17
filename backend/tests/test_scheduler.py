"""任务调度器单元测试：并行调度、优先级、闭环执行、失败重试、观察者持久化。"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.db.models  # noqa: F401  确保全部 ORM 注册到 Base.metadata
from app.db.base import Base
from app.db.models.task import Subtask, Task
from app.orchestration.base_agent import BaseAgent
from app.orchestration.dag_engine import TaskDAG
from app.orchestration.persistence import DatabaseScheduleObserver
from app.orchestration.retry import RetryPolicy
from app.orchestration.scheduler import NodeResult, TaskScheduler
from app.orchestration.task_state import SubtaskStatus


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


def _capturing_agent_factory(captured: list[dict]):
    """创建会记录 input_data 的 Agent 工厂，用于断言节点执行上下文。"""

    def factory(agent_type: str):
        if agent_type == "executor":

            class _CaptureAgent(BaseAgent):
                @property
                def agent_type(self) -> str:
                    return "executor"

                async def _run(self, input_data: dict) -> dict:
                    captured.append(dict(input_data))
                    return {"result": f"done-{input_data['node_id']}"}

            return _CaptureAgent()

        from app.orchestration.agents.validator import ValidatorAgent

        return ValidatorAgent(rules={"min_length": 1, "required_keywords": []})

    return factory


class _RecordingObserver:
    """内存观察者：记录全部转换与结果，供顺序断言。"""

    def __init__(self) -> None:
        self.transitions: list[tuple[str, str, str, str]] = []
        self.results: list[NodeResult] = []

    async def on_transition(
        self,
        task_id: str,
        node_id: str,
        old: SubtaskStatus,
        new: SubtaskStatus,
    ) -> None:
        self.transitions.append((task_id, node_id, old.value, new.value))

    async def on_result(self, task_id: str, result: NodeResult) -> None:
        self.results.append(result)


class _RecordingPublisher:
    """内存事件发布器：记录事件，可模拟发布失败。"""

    def __init__(self, fail: bool = False) -> None:
        self.events: list = []
        self.fail = fail

    async def publish(self, event) -> None:
        if self.fail:
            raise RuntimeError("模拟发布失败")
        self.events.append(event)


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
            retry_policy=RetryPolicy(max_retries=1, backoff_times=[0.01]),
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


@pytest.mark.asyncio
class TestSchedulerObserver:
    """观察者测试：状态转换顺序、结果唯一性、节点执行上下文。"""

    async def test_observer_receives_ordered_transitions_and_single_result(self) -> None:
        """单节点成功：observer 按 READY→RUNNING→VALIDATING→COMPLETED 顺序收到转换。"""
        observer = _RecordingObserver()
        dag = TaskDAG()
        dag.add_node("n1", title="任务", description="描述")
        scheduler = TaskScheduler(agent_factory=_make_agent_factory(), observer=observer)

        result = await scheduler.run(dag, task_id="t1")

        assert result.success
        assert [transition[3] for transition in observer.transitions] == [
            "ready",
            "running",
            "validating",
            "completed",
        ]
        assert all(transition[0] == "t1" for transition in observer.transitions)
        assert all(transition[1] == "n1" for transition in observer.transitions)
        assert len(observer.results) == 1
        assert observer.results[0].status is SubtaskStatus.COMPLETED

    async def test_observer_receives_retry_sequence(self) -> None:
        """重试路径：observer 收到完整顺序，且最终结果只通知一次。"""
        attempts = {"value": 0}

        def flaky_factory(agent_type: str):
            if agent_type == "executor":

                class _FlakyAgent(BaseAgent):
                    @property
                    def agent_type(self) -> str:
                        return "executor"

                    async def _run(self, input_data: dict) -> dict:
                        attempts["value"] += 1
                        if attempts["value"] == 1:
                            raise RuntimeError("首次失败")
                        return {"result": "ok"}

                return _FlakyAgent()

            from app.orchestration.agents.validator import ValidatorAgent

            return ValidatorAgent(rules={"min_length": 1, "required_keywords": []})

        observer = _RecordingObserver()
        scheduler = TaskScheduler(
            agent_factory=flaky_factory,
            retry_policy=RetryPolicy(max_retries=1, backoff_times=[0.01]),
            observer=observer,
        )
        dag = TaskDAG()
        dag.add_node("n1", title="重试任务", description="首次失败后成功")

        result = await scheduler.run(dag, task_id="t1")

        assert result.success
        assert [transition[3] for transition in observer.transitions] == [
            "ready",
            "running",
            "failed",
            "retrying",
            "ready",
            "running",
            "validating",
            "completed",
        ]
        assert len(observer.results) == 1

    async def test_executor_receives_task_context_and_dependency_outputs(self) -> None:
        """节点执行上下文携带 task_id、trace_id、subtask_id 与依赖节点输出。"""
        captured: list[dict] = []
        dag = TaskDAG()
        dag.add_node("n1", title="任务1", description="描述1")
        dag.add_node("n2", title="任务2", description="描述2")
        dag.add_edge("n1", "n2")
        subtask_ids = {"n1": str(uuid.uuid4()), "n2": str(uuid.uuid4())}

        scheduler = TaskScheduler(agent_factory=_capturing_agent_factory(captured))
        result = await scheduler.run(
            dag,
            task_id="task-1",
            trace_id="trace-1",
            subtask_ids=subtask_ids,
        )

        assert result.success
        by_node = {item["node_id"]: item for item in captured}
        assert by_node["n1"]["task_id"] == "task-1"
        assert by_node["n1"]["trace_id"] == "trace-1"
        assert by_node["n1"]["subtask_id"] == subtask_ids["n1"]
        assert by_node["n2"]["dependency_outputs"]["n1"]["result"] == "done-n1"


@pytest.mark.asyncio
class TestDatabaseScheduleObserver:
    """数据库观察者测试：持久化、事件发布与失败隔离。"""

    async def _setup(self):
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
        )
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        task_id = uuid.uuid4()
        subtask_id = uuid.uuid4()
        async with session_factory() as session:
            session.add(
                Task(
                    id=task_id,
                    user_id=uuid.uuid4(),
                    title="观察者任务",
                    description="持久化测试",
                    status="running",
                )
            )
            session.add(
                Subtask(
                    id=subtask_id,
                    task_id=task_id,
                    node_id="n1",
                    title="节点",
                    description="描述",
                    status="pending",
                )
            )
            await session.commit()
        return engine, session_factory, task_id, subtask_id

    async def test_transition_and_result_are_persisted_and_published(self) -> None:
        """状态转换与最终结果写入 SUBTASK，并各自发布一条事件。"""
        engine, session_factory, task_id, subtask_id = await self._setup()
        publisher = _RecordingPublisher()
        observer = DatabaseScheduleObserver(session_factory=session_factory, publisher=publisher)
        try:
            await observer.on_transition(
                str(task_id), "n1", SubtaskStatus.PENDING, SubtaskStatus.READY
            )
            async with session_factory() as session:
                subtask = await session.get(Subtask, subtask_id)
                assert subtask is not None
                assert subtask.status == "ready"
            assert [event.event_type for event in publisher.events] == ["node_status"]
            assert publisher.events[0].event_id == 1

            await observer.on_result(
                str(task_id),
                NodeResult(
                    node_id="n1",
                    status=SubtaskStatus.COMPLETED,
                    output={"result": "ok"},
                    validation={"passed": True},
                    attempts=2,
                ),
            )
            async with session_factory() as session:
                subtask = await session.get(Subtask, subtask_id)
                assert subtask is not None
                assert subtask.status == "completed"
                assert subtask.retry_count == 1
                assert subtask.output_data is not None
                assert subtask.output_data["output"] == {"result": "ok"}
                assert subtask.completed_at is not None
            assert [event.event_id for event in publisher.events] == [1, 2]
        finally:
            await engine.dispose()

    async def test_invalid_task_id_is_ignored(self) -> None:
        """非法 task_id 不抛异常、不产生事件。"""
        publisher = _RecordingPublisher()
        observer = DatabaseScheduleObserver(publisher=publisher)
        await observer.on_transition("not-a-uuid", "n1", SubtaskStatus.PENDING, SubtaskStatus.READY)
        assert publisher.events == []

    async def test_commit_failure_does_not_publish(self) -> None:
        """数据库失败时不发布事件、不向调度器抛异常。"""

        def broken_factory():
            raise RuntimeError("数据库不可用")

        publisher = _RecordingPublisher()
        observer = DatabaseScheduleObserver(session_factory=broken_factory, publisher=publisher)
        await observer.on_transition(
            str(uuid.uuid4()), "n1", SubtaskStatus.PENDING, SubtaskStatus.READY
        )
        assert publisher.events == []

    async def test_publish_failure_keeps_committed_transition(self) -> None:
        """发布失败不影响已提交的数据库状态，也不抛异常。"""
        engine, session_factory, task_id, subtask_id = await self._setup()
        observer = DatabaseScheduleObserver(
            session_factory=session_factory,
            publisher=_RecordingPublisher(fail=True),
        )
        try:
            await observer.on_transition(
                str(task_id), "n1", SubtaskStatus.PENDING, SubtaskStatus.READY
            )
            async with session_factory() as session:
                subtask = await session.get(Subtask, subtask_id)
                assert subtask is not None
                assert subtask.status == "ready"
        finally:
            await engine.dispose()


class _RegeneratingExecutor(BaseAgent):
    """记录每次调用；带 validation_feedback 时返回修订后的正文。"""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict] = []

    @property
    def agent_type(self) -> str:
        return "executor"

    async def _run(self, input_data: dict) -> dict:
        self.calls.append(dict(input_data))
        suffix = "revised" if input_data.get("validation_feedback") else "draft"
        return {"result": f"{input_data['node_id']}-{suffix}"}


class _RegeneratingValidator(BaseAgent):
    """仅对目标节点先返回一次可重试失败，之后通过；记录收到的 feedback。"""

    def __init__(self, *, target_node: str = "n2", always_fail: bool = False) -> None:
        super().__init__()
        self.calls = 0
        self.feedback_seen: list[str] = []
        self._target = target_node
        self._always_fail = always_fail

    @property
    def agent_type(self) -> str:
        return "validator"

    async def _run(self, input_data: dict) -> dict:
        if input_data.get("node_id") != self._target:
            return {
                "passed": True,
                "score": 95.0,
                "checks": [],
                "retryable": False,
                "feedback": "",
            }
        self.calls += 1
        self.feedback_seen.append(str(input_data.get("validation_feedback", "")))
        if self._always_fail or self.calls == 1:
            return {
                "passed": False,
                "score": 40.0,
                "checks": [],
                "retryable": True,
                "feedback": "缺少引用标记",
            }
        return {"passed": True, "score": 95.0, "checks": [], "retryable": False, "feedback": ""}


@pytest.mark.asyncio
class TestTargetedRegeneration:
    """Plan 3 Task 4：校验可重试失败只重做当前节点，最多两次。"""

    @staticmethod
    def _dag() -> TaskDAG:
        dag = TaskDAG()
        dag.add_node("n1", title="检索", description="search")
        dag.add_node("n2", title="撰写", description="write")
        dag.add_edge("n1", "n2")
        return dag

    def _factory(self, executor: _RegeneratingExecutor, validator: _RegeneratingValidator):
        def factory(agent_type: str):
            return executor if agent_type == "executor" else validator

        return factory

    async def test_retryable_validation_regenerates_only_failed_node(self) -> None:
        executor = _RegeneratingExecutor()
        validator = _RegeneratingValidator()
        scheduler = TaskScheduler(agent_factory=self._factory(executor, validator))

        result = await scheduler.run(self._dag(), task_id=str(uuid.uuid4()))

        assert result.success
        assert result.node_results["n2"].attempts == 2
        assert result.node_results["n1"].attempts == 1
        n2_calls = [call for call in executor.calls if call["node_id"] == "n2"]
        assert len(n2_calls) == 2
        assert n2_calls[0].get("validation_feedback") in (None, "")
        assert n2_calls[1]["validation_feedback"] == "缺少引用标记"
        assert n2_calls[1]["regeneration"] == 1
        assert executor.calls[0]["node_id"] == "n1"

    async def test_regeneration_exhaustion_moves_node_to_waiting_human(self) -> None:
        executor = _RegeneratingExecutor()
        validator = _RegeneratingValidator(always_fail=True)
        scheduler = TaskScheduler(
            agent_factory=self._factory(executor, validator), max_regenerations=2
        )

        result = await scheduler.run(self._dag(), task_id=str(uuid.uuid4()))

        assert not result.success
        n2_calls = [call for call in executor.calls if call["node_id"] == "n2"]
        assert len(n2_calls) == 3  # 首次 + 两次重生成
        assert result.node_results["n2"].status is SubtaskStatus.WAITING_HUMAN
        assert result.node_results["n2"].attempts == 3

    async def test_non_retryable_validation_skips_regeneration(self) -> None:
        executor = _RegeneratingExecutor()

        class _IntegrityValidator(BaseAgent):
            @property
            def agent_type(self) -> str:
                return "validator"

            async def _run(self, input_data: dict) -> dict:
                if input_data.get("node_id") != "n2":
                    return {
                        "passed": True,
                        "score": 95.0,
                        "checks": [],
                        "retryable": False,
                        "feedback": "",
                    }
                return {
                    "passed": False,
                    "score": 10.0,
                    "checks": [],
                    "retryable": False,
                    "feedback": "证据归属校验失败",
                }

        scheduler = TaskScheduler(agent_factory=self._factory(executor, _IntegrityValidator()))
        result = await scheduler.run(self._dag(), task_id=str(uuid.uuid4()))

        assert not result.success
        n2_calls = [call for call in executor.calls if call["node_id"] == "n2"]
        assert len(n2_calls) == 1  # 不可重试：不重生成
        assert result.node_results["n2"].status is SubtaskStatus.WAITING_HUMAN
