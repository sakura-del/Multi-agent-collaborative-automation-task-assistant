"""确定性引用校验（Plan 3 Task 4）。

不调用 LLM，按固定五项检查生成 `ValidationReport`：

    citation_markers_resolve      引用标记必须落在证据池范围内
    evidence_is_cited             证据池中的每条证据都必须被引用
    formal_claims_have_evidence   形式化断言必须带引用或显式标注「模型推断」
    required_sections_present     必需章节齐全
    duplicate_paragraph_ratio     重复段落比例不超过阈值（默认 0.20）

另有两项完整性检查（`evidence_ids_valid` / `evidence_ownership`）：
不通过时 `retryable=False`，因为重生成无法修复标识缺失或归属越权。

`retryable=True` 仅用于可通过定向重生成修复的问题：缺失/未知引用、缺失章节、
重复段落比例超阈值。分数保持 0–100；仅在写入既有 `ValidationResult.score`
列（0–1）时做归一化。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 运行期按需导入：避免 knowledge → orchestration 包初始化时的循环依赖
    from app.orchestration.contracts import (
        Evidence,
        ExecutionResult,
        ValidationCheck,
        ValidationReport,
    )

#: 计划要求的固定检查名（顺序即报告顺序）
REQUIREMENT_CHECKS: tuple[str, ...] = (
    "citation_markers_resolve",
    "evidence_is_cited",
    "formal_claims_have_evidence",
    "required_sections_present",
    "duplicate_paragraph_ratio",
)

MARKER_PATTERN = re.compile(r"\[E(\d+)\]")
_SENTENCE_SPLIT = re.compile(r"[。！？!?\n]+")
_INFERENCE_LABEL = "模型推断"
_CLAIM_HINTS = (
    "研究表明",
    "文献表明",
    "已有工作",
    "实验表明",
    "结果显示",
    "调查显示",
    "studies show",
    "research shows",
    "prior work",
)
DEFAULT_DUPLICATE_RATIO_THRESHOLD = 0.20


def _ok(name: str, message: str) -> ValidationCheck:
    from app.orchestration.contracts import ValidationCheck as Check

    return Check(name=name, passed=True, message=message)


def _fail(name: str, message: str) -> ValidationCheck:
    from app.orchestration.contracts import ValidationCheck as Check

    return Check(name=name, passed=False, message=message)


class CitationValidator:
    """引用可追溯性的确定性校验器。"""

    def __init__(
        self,
        *,
        duplicate_ratio_threshold: float = DEFAULT_DUPLICATE_RATIO_THRESHOLD,
        ownership_violation: bool = False,
    ) -> None:
        self._threshold = duplicate_ratio_threshold
        # 由调用方（工作流/持久化层）注入的归属校验结果
        self._ownership_violation = ownership_violation

    def validate(
        self,
        result: ExecutionResult,
        required_sections: Sequence[str] | None = None,
        *,
        evidence_pool: Sequence[Evidence] | None = None,
    ) -> ValidationReport:
        """校验引用可追溯性；`evidence_pool` 缺省时使用 `result.evidence`。"""
        pool = list(evidence_pool if evidence_pool is not None else result.evidence)
        content = result.content or ""
        markers = [int(match) for match in MARKER_PATTERN.findall(content)]
        used = set(markers)

        checks: list[ValidationCheck] = []
        retryable = False
        integrity_failed = False

        unknown = sorted(index for index in used if index < 1 or index > len(pool))
        if unknown:
            checks.append(
                _fail(
                    "citation_markers_resolve",
                    f"存在无法解析的引用标记: {[f'[E{index}]' for index in unknown]}",
                )
            )
            retryable = True
        else:
            checks.append(_ok("citation_markers_resolve", f"{len(used)} 个引用标记全部可解析"))

        uncited = [index for index in range(1, len(pool) + 1) if index not in used]
        if uncited:
            checks.append(
                _fail(
                    "evidence_is_cited",
                    f"证据未被引用: {[f'[E{index}]' for index in uncited]}",
                )
            )
            retryable = True
        else:
            checks.append(_ok("evidence_is_cited", f"{len(pool)} 条证据均被引用"))

        bare_claims = self._bare_claims(content)
        if bare_claims:
            checks.append(
                _fail(
                    "formal_claims_have_evidence",
                    f"{len(bare_claims)} 处形式化断言缺少引用: {bare_claims[0][:60]}",
                )
            )
            retryable = True
        else:
            checks.append(_ok("formal_claims_have_evidence", "形式化断言均带引用或标注模型推断"))

        missing = [section for section in (required_sections or []) if section not in content]
        if missing:
            checks.append(_fail("required_sections_present", f"缺少必需章节: {missing}"))
            retryable = True
        else:
            checks.append(_ok("required_sections_present", "必需章节齐全"))

        ratio = self._duplicate_ratio(content)
        if ratio > self._threshold:
            checks.append(
                _fail(
                    "duplicate_paragraph_ratio",
                    f"重复段落比例 {ratio:.2f} 超过阈值 {self._threshold:.2f}",
                )
            )
            retryable = True
        else:
            checks.append(_ok("duplicate_paragraph_ratio", f"重复段落比例 {ratio:.2f}"))

        malformed = [
            index
            for index, item in enumerate(pool, start=1)
            if item.doc_id is None or item.chunk_id is None
        ]
        if malformed:
            checks.append(
                _fail(
                    "evidence_ids_valid",
                    f"证据标识缺失: {[f'[E{index}]' for index in malformed]}",
                )
            )
            integrity_failed = True
        else:
            checks.append(_ok("evidence_ids_valid", "证据标识完整"))

        if self._ownership_violation:
            checks.append(_fail("evidence_ownership", "证据归属校验失败（跨用户）"))
            integrity_failed = True
        else:
            checks.append(_ok("evidence_ownership", "证据归属校验通过"))

        passed = all(check.passed for check in checks)
        score = round(100.0 * sum(1 for check in checks if check.passed) / len(checks), 2)
        feedback = "; ".join(check.message for check in checks if not check.passed)
        from app.orchestration.contracts import ValidationReport as Report

        return Report(
            passed=passed,
            score=score,
            checks=checks,
            retryable=bool(retryable and not integrity_failed),
            feedback=feedback,
        )

    @staticmethod
    def _bare_claims(content: str) -> list[str]:
        """找出提到文献事实但既无引用标记、也未标注模型推断的句子。"""
        bare: list[str] = []
        for sentence in _SENTENCE_SPLIT.split(content):
            text = sentence.strip()
            if len(text) < 8 or _INFERENCE_LABEL in text:
                continue
            if MARKER_PATTERN.search(text):
                continue
            lowered = text.lower()
            if any(hint.lower() in lowered for hint in _CLAIM_HINTS):
                bare.append(text)
        return bare

    @staticmethod
    def _duplicate_ratio(content: str) -> float:
        paragraphs = [
            re.sub(r"\s+", " ", paragraph).strip()
            for paragraph in content.split("\n\n")
            if paragraph.strip()
        ]
        if len(paragraphs) < 2:
            return 0.0
        duplicates = len(paragraphs) - len(set(paragraphs))
        return duplicates / len(paragraphs)
