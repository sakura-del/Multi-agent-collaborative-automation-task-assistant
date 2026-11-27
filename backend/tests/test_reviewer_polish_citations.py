"""复盘 LLM 润色章节的引用编号必须与最终参考文献对齐（Issue #25 闸门回归）。

生产事故（任务 b79d0e43，2026-11-27）：
- 两个 write 节点各自从 [E1] 起编号；复盘把它们重写成全局编号后，正文只剩
  [E1]、[E2]（两份文档去重后 bibliography 2 条）；
- 但 `_polish_literature_review` 把**原始节点内容**（含节点局部 [E3]/[E4]）喂给
  LLM，LLM 在摘要/局限性里复述了 [E3]、[E4]；
- 润色结果原样进 final_markdown → 落库前闸门审计报「[E3]、[E4] 在参考文献中不存在」
  → 任务两次失败。

修复口径：
1. 送给 LLM 的节点内容先做**全局编号重写**，让它看到的编号与最终正文一致；
2. LLM 返回的摘要/局限性再按最终 bibliography **兜底过滤**：只保留真实存在的
   编号，其余标记剔除（局部编号对 LLM 只是噪声）。
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from typing import Any

from app.orchestration.agents.reviewer import ReviewerAgent
from app.orchestration.validation.citation_consistency import audit_citation_alignment

DOC_A = "11111111-1111-1111-1111-111111111111"
DOC_B = "22222222-2222-2222-2222-222222222222"


def _evidence(doc_id: str, title: str) -> dict[str, Any]:
    return {
        "doc_id": doc_id,
        "doc_title": title,
        "excerpt": f"{title} 的原文片段",
        "kind": "excerpt",
    }


def _literature_input() -> dict[str, Any]:
    """两个 write 节点：证据都是同一对知识库文档，局部编号 1..2 / 3..4。"""
    return {
        "trace_id": "trace-polish",
        "topic": "多智能体协作的任务编排",
        "results": [
            {
                "node_id": "n5",
                "node_type": "write",
                "content": "## 任务分配机制\n\n甲观点 [E1]，乙观点 [E2]。",
                "evidence": [_evidence(DOC_A, "文献甲"), _evidence(DOC_B, "文献乙")],
                "cited_indices": [1, 2],
                "duration_ms": 10,
                "prompt_tokens": 10,
                "completion_tokens": 5,
            },
            {
                "node_id": "n6",
                "node_type": "write",
                "content": "## 协同编排策略\n\n丙观点 [E3]，丁观点 [E4]。",
                "evidence": [_evidence(DOC_A, "文献甲"), _evidence(DOC_B, "文献乙")],
                "cited_indices": [3, 4],
                "duration_ms": 11,
                "prompt_tokens": 10,
                "completion_tokens": 5,
            },
        ],
        "validations": [],
        "event_stats": {"event_count": 5},
    }


class _FakePolishLLM:
    """返回固定润色结论，并记录收到的 prompt，便于断言输入已全局编号。"""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.prompts: list[str] = []

    async def chat(self, messages: list[Any], **kwargs: Any) -> SimpleNamespace:
        self.prompts.append("\n".join(str(getattr(message, "content", "")) for message in messages))
        return SimpleNamespace(content=json.dumps(self.payload, ensure_ascii=False))


def _section(markdown: str, heading: str) -> str:
    return markdown.split(heading, 1)[1].split("\n## ", 1)[0]


async def test_polished_sections_do_not_leak_node_local_citations() -> None:
    """LLM 摘要/局限性里的局部编号不得进入最终正文，闸门审计必须通过。"""
    llm = _FakePolishLLM(
        {
            "abstract": "综上，甲与乙的结论一致 [E1]，丙与丁的差异 [E3]。",
            "limitations": ["仅覆盖两篇知网文献 [E4]，样本有限 [E9]。"],
        }
    )
    report = await ReviewerAgent(llm=llm).execute(_literature_input())

    markdown = report["final_markdown"]
    bibliography = report["bibliography"]

    # 输入侧：LLM 看到的节点内容已是全局编号，局部 [E3]/[E4] 不应出现
    assert llm.prompts, "润色 LLM 未被调用"
    prompt = llm.prompts[0]
    assert "[E3]" not in prompt
    assert "[E4]" not in prompt

    # 输出侧：合法全局编号保留，悬空编号被剔除
    abstract = _section(markdown, "## 摘要")
    limitations = _section(markdown, "## 局限性")
    assert "[E1]" in abstract
    assert "[E3]" not in abstract
    assert "[E4]" not in limitations
    assert "[E9]" not in limitations

    audit = audit_citation_alignment(markdown, bibliography)
    assert audit.ok, audit.reason
    assert audit.cited == (1, 2)


async def test_polished_unknown_citations_removed_without_touching_body() -> None:
    """润色输出里的悬空编号只影响摘要/局限性，成稿正文的合法引用保持原样。"""
    llm = _FakePolishLLM(
        {"abstract": "摘要 [E7]。", "limitations": ["局限 [E8]。"]},
    )
    report = await ReviewerAgent(llm=llm).execute(_literature_input())

    markdown = report["final_markdown"]
    body = markdown.split("## 参考文献", 1)[0]
    assert "[E7]" not in body
    assert "[E8]" not in body
    assert "[E1]" in body and "[E2]" in body
    assert audit_citation_alignment(markdown, report["bibliography"]).ok
    # 剔除后不应残留多余空格造成可读性退化
    abstract = _section(markdown, "## 摘要")
    assert not re.search(r"\s{2,}", abstract.strip())
