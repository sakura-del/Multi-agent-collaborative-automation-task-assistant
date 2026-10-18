"""固定适配器：让进程级 E2E 完全确定（Plan 5 Task 1）。

设计要点：
- 不读环境变量、不访问网络、不依赖全局单例，测试可重复运行且结果一致；
- `FixedLiteratureLLM` 按 **system prompt 的用途** 分派响应，因此规划/执行/语义校验/复盘
  四条真实调用路径都会被覆盖，而不是绕过 Agent 直接塞结果；
- 执行类响应会引用 prompt 中真实存在的 `[E{n}]` 标记，保证引用可回溯断言有意义。
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from app.llm.base import BaseLLM
from app.llm.models import LLMResponse, Message, MessageRole, Usage
from app.orchestration.contracts import TaskEvent
from app.orchestration.events import EventPublisher

# ===== 固定载荷 =====

# 合法文献契约规划：search → extract → write → validate → review
PLAN_PAYLOAD: dict[str, Any] = {
    "version": 1,
    "topic": "检索增强生成证据核验",
    "research_questions": [
        "检索增强生成如何提升证据可靠性？",
        "如何度量引用可追溯性？",
        "多智能体协作对综述质量有何影响？",
    ],
    "search_queries": ["retrieval augmented generation evidence"],
    "acceptance_criteria": ["每条形式化结论都能回溯到上传文档的分块"],
    "nodes": [
        {"id": "n1", "type": "search", "title": "检索候选文献", "dependencies": []},
        {"id": "n2", "type": "extract", "title": "提取证据", "dependencies": ["n1"]},
        {"id": "n3", "type": "write", "title": "撰写综述", "dependencies": ["n2"]},
        {"id": "n4", "type": "validate", "title": "校验引用", "dependencies": ["n3"]},
        {"id": "n5", "type": "review", "title": "汇总复盘", "dependencies": ["n4"]},
    ],
}

SEMANTIC_PAYLOAD: dict[str, Any] = {
    "logic_consistent": True,
    "factual_accurate": True,
    "score": 90,
    "issues": [],
    "suggestions": [],
}

REVIEWER_PAYLOAD: dict[str, Any] = {
    "title": "检索增强生成证据核验综述",
    "abstract": "本文基于上传文档的证据分块，梳理检索增强生成的评估要点。",
    "limitations": "仅覆盖离线语料，未做跨语言验证。",
}

MARKER_RE = re.compile(r"\[E(\d+)\]")
EVIDENCE_LINE_RE = re.compile(r"\[E(\d+)\] doc=(\S+) chunk=(\S+)")


def _system_prompt(messages: list[Message]) -> str:
    for message in messages:
        if message.role is MessageRole.SYSTEM:
            return str(message.content)
    return ""


def _user_prompt(messages: list[Message]) -> str:
    for message in messages:
        if message.role is MessageRole.USER:
            return str(message.content)
    return ""


class FixedLiteratureLLM(BaseLLM):
    """按 system prompt 用途返回固定响应的确定性 LLM。"""

    def __init__(self) -> None:
        super().__init__(
            model="fixed-literature",
            provider="fixed",
            api_key="fixed-key",
            base_url="http://fixed.invalid",
            timeout=1,
            max_retries=1,
            default_temperature=0.0,
            default_max_tokens=None,
        )
        self.calls: list[dict[str, Any]] = []

    def _create_client(self) -> Any:
        return "fixed-client"

    # ===== 响应分派 =====

    def _respond(self, messages: list[Message]) -> str:
        system = _system_prompt(messages)
        user = _user_prompt(messages)

        if "规划专家" in system:
            return json.dumps(PLAN_PAYLOAD, ensure_ascii=False)
        if "复盘专家" in system:
            return json.dumps(REVIEWER_PAYLOAD, ensure_ascii=False)
        if "质量校验专家" in system:
            return json.dumps(SEMANTIC_PAYLOAD, ensure_ascii=False)
        # 执行节点：引用 prompt 里出现的证据标记（至少第一条）
        return self._executor_content(user)

    @staticmethod
    def _executor_content(user_prompt: str) -> str:
        """生成带可解析引用标记的正文。

        只从**证据行**（`[E{n}] doc=… chunk=…`）取编号：依赖输出里也可能出现
        `[E{n}]`，若一并采用会生成越界标记（执行器会拒绝该输出）。
        """
        evidence_numbers = sorted(
            {number for number, _doc, _chunk in EVIDENCE_LINE_RE.findall(user_prompt)}
        )
        if not evidence_numbers:
            evidence_numbers = sorted({int(number) for number in MARKER_RE.findall(user_prompt)})
        if evidence_numbers:
            citations = "".join(f"[E{number}]" for number in evidence_numbers[:3])
            return (
                "## 证据梳理\n"
                f"上传文档指出检索增强生成通过外部知识降低事实性错误{citations}。\n\n"
                "## 评估要点\n"
                f"忠实度与上下文相关性是核心指标{citations}。\n"
            )
        return "## 证据梳理\n当前没有可用检索证据，以下内容标注为模型推断。\n"

    async def _raw_chat(
        self,
        messages: list[Message],
        *,
        tools: list[Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        content = self._respond(messages)
        self.calls.append(
            {
                "system": _system_prompt(messages),
                "user": _user_prompt(messages),
                "temperature": temperature,
                "max_tokens": max_tokens,
                "content": content,
            }
        )
        return LLMResponse(
            content=content,
            usage=Usage(prompt_tokens=20, completion_tokens=40),
            finish_reason="stop",
            model=self.model,
        )

    async def stream_chat(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        yield self._respond(messages)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text) % 7), 0.0, 1.0] for text in texts]


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


class RecordingEventPublisher(EventPublisher):
    """记录所有已发布事件的内存发布器（顺序即发布顺序）。"""

    def __init__(self) -> None:
        self.published: list[TaskEvent] = []
        self._subscribers: dict[UUID, set[asyncio.Queue[TaskEvent]]] = {}

    async def publish(self, event: TaskEvent) -> None:
        self.published.append(event)
        for queue in list(self._subscribers.get(event.task_id, ())):
            queue.put_nowait(event)

    async def subscribe(self, task_id: UUID) -> AsyncIterator[TaskEvent]:
        queue: asyncio.Queue[TaskEvent] = asyncio.Queue()
        subscribers = self._subscribers.setdefault(task_id, set())
        subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(task_id, None)

    def events_for(self, task_id: UUID) -> list[TaskEvent]:
        return [event for event in self.published if event.task_id == task_id]

    @staticmethod
    def evidence_markers(text: str) -> list[int]:
        return [int(number) for number in MARKER_RE.findall(text or "")]

    @staticmethod
    def evidence_lines(text: str) -> list[tuple[int, str, str]]:
        return [
            (int(number), doc_id, chunk_id)
            for number, doc_id, chunk_id in EVIDENCE_LINE_RE.findall(text or "")
        ]
