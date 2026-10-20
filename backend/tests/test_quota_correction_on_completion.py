"""任务完成时按实际用量校正配额（Phase 1 P1-D 收尾）。

## 为什么需要

提交任务时按 `ESTIMATED_TOKENS_PER_TASK`（100k）**预扣**——这是唯一能事前拒绝
的位置（超限 429、任务不创建）。但预估必然不准：

- 实际 < 预估 → 多扣了用户额度（不公平，当天额度会提前耗尽）；
- 实际 > 预估 → 少扣（成本失控）。

因此任务结束后按**实际用量**校正：`refund(预估 - 实际)`。
实际超过预估时不补扣（用户已花掉，补扣只会让下一个任务莫名失败，
且预估值本身是保守上限，超出的概率低）。

## 本文件锁定

`TaskExecution._correct_quota(task_id, result)`：
- 从各节点 `ExecutionResult.prompt_tokens + completion_tokens` 汇总实际用量；
- 退还 `max(0, ESTIMATED - 实际)`；
- **配额组件故障绝不能让任务失败**（fail-open，只记 warning）；
- 配额关闭 / 无 Redis 时静默跳过。
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.orchestration.services.task_execution import (
    ESTIMATED_TOKENS_PER_TASK,
    quota_refund_for_usage,
)


class _FakeQuota:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.refunds: list[tuple[Any, int]] = []

    async def refund(self, user_id: Any, *, tokens: int) -> None:
        self.refunds.append((user_id, tokens))
        if self.error is not None:
            raise self.error


class TestQuotaRefundForUsage:
    @pytest.mark.asyncio
    async def test_refunds_difference_between_estimate_and_actual(self) -> None:
        quota = _FakeQuota()
        user_id = uuid.uuid4()

        await quota_refund_for_usage(quota, user_id, actual_tokens=40_000)

        assert quota.refunds == [(user_id, ESTIMATED_TOKENS_PER_TASK - 40_000)]

    @pytest.mark.asyncio
    async def test_no_refund_when_actual_exceeds_estimate(self) -> None:
        """实际超过预估 → 不退还（也不补扣，避免下一个任务莫名失败）。"""
        quota = _FakeQuota()

        await quota_refund_for_usage(quota, uuid.uuid4(), actual_tokens=200_000)

        assert quota.refunds == []

    @pytest.mark.asyncio
    async def test_no_refund_for_zero_actual(self) -> None:
        """实际为 0（如离线清单任务）→ 全额退还。"""
        quota = _FakeQuota()
        user_id = uuid.uuid4()

        await quota_refund_for_usage(quota, user_id, actual_tokens=0)

        assert quota.refunds == [(user_id, ESTIMATED_TOKENS_PER_TASK)]

    @pytest.mark.asyncio
    async def test_quota_none_is_noop(self) -> None:
        """配额关闭（None）时不调用。"""
        await quota_refund_for_usage(None, uuid.uuid4(), actual_tokens=1000)

    @pytest.mark.asyncio
    async def test_refund_error_does_not_propagate(self) -> None:
        """配额组件故障绝不能拖垮任务（fail-open）。"""
        quota = _FakeQuota(error=ConnectionError("redis down"))

        # 不应抛异常
        await quota_refund_for_usage(quota, uuid.uuid4(), actual_tokens=1000)


class TestActualTokenAggregation:
    def test_sums_prompt_and_completion_across_nodes(self) -> None:
        """从各节点 ExecutionResult 汇总实际 token。"""
        from app.orchestration.services.task_execution import sum_actual_tokens

        class _R:
            def __init__(self, p: int, c: int) -> None:
                self.prompt_tokens = p
                self.completion_tokens = c

        class _Node:
            def __init__(self, output: dict[str, Any] | None) -> None:
                self.output = output

        node_results = {
            "n1": _Node({"prompt_tokens": 100, "completion_tokens": 50}),
            "n2": _Node({"prompt_tokens": 200, "completion_tokens": 80}),
            "n3": _Node(None),  # 未执行的节点
        }

        assert sum_actual_tokens(node_results) == 430

    def test_returns_zero_for_empty_results(self) -> None:
        from app.orchestration.services.task_execution import sum_actual_tokens

        assert sum_actual_tokens({}) == 0

    def test_tolerates_missing_fields(self) -> None:
        """历史载荷可能没有 token 字段 → 按 0 计，不抛错。"""
        from app.orchestration.services.task_execution import sum_actual_tokens

        class _Node:
            def __init__(self) -> None:
                self.output = {"node_id": "n1", "content": "x"}

        assert sum_actual_tokens({"n1": _Node()}) == 0
