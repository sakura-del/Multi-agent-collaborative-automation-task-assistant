"""自动重试机制单元测试：退避、策略切换、耗尽触发人工干预。"""

import asyncio

import pytest

from app.orchestration.retry import RetryExecutor, RetryPolicy
from app.orchestration.task_state import SubtaskStatus, TaskStateMachine


class TestRetryPolicy:
    """重试策略配置测试。"""

    def test_default_backoff_sequence(self) -> None:
        """默认退避序列为 1s/3s/9s。"""
        policy = RetryPolicy()
        assert policy.get_backoff(0) == 1.0
        assert policy.get_backoff(1) == 3.0
        assert policy.get_backoff(2) == 9.0

    def test_default_max_retries(self) -> None:
        """默认最多重试 3 次。"""
        assert RetryPolicy().max_retries == 3

    def test_strategy_changes_per_attempt(self) -> None:
        """每次重试策略不同。"""
        policy = RetryPolicy()
        s0 = policy.get_strategy(0)
        s1 = policy.get_strategy(1)
        s2 = policy.get_strategy(2)
        assert s0["strategy"] != s1["strategy"]
        assert s1["strategy"] != s2["strategy"]

    def test_backoff_out_of_range_returns_last(self) -> None:
        """超出范围的退避时间用最后一个值。"""
        policy = RetryPolicy()
        assert policy.get_backoff(99) == 9.0


@pytest.mark.asyncio
class TestRetryExecutor:
    """重试执行器测试。"""

    async def test_success_on_first_attempt(self) -> None:
        """首次成功不重试。"""
        call_count = 0

        async def func() -> str:
            nonlocal call_count
            call_count += 1
            return "ok"

        executor = RetryExecutor(RetryPolicy(max_retries=3))
        result = await executor.execute(func)
        assert result.success
        assert result.attempts == 1
        assert call_count == 1

    async def test_success_after_retry(self) -> None:
        """首次失败，第二次成功。"""
        call_count = 0

        async def func() -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("首次失败")
            return "ok"

        executor = RetryExecutor(RetryPolicy(max_retries=3))
        result = await executor.execute(func)
        assert result.success
        assert result.attempts == 2
        assert call_count == 2

    async def test_retry_exhausted_triggers_waiting_human(self) -> None:
        """重试耗尽后状态转为 WAITING_HUMAN。"""
        call_count = 0

        async def func() -> None:
            nonlocal call_count
            call_count += 1
            raise RuntimeError(f"第{call_count}次失败")

        sm = TaskStateMachine(node_id="n1")
        executor = RetryExecutor(RetryPolicy(max_retries=2, backoff_times=[0.01, 0.01]))
        result = await executor.execute(func, state_machine=sm)

        assert not result.success
        assert result.attempts == 3  # 首次 + 2 次重试
        assert result.final_status is SubtaskStatus.WAITING_HUMAN
        assert sm.status is SubtaskStatus.WAITING_HUMAN

    async def test_retry_injects_strategy_params(self) -> None:
        """重试时注入策略参数到 kwargs。"""
        received_temps: list[float | None] = []

        async def func(**kwargs: object) -> str:
            received_temps.append(kwargs.get("temperature"))  # type: ignore[arg-type]
            if len(received_temps) == 1:
                raise RuntimeError("首次失败")
            return "ok"

        executor = RetryExecutor(RetryPolicy(max_retries=3))
        await executor.execute(func)
        # 首次无 temperature 注入，重试有
        assert received_temps[0] is None
        assert received_temps[1] is not None

    async def test_retry_respects_max_retries(self) -> None:
        """重试次数不超过 max_retries。"""
        call_count = 0

        async def func() -> None:
            nonlocal call_count
            call_count += 1
            raise RuntimeError("总是失败")

        executor = RetryExecutor(RetryPolicy(max_retries=1, backoff_times=[0.01]))
        result = await executor.execute(func)
        assert call_count == 2  # 首次 + 1 次重试
        assert not result.success

    async def test_backoff_delay_applied(self) -> None:
        """重试前有退避等待。"""
        call_times: list[float] = []

        async def func() -> str:
            call_times.append(asyncio.get_event_loop().time())
            if len(call_times) == 1:
                raise RuntimeError("首次失败")
            return "ok"

        executor = RetryExecutor(RetryPolicy(max_retries=1, backoff_times=[0.1]))
        await executor.execute(func)
        # 第二次调用与第一次间隔至少 0.1s
        assert call_times[1] - call_times[0] >= 0.09
