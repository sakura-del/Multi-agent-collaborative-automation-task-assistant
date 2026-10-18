"""测试用固定适配器（Plan 5 Task 1）。

这些适配器是**测试代码**，不进入生产路径：不读环境变量、不访问网络、
不依赖全局单例，因此进程级 E2E 可以在任何机器上稳定复现。

LLM 部分复用生产模块 `app.llm.deterministic`（Plan 5 Task 3 起它同时服务于
Compose 冒烟演练），测试侧只保留历史类名与额外的固定嵌入/重排/事件记录器。

导出：
- `FixedLiteratureLLM`：确定性 LLM（= `app.llm.deterministic.DeterministicLLM`）
- `FixedEmbedding` / `FixedReranker`：确定性向量与重排
- `RecordingEventPublisher`：记录有序事件的内存发布器
"""

from __future__ import annotations

from typing import Any

from app.llm.deterministic import DeterministicLLM

# 历史类名（Plan 5 Task 1 的测试按这个名字导入）
FixedLiteratureLLM = DeterministicLLM

__all__ = [
    "DeterministicLLM",
    "FixedEmbedding",
    "FixedLiteratureLLM",
    "FixedReranker",
    "RecordingEventPublisher",
]


class FixedEmbedding:
    """确定性嵌入：同一文本永远得到同一向量（长度与字符数相关）。"""

    def __init__(self, dimension: int = 8) -> None:
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_one(text) for text in texts]

    def embed_one(self, text: str) -> list[float]:
        seed = sum(ord(char) for char in text) % 97
        return [((seed + index) % 13) / 13.0 for index in range(self._dimension)]


class FixedReranker:
    """确定性重排：保持输入顺序（检索质量由嵌入决定，不引入随机性）。"""

    def rerank(self, query: str, results: list[Any], top_k: int | None = None) -> list[Any]:
        limited = results[:top_k] if top_k else results
        return list(limited)


class RecordingEventPublisher:
    """记录所有已发布事件的内存发布器（顺序即发布顺序）。"""

    def __init__(self) -> None:
        import asyncio
        from uuid import UUID

        self.published: list[Any] = []
        self._subscribers: dict[UUID, set[asyncio.Queue[Any]]] = {}

    async def publish(self, event: Any) -> None:
        self.published.append(event)
        for queue in list(self._subscribers.get(event.task_id, ())):
            queue.put_nowait(event)

    async def subscribe(self, task_id: Any):
        import asyncio

        queue: asyncio.Queue[Any] = asyncio.Queue()
        subscribers = self._subscribers.setdefault(task_id, set())
        subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(task_id, None)

    def events_for(self, task_id: Any) -> list[Any]:
        return [event for event in self.published if event.task_id == task_id]
