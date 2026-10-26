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

    def test_reads_production_payload_nested_execution_result(self) -> None:
        """**生产真实载荷**：token 在 `output["execution_result"]` 里，不是顶层。

        这条是本轮（用量看板）的核心回归测试。上面三条用例的载荷都是
        「token 平铺在 output 顶层」——那是**测试替身**的形状，生产从来不是这样：

        `LiteratureNodeExecutor._run` 返回的是

        ```python
        {"node_id": ..., "result": ..., "status": "completed", "used_tools": [],
         "warnings": [...], "execution_result": {..., "prompt_tokens": N,
                                                "completion_tokens": M},
         "evidence_pool": [...]}
        ```

        于是 `sum_actual_tokens` 只看顶层 → 恒返回 0 → `task.used_tokens` 永远 0 →
        配额结算退满额（用户白嫖算力）+ 用量看板「累计 token」永远是 0。

        服务器实测（2026-10-26）：42 个任务的 `subtask.output_data` 里能算出
        **169372** token，而 `task.used_tokens` 全部为 0。上面三条用例全绿，
        缺陷却一直在——正是 AGENTS.md §5 #13 说的「假测试」。
        """
        from app.orchestration.services.task_execution import sum_actual_tokens

        class _Node:
            def __init__(self, output: dict[str, Any] | None) -> None:
                self.output = output

        node_results = {
            "n1": _Node(
                {
                    "node_id": "n1",
                    "result": "正文",
                    "status": "completed",
                    "used_tools": [],
                    "warnings": [],
                    "execution_result": {
                        "node_id": "n1",
                        "content": "正文",
                        "duration_ms": 1200,
                        "prompt_tokens": 1000,
                        "completion_tokens": 500,
                    },
                    "evidence_pool": [],
                }
            ),
            "n2": _Node(
                {
                    "node_id": "n2",
                    "result": "正文2",
                    "execution_result": {
                        "node_id": "n2",
                        "content": "正文2",
                        "duration_ms": 800,
                        "prompt_tokens": 2000,
                        "completion_tokens": 800,
                    },
                }
            ),
        }

        assert sum_actual_tokens(node_results) == 4300

    def test_nested_and_flat_payloads_are_not_double_counted(self) -> None:
        """同一节点同时有顶层与嵌套 token 时只算一次（防重复累加）。

        为什么这条必要：修法若是「顶层 + 嵌套都加」，未来某个 agent 一旦把
        两个位置都填上，用量就会翻倍 → 结算多扣用户额度。
        """
        from app.orchestration.services.task_execution import sum_actual_tokens

        class _Node:
            def __init__(self, output: dict[str, Any] | None) -> None:
                self.output = output

        node_results = {
            "n1": _Node(
                {
                    "prompt_tokens": 1000,
                    "completion_tokens": 500,
                    "execution_result": {"prompt_tokens": 1000, "completion_tokens": 500},
                }
            )
        }

        assert sum_actual_tokens(node_results) == 1500
