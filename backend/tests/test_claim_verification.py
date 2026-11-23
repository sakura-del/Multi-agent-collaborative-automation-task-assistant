from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.orchestration.agents.validator import ValidatorAgent
from app.orchestration.contracts import ClaimCheckResult, Evidence, ExecutionResult
from app.orchestration.validation.citation_consistency import CitedClaim, extract_cited_claims
from app.orchestration.validation.claim_verification import (
    ClaimVerifier,
    build_claim_evidence,
    summarize_claim_checks,
)


def _claim(
    claim_id: str = "c1",
    refs: tuple[int, ...] = (1,),
    sentence: str = "该方法显著优于基线 [E1]。",
) -> CitedClaim:
    return CitedClaim(claim_id=claim_id, section="结果", sentence_text=sentence, cited_ref_ids=refs)


def _excerpt(text: str, *, chunk: bool = True) -> Evidence:
    return Evidence(
        kind="excerpt",
        chunk_id=uuid.uuid4() if chunk else None,
        excerpt=text,
    )


def _metadata(text: str) -> Evidence:
    return Evidence(kind="metadata", excerpt=text)


class _ScriptedLLM:
    """按请求里的 claim_id 回 JSON 数组；可指定 verdict/quote 以测清洗逻辑。"""

    def __init__(
        self,
        *,
        verdict: str = "supported",
        quote: str | None = None,
        reply: str | None = None,
    ) -> None:
        self.verdict = verdict
        self.quote = quote
        self.reply = reply
        self.calls: list[list[Any]] = []

    async def chat(self, messages: list[Any], **kwargs: Any) -> Any:
        self.calls.append(messages)
        if self.reply is not None:
            return SimpleNamespace(content=self.reply)

        payload = json.loads(messages[-1].content)
        results = []
        for claim in payload["claims"]:
            passages = claim.get("passages") or []
            quote = self.quote
            if quote is None and passages:
                quote = passages[0][:20]
            results.append(
                {
                    "claim_id": claim["claim_id"],
                    "verdict": self.verdict,
                    "confidence": 0.8,
                    "evidence_quote": quote,
                    "rationale": "证据与论断一致",
                    "suggested_fix": "删掉过度措辞",
                }
            )
        return SimpleNamespace(content=json.dumps(results, ensure_ascii=False))


class TestExtractCitedClaims:
    def test_splits_sentences_and_tracks_sections(self) -> None:
        markdown = (
            "# 引言\n"
            "已有研究指出方法有效 [E1]。另一项工作给出反例 [E2][E3]。\n"
            "```python\n"
            'print("[E9]")\n'
            "```\n"
            "- [1] 条目一 [E1]\n"
            "## 方法\n"
            "样本量 120 [E2]；结论待复核 [E3]。\n"
        )

        claims = extract_cited_claims(markdown, claim_prefix="n2:")

        assert [claim.claim_id for claim in claims] == ["n2:c1", "n2:c2", "n2:c3", "n2:c4"]
        assert [claim.section for claim in claims] == ["引言", "引言", "方法", "方法"]
        assert claims[1].cited_ref_ids == (2, 3)
        assert "[E9]" not in "".join(claim.sentence_text for claim in claims)

    def test_ignores_inline_code_and_plain_text(self) -> None:
        markdown = "`[E4]` 只是代码；这句话没有引用。\n"
        assert extract_cited_claims(markdown) == []

    def test_returns_empty_for_empty_markdown(self) -> None:
        assert extract_cited_claims("") == []
        assert extract_cited_claims(None) == []


class TestBuildClaimEvidence:
    def test_metadata_only_scope_is_abstract_only(self) -> None:
        item = build_claim_evidence(_claim(), [_metadata("[E1] 摘要：观察到改善。")])

        assert item.scope == "abstract_only"
        assert item.passages == ("[E1] 摘要：观察到改善。",)

    def test_chunk_excerpt_scope_is_full_text(self) -> None:
        item = build_claim_evidence(_claim(), [_excerpt("原文：改善显著 (p<0.05)。")])
        assert item.scope == "full_text"

    def test_cited_indices_map_local_numbers(self) -> None:
        """执行器的 cited_indices 是「证据 i 对应局部编号」；核验必须按它取片段。"""
        evidence = [_metadata("第一份证据"), _excerpt("第二份证据")]
        item = build_claim_evidence(_claim(refs=(2,)), evidence, cited_indices=(2, 1))

        assert item.passages == ("第一份证据",)

    def test_passage_is_truncated_to_budget(self) -> None:
        long_text = "证据" * 400
        item = build_claim_evidence(_claim(), [_excerpt(long_text)])

        assert len(item.passages) == 1
        assert len(item.passages[0]) <= 600
        assert item.passages[0].endswith("…")

    def test_unmatched_reference_yields_no_passage(self) -> None:
        item = build_claim_evidence(_claim(refs=(3,)), [_excerpt("只有一条证据")])
        assert item.passages == ()
        assert item.scope == "abstract_only"


