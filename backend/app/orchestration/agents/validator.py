"""校验 Agent：规则引擎 → LLM 自检 → 质量评分 → 校验报告。

职责：
1. 规则引擎：格式/长度/关键词等硬性规则校验
2. LLM 自检：逻辑一致性、事实准确性等语义校验
3. 质量评分：综合规则与 LLM 结果生成 0-100 分
4. 校验报告：结构化输出通过/不通过及改进建议

设计要点：
- 规则与语义分离：规则即时确定，LLM 语义校验可降级
- 评分加权：规则 40% + 语义 60%，权重可配置
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError as PydanticValidationError

from app.core.logging import get_logger
from app.knowledge.services.citation_validator import CitationValidator, ContentRole
from app.llm.models import Message, MessageRole
from app.orchestration.base_agent import BaseAgent
from app.orchestration.contracts import (
    ClaimCheckResult,
    Evidence,
    ExecutionResult,
    ValidationCheck,
    ValidationReport,
)
from app.orchestration.messages import MessageType
from app.orchestration.validation.citation_consistency import extract_cited_claims
from app.orchestration.validation.claim_verification import (
    ClaimVerifier,
    build_claim_evidence_list,
    summarize_claim_checks,
)
from app.tools.literature.models import ReferenceVerification

logger = get_logger(__name__)

_CHECK_SYSTEM = """你是质量校验专家，只输出 JSON。以下为固定判定口径与输出约束，必须严格遵守：
- logic_consistent：结论之间不矛盾、不含无效推理；
- factual_accurate：**只**判断是否存在无明显事实错误或与自身陈述冲突之处；
- 若本节点是"检索/抽取"类节点，其职责就是如实罗列语料中的多篇文献，
  条目涉及不同主题是正常的，**不属于**"与子任务主题不匹配"，不要据此扣分；
- 只有当内容与子任务标题明显无关（例如子任务要求写综述却只输出目录）才判 false；
- issues 最多 3 条、每条不超过 50 字；suggestions 最多 2 条、每条不超过 50 字；
- 不要复述被校验内容的原文，不要输出 JSON 之外的任何解释或正文。

