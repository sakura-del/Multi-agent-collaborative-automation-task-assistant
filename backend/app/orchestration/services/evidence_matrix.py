"""研究空白矩阵聚合服务（Issue #32 Phase 3）。

将任务规划中的研究子问题（`TaskPlan.research_questions`）、复盘产出的论断核验明细
（`ReviewReport.claim_checks`）与参考文献表（`ReviewReport.bibliography`）确定性地聚合为
「子问题 × 证据强度（strong / weak_or_caveat / conflicting / gap）」二维矩阵。

红线约束：
- 严格区分 `evidence_kind="excerpt"`（来自全文分片）与 `"metadata"`（仅题录/摘要）；
- 绝不为无原文支撑的条目伪造 `evidence_quote` 或夸大为 `"strong"` 全文支持；
- 为 `"gap"` 与 `"weak_or_caveat"` 格子生成定向 `suggested_query`，供一键补检索闭环使用。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from app.orchestration.contracts import (
    ClaimCheckResult,
    ClaimVerdict,
    EvidenceMatrix,
    EvidenceMatrixCell,
    EvidenceStrengthBucket,
)

BUCKETS: tuple[EvidenceStrengthBucket, ...] = (
    "strong",
    "weak_or_caveat",
    "conflicting",
    "gap",
)

_TOKEN_RE = re.compile(r"[A-Za-z0-9]{2,}|[\u4e00-\u9fa5]{2,}")
_REF_INDEX_RE = re.compile(r"^E(\d+)$")


def verdict_to_bucket(verdict: ClaimVerdict) -> EvidenceStrengthBucket:
    """将 6 态论断判定映射至 4 档证据强度列。"""
    if verdict == "supported":
        return "strong"
    if verdict in {"weakly_supported", "overstated", "misrepresented"}:
        return "weak_or_caveat"
    if verdict == "contradicted":
        return "conflicting"
    return "gap"


def _tokenize(text: str) -> set[str]:
    """提取中英文粗粒度词项与中文二元组，便于无模型下稳定匹配子问题。"""
    tokens: set[str] = set()
    for match in _TOKEN_RE.findall((text or "").casefold()):
        if match.isascii():
            tokens.add(match)
        else:
            tokens.add(match)
            for i in range(len(match) - 1):
                tokens.add(match[i : i + 2])
    return tokens


def _match_question_index(
    claim: ClaimCheckResult,
    questions: Sequence[str],
    *,
    node_titles: Mapping[str, str],
    fallback_index: int,
) -> int:
    """把单条论断归入最匹配的研究子问题行。"""
    if len(questions) <= 1:
        return 0
    node_id = claim.claim_id.partition(":")[0]
    node_title = node_titles.get(node_id, "")
    claim_tokens = _tokenize(f"{node_title} {claim.sentence_text}")
    if not claim_tokens:
        return fallback_index % len(questions)

    best_idx = 0
    best_score = -1
    for idx, question in enumerate(questions):
        q_tokens = _tokenize(question)
        score = len(claim_tokens & q_tokens)
        if score > best_score:
            best_score = score
            best_idx = idx
    if best_score <= 0:
        return fallback_index % len(questions)
    return best_idx


def _parse_ref_indices(claims: Sequence[ClaimCheckResult]) -> list[int]:
    indices: set[int] = set()
    for claim in claims:
        for ref in claim.cited_ref_ids:
            m = _REF_INDEX_RE.match(ref.strip())
            if m:
                indices.add(int(m.group(1)))
    return sorted(indices)


def _cell_evidence_kind(
    claims: Sequence[ClaimCheckResult],
    ref_indices: Sequence[int],
    bibliography: Sequence[Mapping[str, Any]],
) -> Literal["excerpt", "metadata", "none"]:
    """如实判定格内底层证据粒度：有全文分片才标 excerpt，否则有题录标 metadata，均无标 none。"""
    if not claims and not ref_indices:
        return "none"
    if any(claim.evidence_scope == "full_text" for claim in claims):
        return "excerpt"
    by_idx = {
        int(entry["index"]): entry
        for entry in bibliography
        if isinstance(entry, Mapping) and isinstance(entry.get("index"), int)
    }
    if any(by_idx.get(idx, {}).get("chunk_id") for idx in ref_indices):
        return "excerpt"
    if claims or ref_indices:
        return "metadata"
    return "none"


def build_suggested_query(question: str, *, topic: str = "") -> str:
    """为研究空白或弱证据格子生成定向检索词（≤300 字）。"""
    clean_q = re.sub(r"^\s*\d+[.、)\s]+", "", question or "").strip(" ？?。.")
    clean_topic = (topic or "").strip()
    if clean_topic and clean_topic.casefold() not in clean_q.casefold():
        candidate = f"{clean_topic} {clean_q}".strip()
    else:
        candidate = clean_q or clean_topic or "literature review gap"
    return candidate[:300]


def _cell_summary(
    bucket: EvidenceStrengthBucket,
    claims: Sequence[ClaimCheckResult],
    ref_indices: Sequence[int],
    evidence_kind: Literal["excerpt", "metadata", "none"],
    *,
    row_has_any_claim: bool,
) -> str:
    if not claims:
        if bucket == "gap" and not row_has_any_claim:
            return "该子问题尚未命中可核验论断，建议执行定向补检索。"
        return ""
    scope_note = "（仅题录/摘要级）" if evidence_kind == "metadata" else ""
    labels: dict[EvidenceStrengthBucket, str] = {
        "strong": "强支持论断",
        "weak_or_caveat": "弱支持/存疑论断",
        "conflicting": "冲突论断",
        "gap": "无法核验论断",
    }
    return f"命中 {len(claims)} 条{labels[bucket]}，涉及 {len(ref_indices)} 篇文献{scope_note}。"


def build_evidence_matrix(
    questions: Sequence[str],
    claim_checks: Sequence[ClaimCheckResult | Mapping[str, Any]],
    bibliography: Sequence[Mapping[str, Any]],
    *,
    node_titles: Mapping[str, str] | None = None,
    topic: str = "",
) -> EvidenceMatrix:
    """将研究子问题与论断核验明细聚合为 EvidenceMatrix。"""
    clean_questions = [str(q).strip() for q in questions if str(q).strip()]
    if not clean_questions:
        return EvidenceMatrix(questions=[], cells=[])

    normalized_claims: list[ClaimCheckResult] = [
        item if isinstance(item, ClaimCheckResult) else ClaimCheckResult.model_validate(item)
        for item in claim_checks
    ]
    titles_map = dict(node_titles or {})

    grouped: dict[tuple[int, EvidenceStrengthBucket], list[ClaimCheckResult]] = {}
    row_counts: dict[int, int] = {i: 0 for i in range(len(clean_questions))}
    for pos, claim in enumerate(normalized_claims):
        q_idx = _match_question_index(
            claim, clean_questions, node_titles=titles_map, fallback_index=pos
        )
        bucket = verdict_to_bucket(claim.verdict)
        grouped.setdefault((q_idx, bucket), []).append(claim)
        row_counts[q_idx] += 1

    cells: list[EvidenceMatrixCell] = []
    for q_idx, question in enumerate(clean_questions):
        row_has_any = row_counts[q_idx] > 0
        query_hint = build_suggested_query(question, topic=topic)
        for bucket in BUCKETS:
            bucket_claims = grouped.get((q_idx, bucket), [])
            ref_indices = _parse_ref_indices(bucket_claims)
            evidence_kind = _cell_evidence_kind(bucket_claims, ref_indices, bibliography)
            summary = _cell_summary(
                bucket,
                bucket_claims,
                ref_indices,
                evidence_kind,
                row_has_any_claim=row_has_any,
            )
            suggested = (
                query_hint
                if bucket == "gap" or (bucket == "weak_or_caveat" and bool(bucket_claims))
                else None
            )
            cells.append(
                EvidenceMatrixCell(
                    question_index=q_idx,
                    bucket=bucket,
                    claim_ids=[c.claim_id for c in bucket_claims],
                    ref_indices=ref_indices,
                    evidence_kind=evidence_kind,
                    summary=summary[:400],
                    suggested_query=suggested,
                )
            )
    return EvidenceMatrix(questions=clean_questions, cells=cells)


def merge_gap_records_into_matrix(
    matrix: EvidenceMatrix,
    *,
    question_index: int,
    added_ref_indices: Sequence[int],
    query: str,
) -> EvidenceMatrix:
    """定向补检索新增文献后，更新目标子问题行的矩阵格子（如实标 metadata）。"""
    if not (0 <= question_index < len(matrix.questions)) or not added_ref_indices:
        return matrix

    updated_cells: list[EvidenceMatrixCell] = []
    for cell in matrix.cells:
        if cell.question_index != question_index:
            updated_cells.append(cell)
            continue
        if cell.bucket == "weak_or_caveat":
            merged_refs = sorted(set(cell.ref_indices) | {int(i) for i in added_ref_indices})
            kind: Literal["excerpt", "metadata", "none"] = (
                "excerpt" if cell.evidence_kind == "excerpt" else "metadata"
            )
            extra_note = f"定向补检索新增 {len(added_ref_indices)} 篇文献（仅题录/摘要级）。"
            summary = f"{cell.summary} {extra_note}".strip()[:400]
            updated_cells.append(
                cell.model_copy(
                    update={
                        "ref_indices": merged_refs,
                        "evidence_kind": kind,
                        "summary": summary,
                        "suggested_query": query[:300],
                    }
                )
            )
        elif cell.bucket == "gap":
            summary = (
                f"已针对检索式「{query[:80]}」补充 {len(added_ref_indices)} 篇题录文献，"
                "详情见正文新增证据段落。"
            )[:400]
            updated_cells.append(
                cell.model_copy(
                    update={
                        "summary": summary,
                        "suggested_query": query[:300],
                    }
                )
            )
        else:
            updated_cells.append(cell)

    return EvidenceMatrix(questions=list(matrix.questions), cells=updated_cells)
