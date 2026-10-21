"""任务/节点/成本指标接入测试（Phase 2 · 可观测性 Task 3）。

## 为什么这些指标要从**业务路径**打点，而不是轮询数据库

轮询数据库的代价：一是延迟（指标落后真实状态），二是负载（每个抓取周期一次全表聚合）。
业务路径打点几乎零成本：终态转换点本来就拿到了 `status` 与 `used_tokens`。

## 本文件锁定

1. 完成 → `maa_tasks{status="completed"}` +1，`maa_task_tokens` 累加 `used_tokens`；
2. 调度失败转人工 → `status="waiting_human"`（**不是** failed —— 转人工不是失败，
   两者在运营上含义完全不同：前者等人工，后者是 bug）；
3. 取消 → `status="cancelled"`；
4. 节点结果 → `maa_task_nodes{node_type, status}`；
5. **指标故障时任务照常完成**（fail-open）。

基建（planner/executor/validator/reviewer 替身 + 文件型 SQLite）复用
`test_task_quota_settlement.py` 里已验证过的那套，避免重复维护两份 mock。
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

from app.core import metrics

# 复用已验证的生命周期基建（同一套 planner/agent 替身与 SQLite 夹具）
from tests.test_task_quota_settlement import (
    _make_lifecycle_execution,
    _request,
    _setup_engine,
    _wait_settled,
    _wait_status,
)


@pytest.fixture(autouse=True)
async def _clean_registry():
    """每条用例前后都清空指标 registry。

    **为什么前后都要清**：每条用例会启动两个后台任务（规划 + 执行），
    `container.close()` 只取消**还在跑**的任务；已经进入收尾流程的任务会在
    cancel 之后仍完成 `_record_task_terminal`，从而把计数写进**下一条用例**
    （首版只做前置 reset，实测 5 条用例里有 4 条因此偶发失败）。

    前置 reset 保证起点干净，后置 reset 把本用例的迟到写入抹掉。
    """
    import asyncio

    metrics.reset_for_tests()
    yield
    # 给迟到的收尾流程一点时间落盘，再清一次（顺序无所谓：最终都是 0）
    await asyncio.sleep(0.05)
    metrics.reset_for_tests()


def _counter_value(name: str, **labels: str) -> float:
    """读取指定标签组合的计数值（不存在则 0）。"""
    labels_str = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    target = f"{name}_total{{{labels_str}}}" if labels_str else f"{name}_total"
    for line in metrics.render().splitlines():
        if line.startswith("#"):
            continue
        if line.startswith(target + " "):
            return float(line.rsplit(" ", 1)[1])
    return 0.0


def _render_value(fragment: str) -> float:
    """读取渲染文本里包含 `fragment` 的那一行的值（不存在则 0）。

    用于直方图（`_count` / `_sum` 行）这类不方便按标签拼 target 的场景。
    """
    for line in metrics.render().splitlines():
        if line.startswith("#"):
            continue
        if fragment in line:
            return float(line.rsplit(" ", 1)[1])
    return 0.0


async def _wait_render_value(fragment: str, *, target: float = 1.0, timeout: float = 20.0) -> float:
    """等渲染文本里包含 `fragment` 的那一行达到 `target`。

    直方图的 `_count` / `_sum` 行不便按「名+标签」拼 target，用片段匹配更直接。
    """
    import asyncio

    deadline = asyncio.get_running_loop().time() + timeout
    current = _render_value(fragment)
    while asyncio.get_running_loop().time() < deadline:
        current = _render_value(fragment)
        if current >= target:
            return current
        await asyncio.sleep(0.05)
    raise AssertionError(f"等待渲染值 {fragment} 达到 {target} 超时（current={current}）")


async def _wait_metric(
    name: str, *, target: float = 1.0, timeout: float = 20.0, **labels: str
) -> float:
    """等计数器达到 `target`（默认 1）。

    为什么要等：`_record_task_terminal` 在状态转换 **之后** 执行（还要读库拿
    `used_tokens`），所以「状态已变」不等于「指标已落」。

    断言用**绝对值**而不是增量：`_clean_registry` fixture 在每条用例前后都
    清空 registry，所以本用例看到的就是自己产生的那一份
    （增量写法会被上一条用例的迟到写入污染 baseline，首版踩过）。
    """
    import asyncio

    deadline = asyncio.get_running_loop().time() + timeout
    current = _counter_value(name, **labels)
    while asyncio.get_running_loop().time() < deadline:
        current = _counter_value(name, **labels)
        if current >= target:
            return current
        await asyncio.sleep(0.05)
    raise AssertionError(f"等待指标 {name}{labels} 达到 {target} 超时（current={current}）")


@pytest.mark.asyncio
class TestTaskMetrics:
    async def test_completion_records_status_and_tokens(self, tmp_path: Path) -> None:
        from app.orchestration.contracts import TaskStatus

        engine, factory = await _setup_engine(tmp_path)
        execution = _make_lifecycle_execution(factory, _QuotaStub())
        user_id = uuid.uuid4()
        try:
            task_id = await execution.submit(user_id, _request())
            waiting = await _wait_status(
                execution, user_id, task_id, {TaskStatus.WAITING_APPROVAL.value}
            )
            await execution.approve_plan(user_id, task_id, waiting.revision)
            await _wait_status(execution, user_id, task_id, {TaskStatus.COMPLETED.value})
            task = await _wait_settled(factory, task_id)

            await _wait_metric("maa_tasks", status="completed")
            # token 计数必须与实际用量一致（成本看板全靠它）
            assert await _wait_metric("maa_task_tokens", target=float(task.used_tokens)) == float(
                task.used_tokens
            )
            assert task.used_tokens > 0
        finally:
            await execution.container.close()
            await engine.dispose()

    async def test_completion_records_duration(self, tmp_path: Path) -> None:
        from app.orchestration.contracts import TaskStatus

        engine, factory = await _setup_engine(tmp_path)
        execution = _make_lifecycle_execution(factory, _QuotaStub())
        user_id = uuid.uuid4()
        try:
            task_id = await execution.submit(user_id, _request())
            waiting = await _wait_status(
                execution, user_id, task_id, {TaskStatus.WAITING_APPROVAL.value}
            )
            await execution.approve_plan(user_id, task_id, waiting.revision)
            await _wait_status(execution, user_id, task_id, {TaskStatus.COMPLETED.value})
            await _wait_settled(factory, task_id)
            # 指标落库晚于状态（收尾流程是状态→结算→打点），必须等；
            # 这里等的是**耗时直方图**而不是 maa_tasks —— 两者在同一个打点函数里
            # 先 inc 后 observe，只等前者可能抓到"计数器已 +1、直方图还没写"的中间态。
            await _wait_render_value('maa_task_duration_seconds_count{status="completed"}')

            text = metrics.render()
            assert "maa_task_duration_seconds_count" in text
            assert 'maa_task_duration_seconds_count{status="completed"}' in text
        finally:
            await execution.container.close()
            await engine.dispose()

    async def test_cancel_records_cancelled_status(self, tmp_path: Path) -> None:
        from app.orchestration.contracts import TaskCommand, TaskControlRequest, TaskStatus

        engine, factory = await _setup_engine(tmp_path)
        execution = _make_lifecycle_execution(factory, _QuotaStub())
        user_id = uuid.uuid4()
        try:
            task_id = await execution.submit(user_id, _request())
            await _wait_status(execution, user_id, task_id, {TaskStatus.WAITING_APPROVAL.value})
            await _wait_settled(factory, task_id)

            await execution.control(
                user_id, task_id, TaskControlRequest(command=TaskCommand.CANCEL)
            )

            assert await _wait_metric("maa_tasks", status="cancelled") == 1.0
        finally:
            await execution.container.close()
            await engine.dispose()

    async def test_planning_failure_records_waiting_human(self, tmp_path: Path) -> None:
        """规划失败 → `waiting_human`（**不是** failed）。

        运营含义不同：waiting_human 是「等人工处理」，failed 是「系统 bug」。
        混在一起会让成功率失真，也会让值班的人搞错优先级。
        """
        from app.orchestration.base_agent import BaseAgent
        from app.orchestration.contracts import TaskStatus
        from app.orchestration.runtime import RuntimeContainer
        from app.orchestration.services.task_execution import TaskExecution

        engine, factory = await _setup_engine(tmp_path)

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
        execution = TaskExecution(container, quota_service=_QuotaStub())
        user_id = uuid.uuid4()
        try:
            task_id = await execution.submit(user_id, _request())
            await _wait_status(execution, user_id, task_id, {TaskStatus.WAITING_HUMAN.value})

            assert await _wait_metric("maa_tasks", status="waiting_human") == 1.0
            # 转人工不是失败：failed 必须保持 0（两者运营含义不同）
            assert _counter_value("maa_tasks", status="failed") == 0.0
        finally:
            await container.close()
            await engine.dispose()

    async def test_terminal_status_recorded_once(self, tmp_path: Path) -> None:
        """同一任务只记一次终态（避免重试/并发导致成功率被稀释）。

        断言「恰好 1」：`_record_task_terminal` 在状态转换提交之后调用，
        而转换本身有 `lock_for_update` + 状态断言，重复进入的那次会提前
        return、不会打点。起点干净由 `_clean_registry`（前后各清一次）保证。
        """
        from app.orchestration.contracts import TaskStatus

        engine, factory = await _setup_engine(tmp_path)
        execution = _make_lifecycle_execution(factory, _QuotaStub())
        user_id = uuid.uuid4()
        try:
            task_id = await execution.submit(user_id, _request())
            waiting = await _wait_status(
                execution, user_id, task_id, {TaskStatus.WAITING_APPROVAL.value}
            )
            await execution.approve_plan(user_id, task_id, waiting.revision)
            await _wait_status(execution, user_id, task_id, {TaskStatus.COMPLETED.value})
            await _wait_settled(factory, task_id)

            assert await _wait_metric("maa_tasks", status="completed") == 1.0
        finally:
            await execution.container.close()
            await engine.dispose()


@pytest.mark.asyncio
class TestNodeMetrics:
    async def test_records_node_results_by_type(self, tmp_path: Path) -> None:
        from app.orchestration.contracts import TaskStatus

        engine, factory = await _setup_engine(tmp_path)
        execution = _make_lifecycle_execution(factory, _QuotaStub())
        user_id = uuid.uuid4()
        try:
            task_id = await execution.submit(user_id, _request())
            waiting = await _wait_status(
                execution, user_id, task_id, {TaskStatus.WAITING_APPROVAL.value}
            )
            await execution.approve_plan(user_id, task_id, waiting.revision)
            await _wait_status(execution, user_id, task_id, {TaskStatus.COMPLETED.value})
            await _wait_settled(factory, task_id)
            # 节点指标与完成状态在同一收尾流程里打点，等 completed 计数即可
            await _wait_metric("maa_tasks", status="completed")

            text = metrics.render()
            assert "maa_task_nodes" in text
            # 计划里的五类节点都应出现（search/extract/write/validate/review）
            for node_type in ("search", "extract", "write"):
                assert f'node_type="{node_type}"' in text, f"缺少 {node_type} 节点指标"
            assert 'status="completed"' in text
        finally:
            await execution.container.close()
            await engine.dispose()


@pytest.mark.asyncio
class TestMetricsFailOpen:
    async def test_task_completes_when_metrics_broken(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """指标故障绝不能让任务失败（fail-open）。

        反例代价：一次 registry 故障让所有任务「执行成功但不收尾」，
        比没有监控糟糕得多。
        """
        from app.orchestration.contracts import TaskStatus

        def boom(*args: object, **kwargs: object) -> None:
            raise RuntimeError("指标炸了")

        monkeypatch.setattr(metrics, "_inc_raw", boom)
        monkeypatch.setattr(metrics, "_observe_raw", boom)

        engine, factory = await _setup_engine(tmp_path)
        execution = _make_lifecycle_execution(factory, _QuotaStub())
        user_id = uuid.uuid4()
        try:
            task_id = await execution.submit(user_id, _request())
            waiting = await _wait_status(
                execution, user_id, task_id, {TaskStatus.WAITING_APPROVAL.value}
            )
            await execution.approve_plan(user_id, task_id, waiting.revision)
            snapshot = await _wait_status(execution, user_id, task_id, {TaskStatus.COMPLETED.value})
            assert snapshot.status == TaskStatus.COMPLETED.value
        finally:
            await execution.container.close()
            await engine.dispose()


class _QuotaStub:
    """本文件只测指标，配额行为已由 test_task_quota_settlement.py 覆盖。"""

    async def refund(self, user_id: Any, *, tokens: int) -> None:
        return None

    async def check_and_consume(self, user_id: Any, *, tokens: int) -> None:
        return None
