"""配额结算：未完成任务不再白扣预扣额度（2026-10-21）。

## 为什么需要

提交任务时按 `ESTIMATED_TOKENS_PER_TASK`（100k）**预扣**，任务**完成**时按实际用量
退还差额。但退款只在 `_finish_reviewing` 里调用 → **取消 / 失败 / 转人工 / 放弃确认**
的任务，预扣的 100k 永久占用。

真实故障（2026-10-21 Task 7 验收）：demo 账号 6 个停在 `waiting_approval` 的探针任务
占满 100 万日配额，之后所有建任务请求返回 429 `QUOTA_EXCEEDED`。

## 本文件锁定

1. **用量落库**：`task.used_tokens` 由各节点结果累加，是结算依据
   （内存里的 `ExecutionResult` 进程重启/任务中止后就没了）；
2. **结算幂等**：`quota_settled` 标记 + 同事务更新，重复调用**不重复退款**
   （否则额度能被刷成负成本）；
3. **四条终止路径都结算**：CANCELLED / FAILED / WAITING_HUMAN / WAITING_APPROVAL；
4. **继续执行要重新预扣**：`WAITING_APPROVAL` 确认执行、`WAITING_HUMAN` 重试后继续，
   都必须重新扣 `ESTIMATED - used_tokens`，否则「先结算、继续跑」等于免费额度。
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.db.models  # noqa: F401  确保全部 ORM 注册到 Base.metadata
from app.db.base import Base
from app.db.models.task import Task
from app.orchestration.contracts import TaskStatus
from app.orchestration.services.task_execution import (
    ESTIMATED_TOKENS_PER_TASK,
    TaskExecution,
)

# ===== 测试替身 =====


class _FakeQuota:
    """记录扣减与退还；可模拟「额度不足」与「组件故障」。"""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.refunds: list[tuple[Any, int]] = []
        self.consumes: list[tuple[Any, int]] = []

    async def refund(self, user_id: Any, *, tokens: int) -> None:
        self.refunds.append((user_id, tokens))
        if self.error is not None:
            raise self.error

    async def check_and_consume(self, user_id: Any, *, tokens: int) -> None:
        self.consumes.append((user_id, tokens))
        if self.error is not None:
            raise self.error

    @property
    def total_refunded(self) -> int:
        return sum(tokens for _, tokens in self.refunds)


async def _setup_engine(tmp_path: Path) -> tuple[Any, async_sessionmaker[AsyncSession]]:
    db_path = tmp_path / "quota-settlement.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path.as_posix()}",
        connect_args={"timeout": 10},
    )
    async with engine.begin() as connection:
        await connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, factory


async def _seed_task(
    factory: async_sessionmaker[AsyncSession],
    *,
    status: str = TaskStatus.RUNNING.value,
    used_tokens: int = 0,
    quota_settled: bool = False,
) -> tuple[uuid.UUID, uuid.UUID]:
    """插入一条任务，返回 (task_id, user_id)。"""
    user_id = uuid.uuid4()
    task_id = uuid.uuid4()
    async with factory() as session:
        session.add(
            Task(
                id=task_id,
                user_id=user_id,
                title="配额结算测试任务",
                description="d",
                status=status,
                used_tokens=used_tokens,
                quota_settled=quota_settled,
            )
        )
        await session.commit()
    return task_id, user_id


def _make_execution(factory: async_sessionmaker[AsyncSession], quota: Any) -> TaskExecution:
    from app.orchestration.runtime import RuntimeContainer

    container = RuntimeContainer(session_factory=factory)
    return TaskExecution(container, quota_service=quota)


# ===== 模型层 =====


class TestTaskModelFields:
    async def test_used_tokens_defaults_to_zero(self, tmp_path: Path) -> None:
        """未显式赋值时落库为 0（`default=` 在 INSERT 时生效）。"""
        engine, factory = await _setup_engine(tmp_path)
        try:
            async with factory() as session:
                task = Task(
                    user_id=uuid.uuid4(),
                    title="t",
                    description="d",
                    status=TaskStatus.RUNNING.value,
                )
                session.add(task)
                await session.commit()
                assert task.used_tokens == 0
        finally:
            await engine.dispose()

    async def test_quota_settled_defaults_to_false(self, tmp_path: Path) -> None:
        engine, factory = await _setup_engine(tmp_path)
        try:
            async with factory() as session:
                task = Task(
                    user_id=uuid.uuid4(),
                    title="t",
                    description="d",
                    status=TaskStatus.RUNNING.value,
                )
                session.add(task)
                await session.commit()
                assert task.quota_settled is False
        finally:
            await engine.dispose()


# ===== 结算：基本语义 =====


@pytest.mark.asyncio
class TestSettleQuota:
    async def test_refunds_difference_between_estimate_and_used(self, tmp_path: Path) -> None:
        engine, factory = await _setup_engine(tmp_path)
        quota = _FakeQuota()
        try:
            task_id, user_id = await _seed_task(factory, used_tokens=40_000)
            execution = _make_execution(factory, quota)

            await execution.settle_quota(task_id)

            assert quota.refunds == [(user_id, ESTIMATED_TOKENS_PER_TASK - 40_000)]
        finally:
            await engine.dispose()

    async def test_full_refund_when_nothing_consumed(self, tmp_path: Path) -> None:
        """规划后直接取消（用量 0）→ 全额退还 100k。"""
        engine, factory = await _setup_engine(tmp_path)
        quota = _FakeQuota()
        try:
            task_id, user_id = await _seed_task(factory, used_tokens=0)
            execution = _make_execution(factory, quota)

            await execution.settle_quota(task_id)

            assert quota.refunds == [(user_id, ESTIMATED_TOKENS_PER_TASK)]
        finally:
            await engine.dispose()

    async def test_no_refund_when_used_exceeds_estimate(self, tmp_path: Path) -> None:
        """实际超过预扣 → 不退还也不补扣（与既有语义一致）。"""
        engine, factory = await _setup_engine(tmp_path)
        quota = _FakeQuota()
        try:
            task_id, _ = await _seed_task(factory, used_tokens=150_000)
            execution = _make_execution(factory, quota)

            await execution.settle_quota(task_id)

            assert quota.refunds == []
        finally:
            await engine.dispose()

    async def test_settlement_is_idempotent(self, tmp_path: Path) -> None:
        """**幂等**：结算两次只退一次（否则额度能被刷成正收益）。"""
        engine, factory = await _setup_engine(tmp_path)
        quota = _FakeQuota()
        try:
            task_id, _ = await _seed_task(factory, used_tokens=10_000)
            execution = _make_execution(factory, quota)

            await execution.settle_quota(task_id)
            await execution.settle_quota(task_id)
            await execution.settle_quota(task_id)

            assert len(quota.refunds) == 1
        finally:
            await engine.dispose()

    async def test_marks_task_as_settled(self, tmp_path: Path) -> None:
        engine, factory = await _setup_engine(tmp_path)
        quota = _FakeQuota()
        try:
            task_id, _ = await _seed_task(factory, used_tokens=10_000)
            execution = _make_execution(factory, quota)

            await execution.settle_quota(task_id)

            async with factory() as session:
                task = await session.get(Task, task_id)
                assert task is not None
                assert task.quota_settled is True
        finally:
            await engine.dispose()

    async def test_settled_flag_written_even_when_quota_component_fails(
        self, tmp_path: Path
    ) -> None:
        """配额组件故障（fail-open）：标记仍落地，绝不因外部故障让任务失败。

        为什么标记要先落库：Redis 退款失败时若 DB 没标记，下次还会再试；
        而「再试」意味着可能重复退（Redis 那次其实成功了）。宁可少退不可多退。
        """
        engine, factory = await _setup_engine(tmp_path)
        quota = _FakeQuota(error=ConnectionError("redis down"))
        try:
            task_id, _ = await _seed_task(factory, used_tokens=10_000)
            execution = _make_execution(factory, quota)

            await execution.settle_quota(task_id)  # 不抛异常

            async with factory() as session:
                task = await session.get(Task, task_id)
                assert task is not None
                assert task.quota_settled is True
        finally:
            await engine.dispose()

    async def test_quota_none_is_noop_but_marks_settled(self, tmp_path: Path) -> None:
        """配额关闭（None）：不调用退款，但仍标记已结算（避免后续重复走结算路径）。"""
        engine, factory = await _setup_engine(tmp_path)
        try:
            task_id, _ = await _seed_task(factory, used_tokens=10_000)
            execution = _make_execution(factory, None)

            await execution.settle_quota(task_id)

            async with factory() as session:
                task = await session.get(Task, task_id)
                assert task is not None
                assert task.quota_settled is True
        finally:
            await engine.dispose()

    async def test_missing_task_is_noop(self, tmp_path: Path) -> None:
        engine, factory = await _setup_engine(tmp_path)
        quota = _FakeQuota()
        try:
            execution = _make_execution(factory, quota)

            await execution.settle_quota(uuid.uuid4())

            assert quota.refunds == []
        finally:
            await engine.dispose()


# ===== 用量累加 =====


@pytest.mark.asyncio
class TestUsedTokensAccumulation:
    async def test_accumulates_incrementally(self, tmp_path: Path) -> None:
        """累加而非覆盖：多次执行（续跑/重试）的用量必须相加。"""
        engine, factory = await _setup_engine(tmp_path)
        try:
            task_id, _ = await _seed_task(factory, used_tokens=0)
            execution = _make_execution(factory, _FakeQuota())

            await execution.add_used_tokens(task_id, 1_000)
            await execution.add_used_tokens(task_id, 2_500)

            async with factory() as session:
                task = await session.get(Task, task_id)
                assert task is not None
                assert task.used_tokens == 3_500
        finally:
            await engine.dispose()

    async def test_ignores_non_positive_amounts(self, tmp_path: Path) -> None:
        engine, factory = await _setup_engine(tmp_path)
        try:
            task_id, _ = await _seed_task(factory, used_tokens=100)
            execution = _make_execution(factory, _FakeQuota())

            await execution.add_used_tokens(task_id, 0)
            await execution.add_used_tokens(task_id, -50)

            async with factory() as session:
                task = await session.get(Task, task_id)
                assert task is not None
                assert task.used_tokens == 100
        finally:
            await engine.dispose()


# ===== 生命周期集成：四条终止路径都要结算 =====


def _plan_payload() -> dict[str, Any]:
    """文献综述工作流要求五类节点齐全（search/extract/write/validate/review）。

    节点数尽量少，但类型必须覆盖——否则 `literature_review` 的规划契约校验会拒绝
    （`HumanActionRequiredError: 缺少节点类型`），测试就测不到配额逻辑了。
    """
    return {
        "version": 1,
        "topic": "配额结算生命周期测试",
        "research_questions": ["Q1", "Q2", "Q3"],
        "search_queries": ["quota settlement"],
        "acceptance_criteria": ["引用可回溯"],
        "nodes": [
            {"id": "n1", "type": "search", "title": "检索", "dependencies": []},
            {"id": "n2", "type": "extract", "title": "提取", "dependencies": ["n1"]},
            {"id": "n3", "type": "write", "title": "撰写", "dependencies": ["n2"]},
            {"id": "n4", "type": "validate", "title": "校验", "dependencies": ["n3"]},
            {"id": "n5", "type": "review", "title": "复盘", "dependencies": ["n4"]},
        ],
    }


def _build_agents() -> tuple[Any, dict[str, Any]]:
    """构造确定性 planner / executor / validator / reviewer。"""
    from app.orchestration.agents.planner import PlannerAgent
    from app.orchestration.base_agent import BaseAgent
    from app.orchestration.contracts import TaskPlan

    class _Planner(BaseAgent):
        @property
        def agent_type(self) -> str:
            return "planner"

        async def _run(self, input_data: dict[str, Any]) -> dict[str, Any]:
            plan = TaskPlan.model_validate(_plan_payload())
            return {
                "plan": plan.model_dump(mode="json"),
                "dag": PlannerAgent.build_dag(plan).to_dict(),
            }

    class _Executor(BaseAgent):
        @property
        def agent_type(self) -> str:
            return "executor"

        async def _run(self, input_data: dict[str, Any]) -> dict[str, Any]:
            # 每个节点报告 1000 prompt + 500 completion token
            return {"result": "ok", "prompt_tokens": 1000, "completion_tokens": 500}

    class _Validator(BaseAgent):
        @property
        def agent_type(self) -> str:
            return "validator"

        async def _run(self, input_data: dict[str, Any]) -> dict[str, Any]:
            return {
                "passed": True,
                "score": 90.0,
                "checks": [],
                "retryable": False,
                "feedback": "",
            }

    class _Reviewer(BaseAgent):
        @property
        def agent_type(self) -> str:
            return "reviewer"

        async def _run(self, input_data: dict[str, Any]) -> dict[str, Any]:
            return {
                "trace_id": input_data.get("trace_id", ""),
                "summary": "完成",
                "stats": {"total_logs": 0, "error_count": 0},
                "root_cause": {},
            }

    agents = {
        "executor": _Executor(),
        "validator": _Validator(),
        "reviewer": _Reviewer(),
    }
    return _Planner(), agents


def _make_lifecycle_execution(
    factory: async_sessionmaker[AsyncSession], quota: Any
) -> TaskExecution:
    from app.orchestration.runtime import RuntimeContainer

    planner, agents = _build_agents()
    container = RuntimeContainer(
        session_factory=factory,
        planner_factory=lambda: planner,
        agent_factory=lambda agent_type: agents[agent_type],
    )
    return TaskExecution(container, quota_service=quota)


async def _wait_status(
    execution: TaskExecution, user_id: uuid.UUID, task_id: uuid.UUID, expected: set[str]
) -> Any:
    import asyncio

    deadline = asyncio.get_running_loop().time() + 10.0
    last = None
    while asyncio.get_running_loop().time() < deadline:
        snapshot = await execution.get_snapshot(user_id, task_id)
        last = snapshot.status
        if last in expected:
            return snapshot
        await asyncio.sleep(0.02)
    raise AssertionError(f"等待状态 {expected} 超时，当前 {last}")


async def _wait_settled(
    factory: async_sessionmaker[AsyncSession], task_id: uuid.UUID, *, timeout: float = 20.0
) -> Task:
    """等待任务「配额已结算」。

    **为什么单独等结算而不是等状态**：状态（completed/cancelled/…）与结算
    (`settle_quota`) 是**两步**事务——状态先提交，结算随后。全量门禁在负载高时
    (CPU 竞争) 会出现「状态已 completed，但结算还没跑完」的中间态，
    只断言状态就会偶发失败（2026-10-21 全量跑踩到；单独跑却通过，属于典型
    「时序敏感测试」）。这里显式等结算落库，测试就与机器负载无关了。
    """
    import asyncio

    deadline = asyncio.get_running_loop().time() + timeout
    last: Task | None = None
    while asyncio.get_running_loop().time() < deadline:
        async with factory() as session:
            task = await session.get(Task, task_id)
            if task is not None:
                last = task
                if task.quota_settled:
                    return task
        await asyncio.sleep(0.05)
    raise AssertionError(
        f"等待配额结算超时（quota_settled={getattr(last, 'quota_settled', None)}，"
        f"used_tokens={getattr(last, 'used_tokens', None)}）"
    )


def _request() -> Any:
    from app.orchestration.contracts import LiteratureReviewRequest

    return LiteratureReviewRequest(topic="配额结算测试", keywords=["quota"], max_documents=2)


@pytest.mark.asyncio
class TestSettlementOnLifecyclePaths:
    async def test_cancel_before_approval_refunds_full_reservation(self, tmp_path: Path) -> None:
        """规划后取消（用量 0）→ 全额退还 100k。

        这正是 2026-10-21 实测的事故路径：任务停在 WAITING_APPROVAL 永不继续，
        预扣永久占用，最终把日配额占满。
        """
        from app.orchestration.contracts import TaskCommand, TaskControlRequest, TaskStatus

        engine, factory = await _setup_engine(tmp_path)
        quota = _FakeQuota()
        execution = _make_lifecycle_execution(factory, quota)
        user_id = uuid.uuid4()
        try:
            task_id = await execution.submit(user_id, _request())
            await _wait_status(execution, user_id, task_id, {TaskStatus.WAITING_APPROVAL.value})
            # 等规划阶段的结算落库，避免与取消路径的结算抢时序（并发负载下会读到 0）
            await _wait_settled(factory, task_id)

            await execution.control(
                user_id, task_id, TaskControlRequest(command=TaskCommand.CANCEL)
            )

            # 取消后总额度必须归还（规划阶段已结算 + 取消幂等跳过，合计恰好 100k）
            assert quota.total_refunded == ESTIMATED_TOKENS_PER_TASK
        finally:
            await execution.container.close()
            await engine.dispose()

    async def test_cancel_while_planning_refunds_reservation(self, tmp_path: Path) -> None:
        """**规划中**取消（尚未结算）→ 取消路径自己必须退还。

        为什么单独测这条：`WAITING_APPROVAL` 到达时已结算，取消时的结算会被幂等
        跳过——那种用例在「取消路径完全不结算」时**仍然通过**，是假测试
        （AGENTS.md #13 实测踩到）。只有停在 PLANNING 取消，才能真正验证
        `_control_cancel` 的结算。
        """
        import asyncio

        from app.orchestration.base_agent import BaseAgent
        from app.orchestration.contracts import TaskCommand, TaskControlRequest, TaskStatus
        from app.orchestration.runtime import RuntimeContainer

        engine, factory = await _setup_engine(tmp_path)
        quota = _FakeQuota()
        gate = asyncio.Event()

        class _BlockedPlanner(BaseAgent):
            @property
            def agent_type(self) -> str:
                return "planner"

            async def _run(self, input_data: dict[str, Any]) -> dict[str, Any]:
                await gate.wait()  # 永远停在规划中
                raise AssertionError("不应到达")

        container = RuntimeContainer(
            session_factory=factory,
            planner_factory=lambda: _BlockedPlanner(),
            agent_factory=lambda _t: None,
        )
        execution = TaskExecution(container, quota_service=quota)
        user_id = uuid.uuid4()
        try:
            task_id = await execution.submit(user_id, _request())
            # 确认仍处于 PLANNING（未结算）
            await _wait_status(execution, user_id, task_id, {TaskStatus.PLANNING.value})

            await execution.control(
                user_id, task_id, TaskControlRequest(command=TaskCommand.CANCEL)
            )

            assert quota.total_refunded == ESTIMATED_TOKENS_PER_TASK
        finally:
            gate.set()
            await container.close()
            await engine.dispose()

    async def test_approval_after_settlement_prereserves_again(self, tmp_path: Path) -> None:
        """确认执行时重新预扣（否则「先结算再跑完」等于免费）。"""
        from app.orchestration.contracts import TaskStatus

        engine, factory = await _setup_engine(tmp_path)
        quota = _FakeQuota()
        execution = _make_lifecycle_execution(factory, quota)
        user_id = uuid.uuid4()
        try:
            task_id = await execution.submit(user_id, _request())
            waiting = await _wait_status(
                execution, user_id, task_id, {TaskStatus.WAITING_APPROVAL.value}
            )
            # 状态到 waiting_approval 不代表结算已落库（结算是状态提交后的第二步），
            # 必须先等结算，否则读到的 total_refunded 可能还是 0（并发负载下必现）。
            await _wait_settled(factory, task_id)
            refunded_on_planning = quota.total_refunded

            await execution.approve_plan(user_id, task_id, waiting.revision)

            # 规划后已结算；确认执行必须重新扣回同样的额度
            assert refunded_on_planning == ESTIMATED_TOKENS_PER_TASK
            assert quota.consumes, "确认执行时应重新预扣"
            assert sum(tokens for _, tokens in quota.consumes) == ESTIMATED_TOKENS_PER_TASK

            # 跑完后按真实用量再退差额（且只退一次）。
            # 用量不写死：各节点类型（search/extract/write/validate/review）走不同 Agent，
            # 断言「结算额 == 预扣 − 落库用量」这个恒等式比硬编码数字更抗改动。
            await _wait_status(execution, user_id, task_id, {TaskStatus.COMPLETED.value})
            row = await _wait_settled(factory, task_id)
            used_after = int(row.used_tokens)
            assert used_after > 0, "完成的任务必须落库真实用量（否则无法结算）"
            final_refund = quota.total_refunded - refunded_on_planning
            assert final_refund == ESTIMATED_TOKENS_PER_TASK - used_after
        finally:
            await execution.container.close()
            await engine.dispose()

    async def test_completion_settles_only_once(self, tmp_path: Path) -> None:
        """完成路径：用量落库 + 结算一次（`used_tokens` 不被重复累加）。"""
        from app.orchestration.contracts import TaskStatus

        engine, factory = await _setup_engine(tmp_path)
        quota = _FakeQuota()
        execution = _make_lifecycle_execution(factory, quota)
        user_id = uuid.uuid4()
        try:
            task_id = await execution.submit(user_id, _request())
            waiting = await _wait_status(
                execution, user_id, task_id, {TaskStatus.WAITING_APPROVAL.value}
            )
            await execution.approve_plan(user_id, task_id, waiting.revision)
            await _wait_status(execution, user_id, task_id, {TaskStatus.COMPLETED.value})

            # 等结算落库（状态与结算是两步事务，只等状态会偶发失败）
            task = await _wait_settled(factory, task_id)
            # 5 个节点各报告 1000+500；不写死总数，断言「有量且已结算」
            assert task.used_tokens > 0
            assert task.quota_settled is True
        finally:
            await execution.container.close()
            await engine.dispose()

    async def test_execution_failure_settles_by_actual_usage(self, tmp_path: Path) -> None:
        """执行期节点失败转人工：按**已用**额度结算，不是全额退。

        与「规划失败」的区别：节点已经跑了一部分，`used_tokens > 0`。
        若把它当成 0 用量全额退款，用户就能靠「跑一半再让它失败」白嫖算力。

        **直接调 `_finish_waiting_human`** 而不是驱动真实调度：调度对失败节点用
        指数退避重试（1s/3s/9s），端到端等不稳定；这里要测的是「转人工时是否结算」，
        用最小的状态前置即可。
        """
        from types import SimpleNamespace

        from app.orchestration.contracts import TaskStatus

        engine, factory = await _setup_engine(tmp_path)
        quota = _FakeQuota()
        try:
            task_id, user_id = await _seed_task(
                factory, status=TaskStatus.RUNNING.value, used_tokens=30_000
            )
            execution = _make_execution(factory, quota)

            await execution._finish_waiting_human(
                task_id, SimpleNamespace(completed=1, waiting_human=["n2"])
            )

            # 退 100k − 30k，而不是全额 100k
            assert quota.refunds == [(user_id, ESTIMATED_TOKENS_PER_TASK - 30_000)]
            async with factory() as session:
                task = await session.get(Task, task_id)
                assert task is not None
                assert task.status == TaskStatus.WAITING_HUMAN.value
                assert task.quota_settled is True
        finally:
            await engine.dispose()

    async def test_planning_failure_refunds_full_reservation(self, tmp_path: Path) -> None:
        """规划失败转人工：用量 0 → 全额退还（此前这条路径永不退还）。"""
        from app.orchestration.base_agent import BaseAgent
        from app.orchestration.contracts import TaskStatus
        from app.orchestration.runtime import RuntimeContainer

        engine, factory = await _setup_engine(tmp_path)
        quota = _FakeQuota()

        class _FailingPlanner(BaseAgent):
            @property
            def agent_type(self) -> str:
                return "planner"

            async def _run(self, input_data: dict[str, Any]) -> dict[str, Any]:
                raise ValueError("规划总是失败")

        container = RuntimeContainer(
            session_factory=factory,
            planner_factory=lambda: _FailingPlanner(),
            agent_factory=lambda _t: None,
        )
        execution = TaskExecution(container, quota_service=quota)
        user_id = uuid.uuid4()
        try:
            task_id = await execution.submit(user_id, _request())
            await _wait_status(execution, user_id, task_id, {TaskStatus.WAITING_HUMAN.value})

            assert quota.total_refunded == ESTIMATED_TOKENS_PER_TASK
        finally:
            await container.close()
            await engine.dispose()


class TestPrereserveGuards:
    """重新预扣的两种失败语义：额度不足拒绝、并发冲突撤销。"""

    async def test_prereserve_raises_when_quota_insufficient(self, tmp_path: Path) -> None:
        """额度不足 → 抛 `QuotaExceededError`（HTTP 429），且**不**解除已结算标记。

        为什么不「先解除标记再扣」：扣失败留下「已结算=false」会让任务处于
        「预扣已退、标记说没退」的矛盾状态；用户充值后再确认时重复预扣。
        """
        from app.core.quota import QuotaExceededError

        engine, factory = await _setup_engine(tmp_path)
        quota = _FakeQuota(error=QuotaExceededError(retry_after=60, used=1, limit=1))
        try:
            task_id, user_id = await _seed_task(factory, used_tokens=30_000, quota_settled=True)
            execution = _make_execution(factory, quota)

            with pytest.raises(QuotaExceededError):
                await execution._prereserve_quota(user_id, task_id)

            async with factory() as session:
                task = await session.get(Task, task_id)
                assert task is not None
                assert task.quota_settled is True, "扣款失败时不得解除已结算标记"
        finally:
            await engine.dispose()

    async def test_prereserve_is_noop_when_not_settled(self, tmp_path: Path) -> None:
        """尚未结算（预扣仍在）→ 不重复扣。"""
        engine, factory = await _setup_engine(tmp_path)
        quota = _FakeQuota()
        try:
            task_id, user_id = await _seed_task(factory, used_tokens=0, quota_settled=False)
            execution = _make_execution(factory, quota)

            await execution._prereserve_quota(user_id, task_id)

            assert quota.consumes == []
        finally:
            await engine.dispose()
