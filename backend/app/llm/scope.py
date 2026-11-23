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


def bind_llm_client(client: Any) -> None:
    """在当前 asyncio Task 的 context 里绑定客户端，**不还原**。

    只给后台任务协程用（`TaskExecution._plan` / `_execute`）：context 随协程
    结束一起丢弃，不会影响其他请求；子协程在 `create_task` 时复制当前 context，
    所以必须**先绑定再创建**。

    HTTP 请求内请用 `with use_llm_client(...)`：那里需要精确还原，
    否则同一个请求后续的数据库/事件逻辑可能误用用户模型。
    """
    _current_client.set(client)


# ===== 用户级节点路由（Issue #22 延伸：混合路由用户化）=====

#: 当前任务的用户级路由：node_type → 已解析的客户端。
#: 由 `_bind_task_llm` 在绑定主客户端时一并组装（同一 ContextVar 生命周期）。
_user_routes: ContextVar[dict[str, Any] | None] = ContextVar("veripaper_user_routes", default=None)


def bind_user_routes(routes: dict[str, Any]) -> None:
    """绑定当前任务的用户级节点路由（与 bind_llm_client 同生命周期）。"""
    _user_routes.set(routes or None)


def get_user_routes() -> dict[str, Any] | None:
    """取当前任务的用户级路由；未配置返回 None。"""
    return _user_routes.get()


# ===== 路由屏蔽（防委派环，2026-11-19 RecursionError 修复）=====

#: 路由委派期间置 True：委派目标的内部调用不再二次路由。
#: 为什么需要：委派目标可能是包装类（BYOKClient），其内部持有 BaseLLM 实例——
#: 该实例的 chat 同样处于 node_type scope 内，会再次命中路由并委派回同一个
#: 包装类 → 无限递归（生产实证：extract 路由到七牛云后同一毫秒刷出几十条
#: llm_routed，栈深耗尽 "maximum recursion depth exceeded"）。
_routing_suppressed: ContextVar[bool] = ContextVar("veripaper_routing_suppressed", default=False)


def suppress_routing() -> Any:
    """返回一个上下文管理器：块内所有路由解析返回 None（只允许委派一层）。"""
    return _suppress_flag(True)


@contextmanager
def _suppress_flag(value: bool) -> Iterator[None]:
    token = _routing_suppressed.set(value)
    try:
        yield
    finally:
        _routing_suppressed.reset(token)


def routing_suppressed() -> bool:
    return _routing_suppressed.get()
