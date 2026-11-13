"""任务级 LLM 作用域（Issue #15 · BYOK P0-2）。

## 为什么需要 contextvar

既有的取模型路径是 `RuntimeContainer.create_llm()` → 全局 `LLMManager` 单例，
**不带任何上下文**。深层节点（规划器、执行器、校验器）各自调用它，签名里
没有 `task`，也没有 `user`。要在不改十几处签名的前提下切换成「这个任务用
这个用户的 key」，只能靠隐式上下文。

## 为什么不用参数透传

从 `TaskExecution` 到 `LiteratureReviewWorkflow` 再到每个 Agent，至少四层。
逐层加 `llm=` 参数会把「用哪把 key」这件事变成每个函数都要关心的参数，
且新加节点极易漏传（漏传 = 静默用平台 key，正是我们要避免的）。

## 与 `asyncio.create_task` 的关系

任务创建时**复制当前 context**。因此必须在 `create_task` 之前进入作用域，
否则后台协程拿到的是空值（会静默回落到平台模型）。P0-3 接线时必须注意。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

#: 当前任务使用的 LLM 客户端；None = 走容器默认（平台模型）。
_current_client: ContextVar[Any | None] = ContextVar("veripaper_llm_client", default=None)


@contextmanager
def use_llm_client(client: Any) -> Iterator[None]:
    """在 `with` 块内把 `create_llm()` 固定到指定客户端。

    支持嵌套：内层退出后恢复外层（`Token` 负责精确还原，异常路径也一样）。
    """
    token = _current_client.set(client)
    try:
        yield
    finally:
        _current_client.reset(token)


def get_scoped_llm() -> Any | None:
    """取当前作用域的客户端；不在作用域内返回 None。"""
    return _current_client.get()
