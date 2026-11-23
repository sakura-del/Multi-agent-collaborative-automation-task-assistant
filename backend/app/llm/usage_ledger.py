"""任务级 LLM 用量账本（Issue #16 修复）。

## 为什么

旧实现只汇总「节点最终结果里的 usage」（`sum_actual_usage(node_results)`），
实测漏计 63%~70%：规划、校验、复盘、校验重试、以及路由到本地模型的调用
全部不在节点结果里；且复盘调用发生在记账**之后**，必然不入账。

本账本在 `BaseLLM.chat` 成功路径统一累加（含路由委派的调用——目标客户端
自己的 chat 会走完整成功路径，恰好累加一次），由任务协程在结算点
`take()` 取增量写库，与「任务分多段跑、增量累加」的既有语义一致。

## 边界

- 超时/429 失败的请求没有 usage 可记（SDK 不返回），只统计成功调用；
- 账本按 ContextVar 绑定当前协程，任务之间互不串账。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from app.llm.models import Usage


class UsageLedger:
    """累加器：记录成功调用的 token 明细，`take()` 取增量并清零。"""

    def __init__(self) -> None:
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cache_hit_tokens = 0
        self.cache_miss_tokens = 0

    def add(self, usage: Usage) -> None:
        self.calls += 1
        self.prompt_tokens += max(0, int(usage.prompt_tokens))
        self.completion_tokens += max(0, int(usage.completion_tokens))
        self.cache_hit_tokens += max(0, int(usage.cache_hit_tokens))
        self.cache_miss_tokens += max(0, int(usage.cache_miss_tokens))

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def take(self) -> Usage:
        """返回自上次 take 以来的增量，并清零（供分段结算）。"""
        snapshot = Usage(
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            cache_hit_tokens=self.cache_hit_tokens,
            cache_miss_tokens=self.cache_miss_tokens,
        )
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cache_hit_tokens = 0
        self.cache_miss_tokens = 0
        return snapshot


_current: ContextVar[UsageLedger | None] = ContextVar("llm_usage_ledger", default=None)


@contextmanager
def use_usage_ledger() -> Iterator[UsageLedger]:
    """在作用域内绑定一个新账本（任务协程入口使用）。"""
    ledger = UsageLedger()
    token = _current.set(ledger)
    try:
        yield ledger
    finally:
        _current.reset(token)


def current_ledger() -> UsageLedger | None:
    return _current.get()


def record_usage(usage: Usage) -> None:
    """记录一次成功调用的用量（无账本时静默跳过，兼容脚本/测试直调）。"""
    ledger = _current.get()
    if ledger is not None:
        ledger.add(usage)
