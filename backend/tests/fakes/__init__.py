"""测试用固定适配器（Plan 5 Task 1）。

这些适配器是**测试代码**，不进入生产路径：不读环境变量、不访问网络、
不依赖全局单例，因此进程级 E2E 可以在任何机器上稳定复现。

导出：
- `FixedLiteratureLLM`：按 system prompt 用途返回固定响应的 LLM
- `FixedEmbedding` / `FixedReranker`：确定性向量与重排
- `RecordingEventPublisher`：记录有序事件的内存发布器
"""

from tests.fakes.runtime import (
    FixedEmbedding,
    FixedLiteratureLLM,
    FixedReranker,
    RecordingEventPublisher,
)

__all__ = [
    "FixedEmbedding",
    "FixedLiteratureLLM",
    "FixedReranker",
    "RecordingEventPublisher",
]