输出 JSON schema：
{
  "logic_consistent": true/false,
  "factual_accurate": true/false,
  "issues": ["问题描述（≤50 字）"],
  "suggestions": ["改进建议（≤50 字）"]
}"""

_CHECK_PROMPT = """子任务标题：{title}
执行结果：
{result}"""

# 默认校验规则：可按业务扩展
_DEFAULT_RULES: dict[str, Any] = {
    "min_length": 10,  # 最小字符数
    "max_length": 10000,  # 最大字符数
    "required_keywords": [],  # 必含关键词（空表示不校验）
}


class ValidatorAgent(BaseAgent):
    """校验 Agent：对执行结果进行规则与语义双重校验。"""

    def __init__(
        self,
        *,
        rules: dict[str, Any] | None = None,
        citation_validator: CitationValidator | None = None,
        required_sections: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        # 合并默认规则与自定义规则，允许按任务覆盖
        self._rules = {**_DEFAULT_RULES, **(rules or {})}
        # 文献场景：先跑确定性引用校验，通过后才做 LLM 语义校验
        self._citation_validator = citation_validator
        self._required_sections = list(required_sections or [])

    @property
    def agent_type(self) -> str:
        return "validator"

    async def _run(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """执行校验：规则引擎 → 确定性引用校验 → LLM 语义自检 → 评分 → 报告。

        input_data 需包含 title、result。
        文献场景（配置了 citation_validator 且带 execution_result）先跑确定性检查：
        安全/完整性失败直接返回，只有确定性检查通过才调用 LLM。
        """
        title = input_data.get("title", "")
        result = input_data.get("result", "")
        trace_id = input_data.get("trace_id", "")
        # 内容角色（Issue #6）：规则校验与引用校验都必须知道这是作者正文还是机械汇总，
        # 否则「作者级检查」会被施加到「机器汇总」上（2026-10-27 review 长度上限缺陷）。
        content_role = self._content_role(input_data)

        # 步骤1：规则引擎校验（确定性，即时返回）
        rule_report = self._check_rules(result, content_role=content_role)

        # 步骤2：确定性引用校验（文献场景）
        citation_report = self._run_citation_checks(input_data)

        # 步骤3：LLM 语义自检（仅当确定性安全/完整性检查通过，且非确定性检索节点）
        # search 节点的产出是程序化检索得到的文献列表（尤其是引用库/离线清单模式，重试
        # 必然返回完全相同的条目），不应交由 LLM 做主观相关性否决而触发死循环（Issue #58）。
        integrity_failed = (
            citation_report is not None
            and not citation_report.passed
            and not citation_report.retryable
        )
        if integrity_failed:
            semantic_report = None
        elif self._is_search_node(input_data):
            semantic_report = {
                "score": 100,
                "issues": [],
                "suggestions": [],
                "logic_consistent": True,
                "factual_accurate": True,
            }
        else:
            semantic_report = await self._check_semantic(title, result)

        # 步骤4：论断级核验（Issue #31 Phase 2）——只对通过校验的作者正文执行
        if citation_report is None:
            report = self._legacy_report(rule_report, semantic_report)
        else:
            report = self._citation_report(rule_report, citation_report, semantic_report)

        # 校验失败会触发重生成：花钱核验一段马上要被丢弃的正文没有意义，
        # 所以只在本次校验已通过时做论断核验。
        claim_checks: list[ClaimCheckResult] = []
        if (
            self.llm is not None
            and report["passed"]
            and citation_report is not None
            and not integrity_failed
            and self._is_authored(input_data, content_role=content_role)
        ):
            claim_checks = await self._run_claim_checks(input_data)

        report["claim_checks"] = [item.model_dump() for item in claim_checks]
        claim_stats = summarize_claim_checks(claim_checks)
        report["supported_rate"] = claim_stats["supported_rate"]
        report["flagged_claims_count"] = claim_stats["flagged"]
        report["retracted_refs_count"] = self._count_retracted_refs(input_data)

        # 通知复盘 Agent 采集校验结果
        await self._send_message(
            "reviewer",
            MessageType.RESULT,
            {"node_id": input_data.get("node_id", ""), "validation": report},
            trace_id=trace_id,
        )

        logger.info(
            "validator_done",
            trace_id=trace_id,
            passed=report["passed"],
            score=report["score"],
            deterministic=citation_report is not None,
        )
        return report

    def _run_citation_checks(self, input_data: dict[str, Any]) -> ValidationReport | None:
        """确定性引用校验；未配置校验器或缺少类型化结果时返回 None。

        `reference_verifications`（Issue #3）为工作流预先产出的外部核验结论：
        逐条 `ReferenceVerification.model_validate`，非法条目跳过而不让整次校验失败；
        未提供时按原行为调用（报告形状不变）。
        """
        if self._citation_validator is None:
            return None
        execution_payload = input_data.get("execution_result")
        if not isinstance(execution_payload, dict):
            return None
        try:
            execution = ExecutionResult.model_validate(execution_payload)
        except PydanticValidationError:
            return None

        pool_payload = input_data.get("evidence_pool")
        evidence_pool: list[Evidence] | None = None
        if isinstance(pool_payload, list):
            evidence_pool = []
            for item in pool_payload:
                try:
                    evidence_pool.append(Evidence.model_validate(item))
                except PydanticValidationError:
                    continue
        return self._citation_validator.validate(
            execution,
            self._required_sections,
            evidence_pool=evidence_pool,
            reference_verifications=self._reference_verifications(input_data),
            content_role=self._content_role(input_data),
        )

    @staticmethod
    def _evidence_pool(input_data: dict[str, Any]) -> list[Evidence]:
        """解析 `evidence_pool` 载荷；缺失或类型不符时返回空列表。"""
        payload = input_data.get("evidence_pool")
        if not isinstance(payload, list):
            return []
        evidence: list[Evidence] = []
        for item in payload:
            try:
                evidence.append(Evidence.model_validate(item))
            except PydanticValidationError:
                continue
        return evidence

    @staticmethod
    def _is_search_node(input_data: dict[str, Any]) -> bool:
        """是否为程序化检索节点（search）。"""
        payload = input_data.get("execution_result")
        if not isinstance(payload, dict):
            return False
        return str(payload.get("node_type") or "") == "search"

    @staticmethod
    def _is_authored(input_data: dict[str, Any], *, content_role: ContentRole) -> bool:
        """是否值得做论断级核验（Issue #31 Phase 2）。

        只核验**作者正文**：``search``/``validate``/``review`` 的内容是上游输出
        的拼接，其中的 ``[E#]`` 已经属于更早的 extract/write 节点——重叠核验会
        重复扣费、也会让同一句话出现两份互相矛盾的判定。

        没有 ``node_type`` 的历史负载一律跳过：核验是增强项，宁可少核一次，
        也不要靠推断去猜「这段文字是不是作者正文」。
        """
        payload = input_data.get("execution_result")
        if not isinstance(payload, dict):
            return False
        node_type = str(payload.get("node_type") or "")
        explicit = str(payload.get("content_role") or "")
        if explicit == "aggregated" or node_type in {"search", "validate", "review"}:
            return False
        return node_type in {"extract", "write"} or explicit == "authored"

    async def _run_claim_checks(self, input_data: dict[str, Any]) -> list[ClaimCheckResult]:
        """抽取带引用的句子 → 绑定证据 → 批量 LLM 判定。

        失败一律降级为「无法核验」，绝不抛出——核验是增强项，不能拖垮任务。
        """
        payload = input_data.get("execution_result")
        if not isinstance(payload, dict):
            return []
        try:
            execution = ExecutionResult.model_validate(payload)
        except PydanticValidationError:
            return []

        claims = extract_cited_claims(execution.content, claim_prefix=f"{execution.node_id}:")
        if not claims:
            return []
        items = build_claim_evidence_list(
            claims,
            self._evidence_pool(input_data),
            execution.cited_indices,
        )
        try:
            return await ClaimVerifier(self.llm).verify(items)  # type: ignore[arg-type]
        except Exception as exc:
            logger.warning("claim_checks_skipped", error=str(exc))
            return []

    def _count_retracted_refs(self, input_data: dict[str, Any]) -> int:
        """统计本节点引用中带撤稿标记的文献条数（Issue #31 Phase 1 的质量标记）。"""
        verifications = self._reference_verifications(input_data) or []
        count = 0
        for item in verifications:
            flags = getattr(item, "quality_flags", []) or []
            if item.is_retracted is True or "retracted" in flags:
                count += 1
        return count

    @staticmethod
    def _content_role(input_data: dict[str, Any]) -> ContentRole:
        """解析内容角色（Issue #6）；缺失或非法一律回落到 `authored`（从严，不放宽检查）。"""
        value = input_data.get("content_role")
        return "aggregated" if value == "aggregated" else "authored"

    @staticmethod
    def _reference_verifications(input_data: dict[str, Any]) -> list[ReferenceVerification] | None:
        """解析 `reference_verifications` 载荷；缺失或类型不符时返回 None（保持原行为）。"""
        payload = input_data.get("reference_verifications")
        if not isinstance(payload, list):
            return None
        verifications: list[ReferenceVerification] = []
        for item in payload:
            try:
                verifications.append(ReferenceVerification.model_validate(item))
            except PydanticValidationError:
                # 单条脏数据只跳过该条，不影响其余核验结论进入报告
                logger.warning("reference_verification_payload_invalid")
                continue
        return verifications

    def _legacy_report(
        self,
        rule_report: dict[str, Any],
        semantic_report: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """非文献场景：保持原有「规则 40% + 语义 60%」评分语义。

        重试语义（Issue #6）：这条路径没有完整性检查，失败都属于「内容可重写」，
        故一律 `retryable`——避免一次有噪声的 LLM 判断直接转人工。
        """
        semantic = semantic_report or {"score": 0, "issues": [], "suggestions": []}
        overall = int(rule_report["score"] * 0.4 + semantic["score"] * 0.6)
        passed = overall >= 60 and rule_report["passed"]
        issues = rule_report["issues"] + semantic.get("issues", [])
        return {
            "passed": passed,
            "score": overall,
            "rule_check": rule_report,
            "semantic_check": semantic,
            "issues": issues,
            "suggestions": semantic.get("suggestions", []),
            "checks": [
                ValidationCheck(
                    name="rules",
                    passed=rule_report["passed"],
                    message="; ".join(rule_report["issues"]) or "规则校验通过",
                ).model_dump()
            ],
            "retryable": not passed,
            "feedback": "; ".join(issues),
        }

    def _citation_report(
        self,
        rule_report: dict[str, Any],
        citation_report: ValidationReport,
        semantic_report: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """文献场景：确定性检查优先，语义校验只作为补充。

        重试语义（Issue #6）：**可修复的失败必须可重试**。
        语义判定失败意味着「模型可以重写改好」（重试会把 feedback 作为 `validation_feedback`
        交给重新生成的节点，语义检查本身也产出 suggestions），因此并入 `retryable`；
        只有确定性完整性失败（证据越权/标识非法等，重试无意义）才不可重试。
        """
        checks = list(citation_report.checks)
        checks.append(
            ValidationCheck(
                name="rules",
                passed=rule_report["passed"],
                message="; ".join(rule_report["issues"]) or "规则校验通过",
            )
        )

        semantic_failed = False
        if semantic_report is None:
            passed = False
            score = round(citation_report.score * 0.6 + rule_report["score"] * 0.4, 2)
        else:
            semantic_passed = semantic_report["score"] >= 60
            semantic_failed = not semantic_passed
            checks.append(
                ValidationCheck(
                    name="semantic_consistency",
                    passed=semantic_passed,
                    message="; ".join(semantic_report.get("issues", [])) or "语义校验通过",
                )
            )
            passed = citation_report.passed and rule_report["passed"] and semantic_passed
            score = round(
                citation_report.score * 0.6
                + rule_report["score"] * 0.2
                + semantic_report["score"] * 0.2,
                2,
            )

        issues = [check.message for check in checks if not check.passed]
        return {
            "passed": passed,
            "score": score,
            "checks": [check.model_dump() for check in checks],
            "retryable": bool(
                citation_report.retryable or not rule_report["passed"] or semantic_failed
            ),
            "feedback": "; ".join(issues),
            "rule_check": rule_report,
            "semantic_check": semantic_report or {},
            "issues": issues,
            "suggestions": (semantic_report or {}).get("suggestions", []),
        }

    def check_rules(
        self,
        result: str,
        *,
        content_role: ContentRole = "authored",
    ) -> dict[str, Any]:
        """规则引擎：格式/长度/关键词硬性校验（按内容角色区分）。

        ## 为什么 `max_length` 只对作者正文生效（2026-10-27）

        真实任务里 `review` 节点的**机械汇总**（上游输出拼接）撞上了
        `max_length=10000`：`长度超限：10230 > 10000` → `waiting_human`。

        这不是「阈值太小」：汇总长度随上游节点数量线性增长，**必然超限**，
        且**重试无法修复**（重试只会重新汇总同样内容）。

        `max_length` 是「别写太长」的**写作约束**，对机器汇总没有意义 ——
        该有多长就有多长。这与 Issue #6 是同一类错误：把作者级检查施加到
        机器汇总内容上。Issue #6 修了 `citation_validator` 的
        `duplicate_paragraph_ratio`，但这里的 `max_length` 没跟着修。

        `min_length` 与 `required_keywords` **仍对汇总生效**：
        空汇总、缺失必需章节确实是问题，与「谁写的」无关。
        """
        issues: list[str] = []
        length = len(result)

        min_len = self._rules.get("min_length", 0)
        max_len = self._rules.get("max_length", 999999)
        required_keywords: list[str] = self._rules.get("required_keywords", [])

        if length < min_len:
            issues.append(f"长度不足：{length} < {min_len}")
        # 只有作者正文受上限约束（见 docstring）
        if content_role != "aggregated" and length > max_len:
            issues.append(f"长度超限：{length} > {max_len}")
        for kw in required_keywords:
            if kw not in result:
                issues.append(f"缺失关键词：{kw}")

        passed = len(issues) == 0
        # 规则评分：无问题 100，每个问题扣 20，最低 0
        score = max(0, 100 - len(issues) * 20)
        return {"passed": passed, "score": score, "issues": issues}

    def _check_rules(
        self, result: str, *, content_role: ContentRole = "authored"
    ) -> dict[str, Any]:
        """内部别名，保持既有调用点不变。"""
        return self.check_rules(result, content_role=content_role)

    async def _check_semantic(self, title: str, result: str) -> dict[str, Any]:
        """LLM 语义自检：逻辑一致性/事实准确性。

        无 LLM 时降级为默认通过，避免阻塞流程。
        """
        if self.llm is None:
            return {"score": 80, "issues": [], "suggestions": []}

        from app.core.config import get_settings

        # 校验是「大 prompt + JSON 长输出」的典型场景，90s 全局超时实测不够
        # （Issue #18：n2 一次 90s、n5 连续 3 次 90s 全靠人工救）；
        # per-request 覆盖为专用值，0=跟随全局 request_timeout。
        validation_timeout = get_settings().llm.call_timeout_validation
        response = await self.llm.chat(
            [
                # 静态口径进 system：跨节点复用同一缓存前缀（Issue #22）
                Message(role=MessageRole.SYSTEM, content=_CHECK_SYSTEM),
                Message(
                    role=MessageRole.USER,
                    content=_CHECK_PROMPT.format(title=title, result=result),
                ),
            ],
            temperature=0.2,
            # 输出上限（Issue #22）：已限 issues/suggestions 条数与字数，1200 足够
            max_tokens=get_settings().llm.max_tokens_validator,
            timeout=validation_timeout or None,
        )

        import json

        try:
            data = json.loads(response.content or "{}")
        except json.JSONDecodeError:
            data = {}

        logic_ok = data.get("logic_consistent", True)
        fact_ok = data.get("factual_accurate", True)
        issues = data.get("issues", [])
        suggestions = data.get("suggestions", [])

        # 语义评分：逻辑与事实各占 50 分
        score = (50 if logic_ok else 0) + (50 if fact_ok else 0)
        return {
            "score": score,
            "issues": issues,
            "suggestions": suggestions,
            "logic_consistent": logic_ok,
            "factual_accurate": fact_ok,
        }
