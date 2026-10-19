"""校验重试语义（Issue #6 第二处成因）：可修复的失败必须重试，而不是直接转人工。

背景（服务器实测，任务 `be0b5553`）：write 节点（作者正文）确定性检查全部通过、
规则通过，**只有 LLM 语义自检判不通过**，但报告里 `retryable=False` →
调度器不重试、直接 `waiting_human`。一次有噪声的 LLM 判断就能让整篇综述失败。

语义失败是「模型可以重写修好」的失败：重试路径会把 `feedback` 作为 `validation_feedback`
交给重新生成的节点，校验器本身也产出了 `suggestions`。因此它必须可重试。
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest

from app.knowledge.services.citation_validator import CitationValidator
from app.llm.models import LLMResponse, MessageRole, Usage
from app.orchestration.agents.validator import ValidatorAgent
from tests.llm.mock_llm import MockLLM

CONTENT = "摘要\n综述 [E1]。\n\n方法\n比较 [E2]。\n\n结论\n证据绑定 [E1][E2]。"
SECTIONS = ["摘要", "方法", "结论"]


def _evidence_payload() -> list[dict[str, Any]]:
    return [
        {"doc_id": str(uuid4()), "chunk_id": str(uuid4()), "excerpt": "证据一"},
        {"doc_id": str(uuid4()), "chunk_id": str(uuid4()), "excerpt": "证据二"},
    ]


def _semantic(payload: dict[str, Any]) -> LLMResponse:
    return LLMResponse(
        content=json.dumps(payload),
        finish_reason="stop",
        usage=Usage(),
        role=MessageRole.ASSISTANT,
    )


def _literature_payload(evidence: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "node_id": "n4",
        "title": "撰写综述",
        "result": CONTENT,
        "execution_result": {
            "node_id": "n4",
            "content": CONTENT,
            "evidence": evidence,
            "duration_ms": 1,
        },
        "evidence_pool": evidence,
        "content_role": "authored",
        "trace_id": "t-issue-6-retry",
    }


class TestSemanticFailureIsRetryable:
    async def test_failed_semantic_judgement_is_retryable(self) -> None:
        """Issue #6 主回归：语义判定失败必须可重试（原先直接转人工）。"""
        evidence = _evidence_payload()
        llm = MockLLM(
            responses=[
                _semantic(
                    {
                        "logic_consistent": False,
                        "factual_accurate": True,
                        "issues": ["现有材料不足以支持该结论"],
                        "suggestions": ["删除或弱化该结论"],
                    }
                )
            ]
        )
        agent = ValidatorAgent(
            rules={"min_length": 1, "required_keywords": []},
            citation_validator=CitationValidator(),
            required_sections=SECTIONS,
            llm=llm,
        )

        report = await agent.execute(_literature_payload(evidence))

        assert report["passed"] is False
        assert report["retryable"] is True
        # 重试必须带着可执行的反馈（否则重生成等于盲猜）
        assert "现有材料不足以支持该结论" in report["feedback"]

    async def test_semantic_pass_stays_non_retryable(self) -> None:
        evidence = _evidence_payload()
        llm = MockLLM(
            responses=[
                _semantic(
                    {
                        "logic_consistent": True,
                        "factual_accurate": True,
                        "issues": [],
                        "suggestions": [],
                    }
                )
            ]
        )
        agent = ValidatorAgent(
            rules={"min_length": 1, "required_keywords": []},
            citation_validator=CitationValidator(),
            required_sections=SECTIONS,
            llm=llm,
        )

        report = await agent.execute(_literature_payload(evidence))

        assert report["passed"] is True
        assert report["retryable"] is False

    async def test_integrity_failure_stays_non_retryable(self) -> None:
        """守卫：跨用户证据这类完整性失败不得变成可重试（重试没有意义）。"""
        evidence = _evidence_payload()
        llm = MockLLM(responses=[_semantic({"logic_consistent": True})])
        agent = ValidatorAgent(
            rules={"min_length": 1, "required_keywords": []},
            citation_validator=CitationValidator(ownership_violation=True),
            required_sections=SECTIONS,
            llm=llm,
        )

        report = await agent.execute(_literature_payload(evidence))

        assert report["passed"] is False
        assert report["retryable"] is False
        assert llm.call_count == 0

    async def test_deterministic_retryable_failure_still_runs_semantic(self) -> None:
        """确定性可重试失败（标记不可解析）仍会跑语义校验，两类反馈一起给重生成。"""
        evidence = _evidence_payload()
        payload = _literature_payload(evidence)
        payload["result"] = CONTENT + " 额外断言 [E9]。"
        payload["execution_result"]["content"] = payload["result"]
        llm = MockLLM(responses=[_semantic({"logic_consistent": True, "factual_accurate": True})])
        agent = ValidatorAgent(
            rules={"min_length": 1, "required_keywords": []},
            citation_validator=CitationValidator(),
            required_sections=SECTIONS,
            llm=llm,
        )

        report = await agent.execute(payload)

        assert report["passed"] is False
        assert report["retryable"] is True
        assert llm.call_count == 1


@pytest.mark.asyncio
class TestLegacyPathRetrySemantics:
    """非文献路径（未注入 citation_validator）保持同一语义。"""

    async def test_legacy_semantic_failure_is_retryable(self) -> None:
        llm = MockLLM(
            responses=[
                _semantic(
                    {
                        "logic_consistent": False,
                        "factual_accurate": False,
                        "issues": ["结论自相矛盾"],
                        "suggestions": ["重写结论"],
                    }
                )
            ]
        )
        agent = ValidatorAgent(rules={"min_length": 1, "required_keywords": []}, llm=llm)

        report = await agent.execute(
            {
                "node_id": "n1",
                "title": "任务",
                "result": "一段足够长的内容用于校验。",
                "trace_id": "t",
            }
        )

        assert report["passed"] is False
        assert report["retryable"] is True

    async def test_legacy_rule_failure_is_retryable(self) -> None:
        llm = MockLLM(responses=[_semantic({"logic_consistent": True, "factual_accurate": True})])
        agent = ValidatorAgent(rules={"min_length": 500}, llm=llm)

        report = await agent.execute(
            {"node_id": "n1", "title": "任务", "result": "太短", "trace_id": "t"}
        )

        assert report["passed"] is False
        assert report["retryable"] is True
