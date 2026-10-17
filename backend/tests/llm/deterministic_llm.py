"""确定性 LLM 适配器：规划响应固定为合法三节点 TaskPlan（Plan 2 Task 7）。

用途：端到端测试不手工构造 DAG，而是让 PlannerAgent 通过真实 LLM 调用路径
（`BaseLLM.chat` → JSON 解析 → schema 校验 → `TaskPlan`）产出计划与 DAG。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from app.llm.base import BaseLLM
from app.llm.models import LLMResponse, Message, Usage

# 合法三节点 TaskPlan（search → extract → write）
PLAN_PAYLOAD: dict[str, Any] = {
    "version": 1,
    "topic": "检索增强生成证据核验",
    "research_questions": ["RAG 如何提升证据可靠性？"],
    "search_queries": ["RAG evidence verification"],
    "acceptance_criteria": ["引用可回溯"],
    "nodes": [
        {"id": "n1", "type": "search", "title": "检索文献", "dependencies": []},
        {"id": "n2", "type": "extract", "title": "提取证据", "dependencies": ["n1"]},
        {"id": "n3", "type": "write", "title": "撰写综述", "dependencies": ["n2"]},
    ],
}


class DeterministicPlanLLM(BaseLLM):
    """每次 chat 都返回同一份 TaskPlan JSON 的确定性适配器。"""

    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        *,
        model: str = "deterministic-plan",
        provider: str = "deterministic",
    ) -> None:
        super().__init__(
            model=model,
            provider=provider,
            api_key="deterministic-key",
            base_url="http://deterministic",
            timeout=1,
            max_retries=1,
            default_temperature=0.0,
            default_max_tokens=None,
        )
        self.payload = payload if payload is not None else PLAN_PAYLOAD
        self.call_count = 0
        self.call_history: list[dict[str, Any]] = []

    def _create_client(self) -> Any:
        return "deterministic-client"

    async def _raw_chat(
        self,
        messages: list[Message],
        *,
        tools: list[Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        self.call_count += 1
        self.call_history.append(
            {
                "messages": messages,
                "tools": tools,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "kwargs": kwargs,
            }
        )
        return LLMResponse(
            content=json.dumps(self.payload, ensure_ascii=False),
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
        yield json.dumps(self.payload, ensure_ascii=False)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0, 0.0, 0.0] for _ in texts]
