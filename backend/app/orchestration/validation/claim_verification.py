"""论断级核验（Issue #31 Phase 2）。

把综述正文里「带 ``[E#]`` 引用的句子」按句抽出来，绑定到对应证据片段，
再批量交给 LLM 判定：支持 / 弱支持 / 夸大 / 曲解 / 矛盾 / 无法核验。

设计约束（都与真实踩坑有关）：

- **只有题录/摘要时不能判「矛盾」**：``metadata`` 证据不含原文，
  「摘要里没提」不等于「论文没做」——必须落到 ``unverifiable``；
- **不缓存失败**：模型超时/输出不可解析时给 ``unverifiable`` 但不写缓存，
  下一次局部重跑还能重新核验；
- **引用编号是局部的**：节点内 ``[E3]`` 只在该节点的证据列表里有意义，
  ``claim_id`` 带 ``{node_id}:`` 前缀，复盘阶段再重写成全局编号；
- **缓存键含证据内容**：同一句话换了证据（重新检索）必须重判。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from app.core.logging import get_logger
from app.llm.models import Message, MessageRole
from app.orchestration.contracts import ClaimCheckResult, Evidence
from app.orchestration.validation.citation_consistency import CitedClaim

if TYPE_CHECKING:  # pragma: no cover - 仅类型引用
    from app.llm.base import BaseLLM

logger = get_logger(__name__)

#: 每批送给模型的论断数：批量太小浪费往返，太大则单次输出容易截断
DEFAULT_BATCH_SIZE = 6
#: 同时在跑的批次数
DEFAULT_MAX_CONCURRENCY = 5
#: 单条论断随 prompt 携带的证据上限（字符）
MAX_EVIDENCE_CHARS_PER_CLAIM = 1200
#: 单条论断最多带几段证据（与 spec 的 Top-3 一致）
MAX_PASSAGES_PER_CLAIM = 3
#: 单个证据片段的截断长度
MAX_PASSAGE_CHARS = 600

_VALID_VERDICTS = {
    "supported",
    "weakly_supported",
    "overstated",
    "misrepresented",
    "contradicted",
    "unverifiable",
}

_SYSTEM_PROMPT = """你是学术综述的事实核验员。你会收到若干条带引用的论断，以及这些引用对应的原文证据片段。
请逐条判定论断与证据的关系，并只输出 JSON 数组。

判定口径：
- supported：证据直接、明确地支持该论断。
- weakly_supported：证据只支持一部分，或需要额外推理才能成立。
- overstated：证据方向一致，但论断把结论说得更强/更普适（如「显著改善」而证据只说「观察到改善」）。
- misrepresented：证据讨论的是别的问题、对象或条件，被拿来支撑该论断（引用错配）。
- contradicted：证据与论断方向相反。
- unverifiable：证据不足以判断，或证据里根本没有论断所提的细节。

