"""验证引用独立短链路（Issue #32 Phase 2A）。

## 为什么不走完整 5 节点综述 DAG

`LiteratureReviewWorkflow.validate_plan_contract` 强制规划包含
`search / extract / write / validate / review` 五类节点且 `write` 依赖 `extract`。
但「验证引用」的输入是用户已有草稿或文档，核心需求是核验已有引用的真实性与论断支撑度，
绝不能重新检索并重写用户的正文。

本模块直接复用既有核验组件：
1. 解析草稿正文中的 `[E#]` / `[#]` 引用与文末参考文献条目；
2. 调用 `ReferenceVerifier.verify_many()` 完成 Crossref / OpenAlex 四态核验与来源质量标记；
3. 调用 `ClaimVerifier.verify()` 输出 6 态论断核验结果（仅题录时 `abstract_only`，禁止伪造 quote）；
4. 组装标准 `ReviewReport`（原样保留草稿正文并保证与 `bibliography` 双向对齐，通过引用一致性闸门）。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.core.logging import get_logger
from app.knowledge.services.reference_verifier import (
    candidates_from_bibliography,
    summarize_verifications,
)
from app.orchestration.agents.reviewer import _verification_line, _verification_mark
from app.orchestration.contracts import ClaimCheckResult, Evidence, ReviewReport
from app.orchestration.validation.citation_consistency import extract_cited_claims
from app.orchestration.validation.claim_verification import (
    ClaimVerifier,
    build_claim_evidence_list,
    summarize_claim_checks,
)
from app.tools.literature.models import ReferenceVerdict, ReferenceVerification

if TYPE_CHECKING:
    from app.knowledge.services.reference_verifier import ReferenceVerifier
    from app.llm.base import BaseLLM

logger = get_logger(__name__)

_REF_HEADING_RE = re.compile(
    r"^\s*#{1,3}\s*(?:参考文献|References|Bibliography)\s*$", re.IGNORECASE | re.MULTILINE
)
_REF_LINE_RE = re.compile(r"^\s*(?:[-*]\s*)?(?:\[E?(\d+)\]|(\d+)\.)\s*(.+?)\s*$", re.IGNORECASE)
_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s,;\]\)\"'>]+)", re.IGNORECASE)
_URL_RE = re.compile(r"(https?://[^\s\)\]>]+)", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
_PLAIN_BRACKET_RE = re.compile(r"(?<!\[)\[(\d+)\](?!\]|\()")
_E_MARKER_RE = re.compile(r"\[E(\d+)\]")


@dataclass(frozen=True)
class DocumentChunkInput:
    """用户勾选的知识库原文分块（用于全文级论断核验）。"""

    doc_id: UUID
    chunk_id: UUID
    title: str
    content: str
    source_url: str | None = None


def parse_draft_and_references(
    draft_text: str,
) -> tuple[str, list[dict[str, Any]]]:
    """拆分草稿正文与文末参考文献区，并将 `[n]` 归一为 `[En]` 供引用核验链识别。

    规则：
    - 若草稿含 `## 参考文献` / `## References` 标题，其下方解析为参考文献条目；
    - 若正文未出现 `[E#]` 而出现了 `[1]` 这类数字角标且存在对应编号（或正文引用了该编号），
      自动转换为 `[E1]`，以便 `extract_cited_claims` 与前端 `ClaimEvidenceDrawer` 无缝识别；
    - 正文引用了参考文献中缺失的编号时，补齐最小占位条目（标题标明「未在草稿参考文献列表中提供」），
      既保证引用一致性闸门通过，又让核验器如实标出未验证/未找到。
    """
    raw = (draft_text or "").strip()
    if not raw:
        return "", []

    match = _REF_HEADING_RE.search(raw)
    if match is not None:
        body = raw[: match.start()].strip()
        ref_block = raw[match.end() :].strip()
    else:
        body = raw
        ref_block = ""

    parsed_by_index: dict[int, dict[str, Any]] = {}
    next_index = 1
    for line in ref_block.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            break
        line_match = _REF_LINE_RE.match(stripped)
        if line_match is None:
            continue
        idx_str = line_match.group(1) or line_match.group(2)
        index = int(idx_str) if idx_str else next_index
        next_index = max(next_index, index + 1)
        entry_text = line_match.group(3).strip()
        parsed_by_index[index] = _parse_reference_line(index, entry_text)

    # 若正文只用了 [1] 而未用 [E1]，转换为 [E1]
    if not _E_MARKER_RE.search(body) and _PLAIN_BRACKET_RE.search(body):
        body = _PLAIN_BRACKET_RE.sub(lambda m: f"[E{m.group(1)}]", body)

    cited_indices = sorted({int(m.group(1)) for m in _E_MARKER_RE.finditer(body)})
    # 保证正文引用的每个编号都在 bibliography 中存在（缺失则补诚实占位）
    for idx in cited_indices:
        if idx not in parsed_by_index:
            parsed_by_index[idx] = {
                "index": idx,
                "title": f"引用 [{idx}]（草稿未提供题录）",
            }

    # 若存在文末未被引用的孤儿参考文献，且正文没有任何引用，保留它们并在正文追加核验清单；
    # 若正文已有引用，则仅保留被正文引用的条目 + 为未引用条目在正文添加「未在正文引用的文献」说明以保持双向对齐。
    orphan_indices = sorted(idx for idx in parsed_by_index if idx not in cited_indices)
    if orphan_indices:
        orphan_markers = " ".join(f"[E{idx}]" for idx in orphan_indices)
        note = f"\n\n> 注：以下参考文献条目出现在文末清单但未在正文段落直接标注角标：{orphan_markers}。"
        body = (body + note).strip()

    bibliography = [parsed_by_index[idx] for idx in sorted(parsed_by_index)]
    return body, bibliography


def _parse_reference_line(index: int, text: str) -> dict[str, Any]:
    """从单行参考文献文本提取标题、DOI、URL 与年份。"""
    entry: dict[str, Any] = {"index": index}
    doi_match = _DOI_RE.search(text)
    if doi_match:
        entry["doi"] = doi_match.group(1).rstrip(".")
    url_match = _URL_RE.search(text)
    if url_match:
        entry["source_url"] = url_match.group(1).rstrip(".,;")
    elif "doi" in entry:
        entry["source_url"] = f"https://doi.org/{entry['doi']}"
    year_match = _YEAR_RE.search(text)
    if year_match:
        entry["year"] = int(year_match.group(1))

    # 标题：去除 URL 与 DOI 尾巴后的可读正文
    title = _URL_RE.sub("", text)
    title = re.sub(r"\bdoi:\s*10\.\d{4,9}/\S+", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*[—–-]\s*$", "", title).strip(" .;-—")
    entry["title"] = title[:300] if title else text[:300]
    return entry


class VerifyCitationsRunner:
    """验证引用短链路执行器：题录核验 + 论断支撑度核验 → 标准 ReviewReport。"""

    def __init__(
        self,
        *,
        reference_verifier: ReferenceVerifier | None = None,
        llm: BaseLLM | None = None,
    ) -> None:
        self._reference_verifier = reference_verifier
        self._llm = llm

    async def run(
        self,
        *,
        topic: str,
        draft_text: str | None,
        document_chunks: Sequence[DocumentChunkInput] = (),
    ) -> ReviewReport:
        """执行引用核验并输出标准 ReviewReport。"""
        effective_draft = (draft_text or "").strip()
        if not effective_draft and document_chunks:
            # 仅勾选知识库文档而未粘贴草稿时，拼接前若干分块作为待检文本
            effective_draft = "\n\n".join(chunk.content for chunk in document_chunks[:8])

        body, bibliography = parse_draft_and_references(effective_draft)
        bibliography, verification_summary = await self._verify_bibliography(bibliography)

        evidence_list, cited_indices = self._build_evidence(bibliography, document_chunks)
        claims = extract_cited_claims(body, claim_prefix="verify:")
        claim_checks: list[ClaimCheckResult] = []
        if claims and self._llm is not None:
            claim_evidence = build_claim_evidence_list(
                claims, evidence_list, cited_indices=cited_indices
            )
            verifier = ClaimVerifier(self._llm)
            claim_checks = await verifier.verify(claim_evidence)

        claim_stats = summarize_claim_checks(claim_checks)
        retracted_count = sum(
            1
            for entry in bibliography
            if entry.get("is_retracted") is True
            or "retracted" in (entry.get("quality_flags") or [])
        )
        issues = self._collect_issues(bibliography, claim_checks)
        scores = self._compute_scores(bibliography, claim_stats)

        final_markdown = self._compose_markdown(
            topic=topic,
            body=body,
            bibliography=bibliography,
            verification_summary=verification_summary,
        )
        stats: dict[str, Any] = {
            "mode": "verify_citations",
            "documents": len(bibliography),
            "claims_checked": len(claim_checks),
            "retracted_refs": retracted_count,
            "title": topic,
        }
        if verification_summary is not None:
            stats["reference_verification"] = verification_summary

        return ReviewReport(
            final_markdown=final_markdown,
            bibliography=bibliography,
            scores=scores,
            issues=issues,
            stats=stats,
            claim_checks=claim_checks,
            claim_stats=claim_stats,
        )

    async def _verify_bibliography(
        self, bibliography: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        """复用 ReferenceVerifier 对参考文献做四态与来源质量核验。"""
        verifier = self._reference_verifier
        if verifier is None or not bibliography:
            return bibliography, None

        candidates = []
        keys: list[str | None] = []
        for entry in bibliography:
            built = candidates_from_bibliography([entry])
            candidate = built[0] if built else None
            keys.append(candidate.key if candidate is not None else None)
            if candidate is not None:
                candidates.append(candidate)

        try:
            verifications = await verifier.verify_many(candidates)
        except Exception as exc:
            logger.warning("verify_citations_ref_check_failed", error=str(exc))
            verifications = [
                ReferenceVerification(
                    reference_key=c.key,
                    title=c.title,
                    verdict=ReferenceVerdict.UNVERIFIED,
                    reason=f"核验异常，降级为未核验：{exc}"[:200],
                )
                for c in candidates
            ]

        by_key = {item.reference_key: item for item in verifications}
        for entry, key in zip(bibliography, keys, strict=True):
            verification = by_key.get(key) if key is not None else None
            if verification is None:
                continue
            entry["verification_status"] = verification.verdict.value
            entry["verification_reason"] = verification.reason
            entry["is_retracted"] = verification.is_retracted
            entry["quality_flags"] = list(verification.quality_flags)
            entry["source_type"] = verification.source_type
            entry["cited_by_count"] = verification.cited_by_count
            entry["retraction_reason"] = verification.retraction_reason
            entry["retraction_notice_doi"] = verification.retraction_notice_doi

        summary = summarize_verifications(verifications, dropped=0, intercepted=False)
        return bibliography, summary.model_dump()

    @staticmethod
    def _build_evidence(
        bibliography: list[dict[str, Any]],
        document_chunks: Sequence[DocumentChunkInput],
    ) -> tuple[list[Evidence], list[int]]:
        """构造供 ClaimVerifier 核验的证据列表（严守红线 4：无全文时标 metadata）。"""
        evidence: list[Evidence] = []
        cited_indices: list[int] = []

        for entry in bibliography:
            idx = int(entry["index"])
            title = str(entry.get("title") or "").strip()
            # 若存在同标题或唯一关联的知识库原文分块，作为 full_text excerpt 挂载
            matched_chunks = [
                chunk
                for chunk in document_chunks
                if title
                and (
                    chunk.title.casefold() in title.casefold()
                    or title.casefold() in chunk.title.casefold()
                )
            ]
            if not matched_chunks and len(document_chunks) > 0 and len(bibliography) == 1:
                matched_chunks = list(document_chunks[:3])

            if matched_chunks:
                for chunk in matched_chunks[:3]:
                    evidence.append(
                        Evidence(
                            doc_id=chunk.doc_id,
                            chunk_id=chunk.chunk_id,
                            excerpt=chunk.content[:2000] or title or f"文献 {idx}",
                            kind="excerpt",
                        )
                    )
                    cited_indices.append(idx)
            else:
                meta_parts = [title or f"文献 {idx}"]
                if entry.get("year"):
                    meta_parts.append(f"年份：{entry['year']}")
                if entry.get("doi"):
                    meta_parts.append(f"DOI：{entry['doi']}")
                if entry.get("verification_status"):
                    meta_parts.append(f"题录核验：{entry['verification_status']}")
                evidence.append(
                    Evidence(
                        excerpt="；".join(meta_parts)[:2000],
                        kind="metadata",
                    )
                )
                cited_indices.append(idx)
        return evidence, cited_indices

    @staticmethod
    def _collect_issues(
        bibliography: list[dict[str, Any]],
        claim_checks: Sequence[ClaimCheckResult],
    ) -> list[str]:
        issues: list[str] = []
        for entry in bibliography:
            idx = entry.get("index")
            status = entry.get("verification_status")
            if entry.get("is_retracted") is True:
                issues.append(f"参考文献 [{idx}] 已被撤稿，请勿作为有效论据。")
            elif status in {"not_found", "metadata_mismatch"}:
                issues.append(f"参考文献 [{idx}] 题录核验异常（{status}）。")
        for check in claim_checks:
            if check.verdict in {"contradicted", "misrepresented", "overstated"}:
                issues.append(
                    f"论断 {check.claim_id} 核验判定为 {check.verdict}：{check.rationale}"
                )
        return issues

    @staticmethod
    def _compute_scores(
        bibliography: list[dict[str, Any]],
        claim_stats: dict[str, Any],
    ) -> dict[str, float]:
        if bibliography:
            verified = sum(
                1 for item in bibliography if item.get("verification_status") == "verified"
            )
            ref_score = round(verified / len(bibliography), 4)
        else:
            ref_score = 1.0
        supported_rate = claim_stats.get("supported_rate")
        quality = float(supported_rate) if isinstance(supported_rate, (int, float)) else ref_score
        return {
            "citation_traceability": ref_score,
            "quality": round(quality, 4),
        }

    @staticmethod
    def _compose_markdown(
        *,
        topic: str,
        body: str,
        bibliography: list[dict[str, Any]],
        verification_summary: dict[str, Any] | None,
    ) -> str:
        parts = [f"# {topic}"]
        if body:
            parts.append(body)
        if bibliography:
            lines = []
            for entry in bibliography:
                title = entry.get("title") or "未命名文献"
                url = entry.get("source_url") or ""
                mark = _verification_mark(
                    entry.get("verification_status"), entry.get("is_retracted")
                )
                lines.append(f"- [{entry['index']}] {title} — {url}".rstrip(" —") + mark)
            parts.append("## 参考文献\n" + "\n".join(lines))
        if verification_summary is not None:
            parts.append(f"## 引用核验\n{_verification_line(verification_summary)}")
        return "\n\n".join(parts)
