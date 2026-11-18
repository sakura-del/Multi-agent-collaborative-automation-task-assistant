"""全局 LLM 调用闸门（进程级并发上限）。

## 为什么需要

任务调度器按 DAG 层级并发（`TaskScheduler.max_concurrency` 默认 5），
且每个任务独立跑协程、**彼此之间没有共享闸门**。N 个任务同时运行
= 对同一把厂商 key 的瞬时请求数 N × 5。免费/低配额 key 必爆 429
（2026-11-18 生产实测：单任务 extract 层 3 并发即触发
`429 Too many requests for this free model`）。

闸门放在 `BaseLLM.chat()` 的**单次尝试**处，因为它是所有适配器实例的
唯一漏斗：平台模型、BYOK 自带模型、降级链全部经过这里，与任务数解耦。

## 语义

- 进程级、跨实例共享：同一进程内（一个 uvicorn worker）所有 LLM 客户端
  共用一个信号量；多 worker 部署时每个 worker 各自一份
  （进程隔离，上游总并发 = workers × max_concurrency）。
- 按**单次原始请求**（`_raw_chat`）持有槽位；重试退避期间不占坑，
  避免「睡着的请求占着坑」把吞吐拖死。
- 上限读 `settings.llm.max_concurrency`（默认 3），可用环境变量
  `LLM__MAX_CONCURRENCY` 覆盖。免费 key 建议设 1（完全串行）。
- 等待超过阈值（默认 10s）记一条 warning，便于区分「模型慢」与「在排队」。

## 已知边界

- 流式接口 `stream_chat()` **不走闸门**：目前没有 Agent 使用流式；
  若未来接入，需要按流生命周期单独设计（长连接占槽语义不同）。
- 心跳/探活（`testLLMCredential` 探测 /models）不经过 `chat()`，不占槽。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.core.logging import get_logger

logger = get_logger(__name__)

# 每个事件循环一个信号量 + 其创建时的上限值。
# 为什么按 loop 存：pytest 每个用例新建事件循环，模块级单例跨 loop 复用会抛
# "attached to a different loop"。生产只有一个 loop，等价于进程级单例。
_semaphores: dict[int, tuple[asyncio.Semaphore, int]] = {}

# 排队超过该秒数记 warning（可观测性：区分「上游慢」与「在本进程排队」）
_WAIT_WARN_SECONDS = 10.0


def _configured_limit() -> int:
    """读配置的并发上限；配置读取失败时保守回退到 1（宁可慢，不可打爆）。"""
    try:
        from app.core.config import get_settings

        return max(1, int(get_settings().llm.max_concurrency))
    except Exception:  # pragma: no cover - 配置损坏属于启动期就该暴露的问题
        return 1


def _semaphore_for_current_loop(limit: int) -> asyncio.Semaphore:
    """取当前事件循环对应的信号量；上限变化时重建（含旧等待者已排队的边界）。"""
    loop = asyncio.get_running_loop()
    key = id(loop)
    current = _semaphores.get(key)
    if current is None or current[1] != limit:
        semaphore = asyncio.Semaphore(limit)
        _semaphores[key] = (semaphore, limit)
        return semaphore
    return current[0]


@asynccontextmanager
async def llm_slot(*, provider: str = "", model: str = "") -> AsyncIterator[None]:
    """获取一个全局 LLM 调用槽位。

    用法::

        async with llm_slot(provider=self.provider, model=self.model):
            response = await self._raw_chat(...)
    """
    limit = _configured_limit()
    semaphore = _semaphore_for_current_loop(limit)
    started = time.monotonic()
    await semaphore.acquire()
    waited = time.monotonic() - started
    try:
        if waited >= _WAIT_WARN_SECONDS:
            logger.warning(
                "llm_throttle_waited",
                provider=provider,
                model=model,
                waited_seconds=round(waited, 2),
                limit=limit,
            )
        yield
    finally:
        semaphore.release()


def reset_for_tests() -> None:
    """清空按循环缓存的信号量（仅供测试夹具使用）。"""
    _semaphores.clear()