硬规则：
1. 当 scope 为 abstract_only（只有题录/摘要、没有全文）时，摘要未提及该细节只能判 unverifiable，不得判 contradicted 或 misrepresented。
2. evidence_quote 必须是所给证据片段中的一个**原样子串**（可以跨句，不要改写、不要加省略号）；找不到就填 null。
3. rationale 用简洁中文说明依据（不超过 80 字）；suggested_fix 在非 supported 时给出可执行的修改建议，supported 时为 null。
4. confidence 是 0–1 的小数，表示你对该判定的把握。
5. 输出数组元素形如：
{"claim_id": "n2:c1", "verdict": "supported", "confidence": 0.9, "evidence_quote": "原文子串", "rationale": "证据直接给出该结论", "suggested_fix": null}
不要输出 JSON 之外的任何文字。"""


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize_ws(text: str) -> str:
    """折叠空白，供「引文是否真的来自证据」的包含判断使用。"""
    return re.sub(r"\s+", " ", text).strip()


@dataclass(frozen=True)
class ClaimEvidence:
    """一条论断 + 它绑定的证据片段（送模型的最小单元）。"""

    claim: CitedClaim
    passages: tuple[str, ...]
    scope: Literal["full_text", "abstract_only"]

    @property
    def cache_key(self) -> str:
        payload = json.dumps(
            {
                "claim_id": self.claim.claim_id,
                "sentence": self.claim.sentence_text,
                "refs": list(self.claim.cited_ref_ids),
                "scope": self.scope,
                "passages": [_digest(_normalize_ws(p)) for p in self.passages],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return _digest(payload)


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def build_claim_evidence(
    claim: CitedClaim,
    evidence: Sequence[Evidence],
    cited_indices: Sequence[int] = (),
) -> ClaimEvidence:
    """把一条论断绑定到证据片段。

    ``cited_indices`` 是该节点「局部引用编号 ↔ 证据列表」的映射（与执行器
    登记 ``cited_indices`` 的口径一致）；没有映射时按 1-based 位置对齐。
    """
    mapped: dict[int, list[Evidence]] = {}
    if cited_indices and len(cited_indices) == len(evidence):
        for local_no, item in zip(cited_indices, evidence, strict=True):
            mapped.setdefault(int(local_no), []).append(item)
    else:
        for position, item in enumerate(evidence, start=1):
            mapped.setdefault(position, []).append(item)

    passages: list[str] = []
    has_full_text = False
    budget = MAX_EVIDENCE_CHARS_PER_CLAIM
    for ref_no in claim.cited_ref_ids:
        for item in mapped.get(int(ref_no), []):
            if item.kind == "excerpt" and item.chunk_id is not None:
                has_full_text = True
            if len(passages) >= MAX_PASSAGES_PER_CLAIM or budget <= 0:
                continue
            passage = _truncate(item.excerpt, min(MAX_PASSAGE_CHARS, budget))
            if passage and passage not in passages:
                passages.append(passage)
                budget -= len(passage)

    return ClaimEvidence(
        claim=claim,
        passages=tuple(passages),
        scope="full_text" if has_full_text else "abstract_only",
    )


def build_claim_evidence_list(
    claims: Sequence[CitedClaim],
    evidence: Sequence[Evidence],
    cited_indices: Sequence[int] = (),
) -> list[ClaimEvidence]:
    """批量版 :func:`build_claim_evidence`。"""
    return [build_claim_evidence(claim, evidence, cited_indices) for claim in claims]


def summarize_claim_checks(checks: Sequence[ClaimCheckResult]) -> dict[str, Any]:
    """汇总统计：完全支持率、被标记数、按判定计数、撤稿引用数。"""
    total = len(checks)
    supported = sum(1 for item in checks if item.verdict == "supported")
    by_verdict: dict[str, int] = {}
    for item in checks:
        by_verdict[item.verdict] = by_verdict.get(item.verdict, 0) + 1
    return {
        "total": total,
        "supported": supported,
        "flagged": total - supported,
        "supported_rate": round(supported / total, 4) if total else None,
        "by_verdict": by_verdict,
    }


def _fallback(claim: CitedClaim, reason: str) -> ClaimCheckResult:
    return ClaimCheckResult(
        claim_id=claim.claim_id,
        sentence_text=claim.sentence_text,
        cited_ref_ids=[f"E{no}" for no in claim.cited_ref_ids],
        verdict="unverifiable",
        confidence=0.0,
        evidence_quote=None,
        rationale=reason,
        suggested_fix=None,
        evidence_scope=None,
        evidence_passage=None,
    )


class ClaimVerifier:
    """批量论断核验器（每批 6 条、并发 5、按内容哈希缓存）。"""

    def __init__(
        self,
        llm: BaseLLM,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    ) -> None:
        self._llm = llm
        self._batch_size = max(1, batch_size)
        self._semaphore = asyncio.Semaphore(max(1, max_concurrency))
        self._cache: dict[str, ClaimCheckResult] = {}

    async def verify(self, items: Sequence[ClaimEvidence]) -> list[ClaimCheckResult]:
        """核验一批论断；返回顺序与输入一致。"""
        if not items:
            return []

        results: list[ClaimCheckResult | None] = [None] * len(items)
        pending: list[tuple[int, ClaimEvidence]] = []
        for index, item in enumerate(items):
            cached = self._cache.get(item.cache_key)
            if cached is not None:
                results[index] = cached
            else:
                pending.append((index, item))

        if pending:
            batches = [
                pending[start : start + self._batch_size]
                for start in range(0, len(pending), self._batch_size)
            ]
            await asyncio.gather(*(self._run_batch(batch, results) for batch in batches))

        return [
            item if item is not None else _fallback_for(items[i]) for i, item in enumerate(results)
        ]

    async def _run_batch(
        self,
        batch: Sequence[tuple[int, ClaimEvidence]],
        results: list[ClaimCheckResult | None],
    ) -> None:
        async with self._semaphore:
            try:
                parsed = await self._judge([item for _, item in batch])
            except Exception as exc:
                logger.warning("claim_verification_failed", error=str(exc))
                parsed = {}

            for index, item in batch:
                check = parsed.get(item.claim.claim_id)
                if check is None:
                    results[index] = _fallback(item.claim, "核验未返回结果，已标为无法核验")
                    continue
                check = self._sanitize(check, item)
                results[index] = check
                self._cache[item.cache_key] = check

    async def _judge(self, items: Sequence[ClaimEvidence]) -> dict[str, ClaimCheckResult]:
        payload = {
            "claims": [
                {
                    "claim_id": item.claim.claim_id,
                    "sentence": item.claim.sentence_text,
                    "section": item.claim.section,
                    "refs": [f"E{no}" for no in item.claim.cited_ref_ids],
                    "scope": item.scope,
                    "passages": list(item.passages),
                }
                for item in items
            ]
        }
        messages = [
            Message(role=MessageRole.SYSTEM, content=_SYSTEM_PROMPT),
            Message(role=MessageRole.USER, content=json.dumps(payload, ensure_ascii=False)),
        ]
        response = await self._chat(messages)
        return self._parse_response(response, items)

    async def _chat(self, messages: list[Message]) -> str:
        from app.llm.router import node_type_scope

        with node_type_scope(_claim_node_type()):
            response = await self._llm.chat(
                messages,
                temperature=0.1,
                max_tokens=1600,
                timeout=getattr(self._llm, "call_timeout_validation", None),
            )
        return getattr(response, "content", None) or ""

    def _parse_response(
        self,
        content: str,
        items: Sequence[ClaimEvidence],
    ) -> dict[str, ClaimCheckResult]:
        text = content.strip()
        start, end = text.find("["), text.rfind("]")
        if start < 0 or end <= start:
            raise ValueError("模型输出不是 JSON 数组")
        raw = json.loads(text[start : end + 1])
        if not isinstance(raw, list):
            raise ValueError("模型输出不是 JSON 数组")

        by_id = {item.claim.claim_id: item for item in items}
        parsed: dict[str, ClaimCheckResult] = {}
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            claim_id = str(entry.get("claim_id", ""))
            item = by_id.get(claim_id)
            if item is None:
                continue
            parsed[claim_id] = self._sanitize(entry, item)
        return parsed

    def _sanitize(
        self, entry: ClaimCheckResult | dict[str, Any], item: ClaimEvidence
    ) -> ClaimCheckResult:
        claim = item.claim
        if isinstance(entry, ClaimCheckResult):
            data = entry.model_dump()
        else:
            data = dict(entry)

        verdict = str(data.get("verdict", "")).strip()
        if verdict not in _VALID_VERDICTS:
            verdict = "unverifiable"
        # 只有摘要时「矛盾/曲解」不成立：摘要没提 ≠ 论文没做
        if item.scope == "abstract_only" and verdict in {"contradicted", "misrepresented"}:
            verdict = "unverifiable"

        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = min(1.0, max(0.0, confidence))

        quote = data.get("evidence_quote")
        if quote is not None:
            quote = str(quote).strip()
            haystack = _normalize_ws(" ".join(item.passages))
            quote = quote if quote and _normalize_ws(quote) in haystack else None

        rationale = str(data.get("rationale") or "").strip()[:1000]
        fix = data.get("suggested_fix")
        fix = str(fix).strip()[:1000] if fix else None
        if verdict == "supported":
            fix = None

        return ClaimCheckResult(
            claim_id=claim.claim_id,
            sentence_text=claim.sentence_text,
            cited_ref_ids=[f"E{no}" for no in claim.cited_ref_ids],
            verdict=verdict,  # type: ignore[arg-type]
            confidence=confidence,
            evidence_quote=quote,
            rationale=rationale or "模型未给出理由",
            suggested_fix=fix,
            evidence_scope=item.scope,
            evidence_passage=item.passages[0] if item.passages else None,
        )


def _fallback_for(item: ClaimEvidence) -> ClaimCheckResult:
    return _fallback(item.claim, "核验未执行，已标为无法核验")


def _claim_node_type() -> str:
    """论断核验可单独配模型；未配置时沿用 ``validate`` 的路由。"""
    try:
        from app.llm.scope import get_user_routes

        routes = get_user_routes()
        if routes and "claim_check" in routes:
            return "claim_check"
    except Exception:
        pass
    try:
        from app.core.config import get_settings

        if "claim_check" in (getattr(get_settings().llm, "node_routing", {}) or {}):
            return "claim_check"
    except Exception:
        pass
    return "validate"
