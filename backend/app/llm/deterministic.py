"""确定性 LLM 适配器：离线演练 / 冒烟 / 回归测试专用（Plan 5 Task 3）。

用途：在没有真实模型密钥的环境里跑通完整链路（规划 → 证据约束执行 → 校验 → 复盘），
并保持**完全可复现**——同一输入永远得到同一输出，便于对比两次运行的产物哈希。

启用方式（显式，不存在误用风险）：`LLM__DEFAULT_PROVIDER=deterministic`。

设计要点：
- 按 system prompt 的**用途**分派四类响应（规划 / 执行 / 语义校验 / 复盘），
  因此走的是与真实模型完全相同的 Agent 调用路径，而不是绕过 Agent 塞结果；
- 执行类响应只引用 prompt 中**证据行**出现的 `[E{n}]`，保证引用可回溯且不越界；
- 不读环境变量、不访问网络。
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any

from app.llm.base import BaseLLM
from app.llm.models import LLMResponse, Message, MessageRole, Usage

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


def executor_content(user_prompt: str) -> str:
    """生成带可解析引用标记的正文。

    只从**证据行**（`[E{n}] doc=… chunk=…`）取编号：依赖输出里也可能出现 `[E{n}]`，
    若一并采用会生成越界标记（执行器会拒绝该输出）。
    """
    evidence_numbers = sorted(
        {int(number) for number, _doc, _chunk in EVIDENCE_LINE_RE.findall(user_prompt)}
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


def render_response(messages: list[Message]) -> str:
    """按 system prompt 用途返回固定响应。"""
    system = _system_prompt(messages)
    if "规划专家" in system:
        return json.dumps(PLAN_PAYLOAD, ensure_ascii=False)
    if "复盘专家" in system:
        return json.dumps(REVIEWER_PAYLOAD, ensure_ascii=False)
    if "质量校验专家" in system:
        return json.dumps(SEMANTIC_PAYLOAD, ensure_ascii=False)
    return executor_content(_user_prompt(messages))


class DeterministicLLM(BaseLLM):
    """确定性适配器：按用途分派固定响应，记录每次调用。"""

    def __init__(
        self,
        *,
        model: str = "deterministic",
        provider: str = "deterministic",
    ) -> None:
        super().__init__(
            model=model,
            provider=provider,
            api_key="deterministic-key",
            base_url="http://deterministic.invalid",
            timeout=1,
            max_retries=1,
            default_temperature=0.0,
            default_max_tokens=None,
        )
        self.calls: list[dict[str, Any]] = []

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
        content = render_response(messages)
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
        yield render_response(messages)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text) % 7), 0.0, 1.0] for text in texts]
