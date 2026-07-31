"""自动重试机制：指数退避 + 策略切换 + 人工干预触发。

职责：
- 指数退避重试（1s→3s→9s，最多3次）
- 每次重试自动切换执行策略（调高温度/换 Prompt/增工具）
- 重试耗尽后将子任务状态转为 WAITING_HUMAN

设计要点：
- 策略切换表：按重试次数递进调整参数，避免相同输入导致相同失败
- 退避时间可配置，默认 3^n 秒（1/3/9）
- 重试逻辑与业务解耦：RetryExecutor 接受可调用对象，不关心具体任务
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger
from app.orchestration.task_state import SubtaskStatus, TaskStateMachine

logger = get_logger(__name__)

# 默认退避序列：1s → 3s → 9s（3^0, 3^1, 3^2）
_DEFAULT_BACKOFF: list[float] = [1.0, 3.0, 9.0]

# 重试策略切换表：每次重试调整执行参数，增加成功率
# 第1次重试：提高温度（增加探索性）
# 第2次重试：降低温度 + 换简化 Prompt
# 第3次重试：恢复默认 + 启用工具调用
_RETRY_STRATEGIES: list[dict[str, Any]] = [
    {"temperature": 0.9, "strategy": "high_temp_exploration"},
    {"temperature": 0.2, "strategy": "low_temp_focused", "simplify_prompt": True},
    {"temperature": 0.5, "strategy": "enable_tools", "use_tools": True},
]


def _filter_strategy_params(
    func: Callable[..., Awaitable[Any]],
    strategy: dict[str, Any],
) -> dict[str, Any]:
    """过滤策略参数，只保留 func 实际接受的参数。

    避免向不接受 temperature 等参数的函数注入多余 kwargs。
    若 func 支持 **kwargs，则全部注入（由调用方自行取舍）。
    """
    try:
        sig = inspect.signature(func)
        params = sig.parameters
        # func 接受 **kwargs 时，全部注入
        if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
            return dict(strategy)
        # 否则只注入 func 已声明的参数
        return {k: v for k, v in strategy.items() if k in params}
    except (ValueError, TypeError):
        return dict(strategy)


@dataclass
class RetryPolicy:
    """重试策略配置。

    backoff_times 为各次重试前的等待秒数，长度即最大重试次数。
    """

    max_retries: int = 3
    backoff_times: list[float] = field(default_factory=lambda: list(_DEFAULT_BACKOFF))
    strategies: list[dict[str, Any]] = field(default_factory=lambda: list(_RETRY_STRATEGIES))

    def get_backoff(self, attempt: int) -> float:
        """获取第 attempt 次重试的退避时间（attempt 从 0 开始）。"""
        if attempt < len(self.backoff_times):
            return self.backoff_times[attempt]
        return self.backoff_times[-1]  # 超出范围用最后一个值

    def get_strategy(self, attempt: int) -> dict[str, Any]:
        """获取第 attempt 次重试的策略参数。"""
        if attempt < len(self.strategies):
            return self.strategies[attempt]
        return self.strategies[-1]


@dataclass
class RetryResult:
    """重试执行结果。"""

    success: bool
    result: Any = None
    attempts: int = 0  # 总尝试次数（含首次）
    final_status: SubtaskStatus = SubtaskStatus.COMPLETED
    error: str | None = None


class RetryExecutor:
    """重试执行器：包裹可调用对象，失败时按策略重试。

    用法：
        executor = RetryExecutor(policy=RetryPolicy(max_retries=3))
        result = await executor.execute(
            func=agent.execute,
            input_data={...},
            state_machine=subtask_sm,
        )
    """

    def __init__(self, policy: RetryPolicy | None = None) -> None:
        self.policy = policy or RetryPolicy()

    async def execute(
        self,
        func: Callable[..., Awaitable[Any]],
        *,
        args: tuple = (),
        kwargs: dict[str, Any] | None = None,
        state_machine: TaskStateMachine | None = None,
    ) -> RetryResult:
        """执行带重试的任务。

        func 为异步可调用对象，失败（抛异常）时按退避时间等待后重试。
        每次重试按策略参数调整输入（仅注入 func 实际接受的参数）。
        重试耗尽后更新状态机为 WAITING_HUMAN。

        状态流转（需 state_machine 配合）：
            PENDING → RUNNING → (失败) → FAILED → RETRYING → RUNNING → ... → WAITING_HUMAN
        成功时保持 RUNNING，由调用方推进到 VALIDATING/COMPLETED。
        """
        kwargs = dict(kwargs or {})
        max_retries = self.policy.max_retries
        last_error: str | None = None

        for attempt in range(max_retries + 1):  # 首次 + 重试次数
            if attempt == 0:
                # 首次调用前：PENDING → RUNNING（确保后续 FAILED 合法）
                if state_machine is not None and state_machine.status is SubtaskStatus.PENDING:
                    state_machine.transition(SubtaskStatus.RUNNING)
            else:
                # 重试前：FAILED → RETRYING → RUNNING，并注入策略参数
                strategy = self.policy.get_strategy(attempt - 1)
                kwargs.update(_filter_strategy_params(func, strategy))
                if state_machine is not None:
                    state_machine.transition(SubtaskStatus.RETRYING)
                    state_machine.transition(SubtaskStatus.RUNNING)
                logger.info(
                    "retry_attempt",
                    attempt=attempt,
                    strategy=strategy.get("strategy"),
                )

            try:
                result = await func(*args, **kwargs)
                return RetryResult(
                    success=True,
                    result=result,
                    attempts=attempt + 1,
                    final_status=SubtaskStatus.COMPLETED,
                )
            except Exception as exc:
                last_error = str(exc)
                logger.warning(
                    "retry_failed",
                    attempt=attempt + 1,
                    max_attempts=max_retries + 1,
                    error=last_error,
                )
                # 失败后：RUNNING → FAILED（合法转换）
                if state_machine is not None:
                    state_machine.transition(SubtaskStatus.FAILED)
                if attempt < max_retries:
                    # 等待退避时间后重试
                    backoff = self.policy.get_backoff(attempt)
                    await asyncio.sleep(backoff)
                else:
                    # 重试耗尽：FAILED → WAITING_HUMAN
                    if state_machine is not None:
                        state_machine.transition(SubtaskStatus.WAITING_HUMAN)
                    logger.error(
                        "retry_exhausted",
                        attempts=attempt + 1,
                        error=last_error,
                    )

        return RetryResult(
            success=False,
            attempts=max_retries + 1,
            final_status=SubtaskStatus.WAITING_HUMAN,
            error=last_error,
        )