class TestClaimVerifier:
    async def test_verifies_and_sanitizes_supported(self) -> None:
        item = build_claim_evidence(_claim(), [_excerpt("该方法显著优于基线。")])
        verifier = ClaimVerifier(_ScriptedLLM())  # type: ignore[arg-type]

        [check] = await verifier.verify([item])

        assert check.claim_id == "c1"
        assert check.verdict == "supported"
        assert check.confidence == pytest.approx(0.8)
        assert check.suggested_fix is None  # supported 不需要修改建议
        assert check.cited_ref_ids == ["E1"]
        assert check.evidence_scope == "full_text"
        assert check.evidence_quote is not None

    async def test_abstract_only_never_contradicted(self) -> None:
        """只有摘要时「矛盾/曲解」不成立：摘要没提 ≠ 论文没做。"""
        item = build_claim_evidence(_claim(), [_metadata("摘要：只提到方法，没有数字。")])
        verifier = ClaimVerifier(_ScriptedLLM(verdict="contradicted"))  # type: ignore[arg-type]

        [check] = await verifier.verify([item])

        assert check.verdict == "unverifiable"
        assert check.evidence_scope == "abstract_only"

    async def test_quote_must_come_from_passages(self) -> None:
        item = build_claim_evidence(_claim(), [_excerpt("原文片段。")])
        verifier = ClaimVerifier(_ScriptedLLM(quote="这句是模型编的"))  # type: ignore[arg-type]

        [check] = await verifier.verify([item])

        assert check.evidence_quote is None

    async def test_batches_by_six_and_caches_results(self) -> None:
        items = [
            build_claim_evidence(
                _claim(claim_id=f"c{i}", sentence=f"第 {i} 条论断 [E1]。"),
                [_excerpt(f"第 {i} 条证据。")],
            )
            for i in range(1, 8)
        ]
        llm = _ScriptedLLM()
        verifier = ClaimVerifier(llm)  # type: ignore[arg-type]

        first = await verifier.verify(items)
        calls_after_first = len(llm.calls)
        second = await verifier.verify(items)

        assert len(first) == 7
        assert calls_after_first == 2  # 6 + 1
        assert len(llm.calls) == calls_after_first  # 第二次全部命中缓存
        assert [item.claim_id for item in second] == [f"c{i}" for i in range(1, 8)]
        batch_ids = [
            message["claim_id"] for call in llm.calls for message in _payload(call)["claims"]
        ]
        assert sorted(batch_ids) == sorted(f"c{i}" for i in range(1, 8))
        assert sorted(len(_payload(call)["claims"]) for call in llm.calls) == [1, 6]

    async def test_unparseable_reply_degrades_without_cache(self) -> None:
        items = [build_claim_evidence(_claim(), [_excerpt("证据。")])]
        llm = _ScriptedLLM(reply="抱歉，我无法判断")
        verifier = ClaimVerifier(llm)  # type: ignore[arg-type]

        [check] = await verifier.verify(items)
        assert check.verdict == "unverifiable"
        assert check.suggested_fix is None

        await verifier.verify(items)
        assert len(llm.calls) == 2  # 失败不缓存，下次重试

    def test_summarize_counts_by_verdict(self) -> None:
        checks = []
        for index, verdict in enumerate(
            ["supported", "supported", "overstated", "unverifiable"], start=1
        ):
            checks.append(
                {
                    "claim_id": f"c{index}",
                    "sentence_text": f"第 {index} 条论断 [E1]。",
                    "cited_ref_ids": ["E1"],
                    "verdict": verdict,
                    "confidence": 0.5,
                    "rationale": "测试",
                }
            )
        results = [ClaimCheckResult.model_validate(item) for item in checks]

        stats = summarize_claim_checks(results)

        assert stats["total"] == 4
        assert stats["supported"] == 2
        assert stats["flagged"] == 2
        assert stats["supported_rate"] == pytest.approx(0.5)
        assert stats["by_verdict"] == {"supported": 2, "overstated": 1, "unverifiable": 1}


def _payload(messages: list[Any]) -> dict[str, Any]:
    return json.loads(messages[-1].content)


class TestValidatorClaimGate:
    def test_only_authored_nodes_are_verified(self) -> None:
        authored = {"execution_result": {"node_type": "write"}}
        review = {"execution_result": {"node_type": "review"}}
        aggregated = {"execution_result": {"content_role": "aggregated"}}

        assert ValidatorAgent._is_authored(authored, content_role="authored") is True
        assert ValidatorAgent._is_authored(review, content_role="authored") is False
        assert ValidatorAgent._is_authored(aggregated, content_role="authored") is False
        # 历史负载没有 node_type：不猜，跳过核验（宁可少核一次也不重复扣费）
        assert ValidatorAgent._is_authored({}, content_role="authored") is False
        explicit = {"execution_result": {"content_role": "authored"}}
        assert ValidatorAgent._is_authored(explicit, content_role="aggregated") is True

    async def test_run_claim_checks_uses_prefixed_claim_ids(self) -> None:
        llm = _ScriptedLLM()
        agent = ValidatorAgent(llm=llm)  # type: ignore[arg-type]
        execution = ExecutionResult(
            node_id="n2",
            content="该方法显著优于基线 [E1]。",
            evidence=[_excerpt("该方法显著优于基线。")],
            cited_indices=[1],
            node_type="write",
            content_role="authored",
            duration_ms=0,
        )
        payload = execution.model_dump(mode="json")

        checks = await agent._run_claim_checks(
            {
                "node_id": "n2",
                "execution_result": payload,
                "evidence_pool": [item.model_dump(mode="json") for item in execution.evidence],
            }
        )

        assert [check.claim_id for check in checks] == ["n2:c1"]
        assert checks[0].verdict == "supported"

    def test_evidence_pool_skips_invalid_entries(self) -> None:
        pool = [
            _excerpt("有效证据").model_dump(mode="json"),
            {"excerpt": "缺少必备字段?"},
            "not-a-dict",
        ]
        parsed = ValidatorAgent._evidence_pool({"evidence_pool": pool})
        # 缺可选字段的 Evidence 仍合法；非 dict / 字段类型错的条目被跳过
        assert len(parsed) == 2
