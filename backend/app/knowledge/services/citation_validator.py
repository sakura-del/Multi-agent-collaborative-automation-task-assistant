"""确定性引用校验（Plan 3 Task 4）。

不调用 LLM，按固定五项检查生成 `ValidationReport`：

    citation_markers_resolve      引用标记必须落在证据池范围内
    evidence_is_cited             证据池非空时正文必须真正引用池内证据（不要求逐条引用）
    formal_claims_have_evidence   形式化断言必须带引用或显式标注「模型推断」
    required_sections_present     必需章节齐全
    duplicate_paragraph_ratio     重复段落比例不超过阈值（默认 0.20）

另有两项完整性检查（`evidence_ids_valid` / `evidence_ownership`）：
不通过时 `retryable=False`，因为重生成无法修复标识缺失或归属越权。

Issue #3 新增外部引用真实性核验（`reference_authenticity`）：
由 `ReferenceVerifier` 预先产出四态结论（三态判定 + `unverified` 降级态）后传入，
本模块只做确定性归并与判定，不发起任何网络请求。
核验模式 `off` / `report` / `enforce`：`report` 为 advisory（记录但不影响通过与否），
`enforce` 才让「未找到 / 元数据不符」导致校验不通过；`unverified` 永不判失败。

`retryable=True` 仅用于可通过定向重生成修复的问题：缺失/未知引用、缺失章节、
重复段落比例超阈值、以及 enforce 模式下的引用真实性问题。
分数保持 0–100；仅在写入既有 `ValidationResult.score` 列（0–1）时做归一化。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:  # 运行期按需导入：避免 knowledge → orchestration 包初始化时的循环依赖
    from app.orchestration.contracts import (
        Evidence,
        ExecutionResult,
        ValidationCheck,
        ValidationReport,
    )
    from app.tools.literature.models import ReferenceVerification

#: 计划要求的固定检查名（顺序即报告顺序）
REQUIREMENT_CHECKS: tuple[str, ...] = (
    "citation_markers_resolve",
    "evidence_is_cited",
    "formal_claims_have_evidence",
    "required_sections_present",
    "duplicate_paragraph_ratio",
)

#: Issue #3 引用真实性核验的检查名
REFERENCE_CHECK = "reference_authenticity"

#: 核验模式：off=不检查；report=advisory（不影响通过与否）；enforce=不通过则校验失败
ReferenceCheckMode = Literal["off", "report", "enforce"]

#: 内容角色（Issue #6）：
#: - authored：由模型创作的正文（extract/write 节点），作者级文本质量检查照常生效；
#: - aggregated：search/validate/review 等节点的**机械汇总**内容（依赖输出拼接或检索结果列表），
#:   跨节点复述是这类内容的固有形态、重试无法修复，故不做作者级判定。
ContentRole = Literal["authored", "aggregated"]

#: 仅对作者级内容有意义的检查（Issue #6）
AUTHORED_ONLY_CHECKS: tuple[str, ...] = (
    "formal_claims_have_evidence",
    "duplicate_paragraph_ratio",
)

#: 汇总内容上这些检查的说明文案（必须显式出现，不得静默变成通过）
_AGGREGATED_SKIP_NOTE = "机器汇总内容（依赖输出拼接/检索结果），不做作者级判定"

#: 判定态 → 中文标签（进入校验报告 message）
_VERDICT_LABELS: tuple[tuple[str, str], ...] = (
    ("verified", "已验证"),
    ("metadata_mismatch", "元数据不符"),
    ("not_found", "未找到"),
    ("unverified", "未核验"),
)
#: 会被计入「问题条目」的判定态
_PROBLEM_VERDICTS = frozenset({"metadata_mismatch", "not_found"})
_MAX_REPORTED_PROBLEMS = 3

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
        reference_mode: ReferenceCheckMode = "off",
    ) -> None:
        self._threshold = duplicate_ratio_threshold
        # 由调用方（工作流/持久化层）注入的归属校验结果
        self._ownership_violation = ownership_violation
        # Issue #3：引用真实性核验模式，缺省 off 保持既有报告形状完全不变
        self._reference_mode: ReferenceCheckMode = reference_mode

    def validate(
        self,
        result: ExecutionResult,
        required_sections: Sequence[str] | None = None,
        *,
        evidence_pool: Sequence[Evidence] | None = None,
        reference_verifications: Sequence[ReferenceVerification] | None = None,
        content_role: ContentRole = "authored",
    ) -> ValidationReport:
        """校验引用可追溯性；`evidence_pool` 缺省时使用 `result.evidence`。

        `reference_verifications` 为外部引用核验结论：
        仅当 `reference_mode != "off"` 且传入非 None 时才追加 `reference_authenticity` 检查。

        `content_role`（Issue #6）：`aggregated` 表示这是 search/validate/review 节点的机械汇总内容，
        `AUTHORED_ONLY_CHECKS` 记为「不适用」而非失败——跨节点复述不是模型缺陷，
        且重试无法修复（实测约 40% 运行因此转人工）。报告形状（检查名与顺序）保持不变。
        """
        pool = list(evidence_pool if evidence_pool is not None else result.evidence)
        content = result.content or ""
        markers = [int(match) for match in MARKER_PATTERN.findall(content)]
        used = set(markers)
        authored = content_role != "aggregated"

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

        # 语义：只要证据池非空，正文就必须真正用上池内证据（避免忽略检索结果的空转），
        # 但不要求逐条引用——单节点只采样相关证据是合理的；覆盖率作为提示信息记录。
        uncited = [index for index in range(1, len(pool) + 1) if index not in used]
        if pool and not used:
            checks.append(
                _fail(
                    "evidence_is_cited",
                    f"{len(pool)} 条证据均未被引用，正文未使用检索证据",
                )
            )
            retryable = True
        elif uncited:
            checks.append(
                _ok(
                    "evidence_is_cited",
                    f"已引用 {len(used)}/{len(pool)} 条证据；未引用: "
                    f"{[f'[E{index}]' for index in uncited]}",
                )
            )
        else:
            checks.append(_ok("evidence_is_cited", f"{len(pool)} 条证据均被引用"))

        bare_claims = self._bare_claims(content)
        if not authored:
            checks.append(_ok("formal_claims_have_evidence", f"{_AGGREGATED_SKIP_NOTE}（不适用）"))
        elif bare_claims:
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
        if not authored:
            checks.append(
                _ok(
                    "duplicate_paragraph_ratio",
                    f"{_AGGREGATED_SKIP_NOTE}（不适用，重复比例 {ratio:.2f}）",
                )
            )
        elif ratio > self._threshold:
            checks.append(
                _fail(
                    "duplicate_paragraph_ratio",
                    f"重复段落比例 {ratio:.2f} 超过阈值 {self._threshold:.2f}",
                )
            )
            retryable = True
        else:
            checks.append(_ok("duplicate_paragraph_ratio", f"重复段落比例 {ratio:.2f}"))

        # 元数据证据（`kind="metadata"`）按设计**没有** doc_id/chunk_id ——
        # 它来自文献检索结果，不绑定知识库分块。填一个假 UUID 会让
        # 「可回溯」变成假话（AGENTS §5 #28）。所以只对原文证据要求标识完整。
        malformed = [
            index
            for index, item in enumerate(pool, start=1)
            if item.kind != "metadata" and (item.doc_id is None or item.chunk_id is None)
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

        # Issue #3：外部引用真实性（网络核验由 ReferenceVerifier 预先完成）
        if self._reference_mode != "off" and reference_verifications is not None:
            reference_check = self.check_reference_authenticity(
                reference_verifications,
                advisory=self._reference_mode == "report",
            )
            checks.append(reference_check)
            if not reference_check.passed and not reference_check.advisory:
                # 伪造/不符的引用可通过「只引用已核验文献」的定向重生成修复
                retryable = True

        # advisory 检查只作为审计信息：不参与通过判定与评分
        scored = [check for check in checks if not check.advisory]
        passed = all(check.passed for check in scored)
        score = round(100.0 * sum(1 for check in scored if check.passed) / len(scored), 2)
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
    def check_reference_authenticity(
        verifications: Sequence[ReferenceVerification],
        *,
        advisory: bool,
    ) -> ValidationCheck:
        """把 `ReferenceVerifier` 的四态结论归并为一条 `ValidationCheck`。

        - `verified` / `unverified` 都算通过：后者是外部源不可用的降级态，不得误伤；
        - `metadata_mismatch` / `not_found` 判为不通过，并在 message 中最多列出 3 条问题条目。
        """
        counts: dict[str, int] = {verdict: 0 for verdict, _label in _VERDICT_LABELS}
        problems: list[ReferenceVerification] = []
        for verification in verifications:
            value = verification.verdict.value
            counts[value] = counts.get(value, 0) + 1
            if value in _PROBLEM_VERDICTS:
                problems.append(verification)

        labels = dict(_VERDICT_LABELS)
        head = "引用核验：" + " / ".join(
            f"{labels[verdict]} {counts.get(verdict, 0)} 条" for verdict, _label in _VERDICT_LABELS
        )
        message = head
        if problems:
            details = "; ".join(
                f"{item.title}（{labels[item.verdict.value]}"
                + (f"：{item.reason}" if item.reason else "")
                + "）"
                for item in problems[:_MAX_REPORTED_PROBLEMS]
            )
            message = f"{head}；问题条目：{details}"

        passed = not problems
        from app.orchestration.contracts import ValidationCheck as Check

        return Check(
            name=REFERENCE_CHECK,
            passed=passed,
            message=message,
            advisory=advisory,
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
