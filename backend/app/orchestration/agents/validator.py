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
    Evidence,
    ExecutionResult,
    ValidationCheck,
    ValidationReport,
)
from app.orchestration.messages import MessageType
from app.tools.literature.models import ReferenceVerification

logger = get_logger(__name__)

_CHECK_PROMPT = """请校验以下执行结果的逻辑一致性与事实准确性。

子任务标题：{title}
执行结果：{result}

判定标准（请严格按此口径，不要因为内容"覆盖面广"就判为不匹配）：
- logic_consistent：结论之间不矛盾、不含无效推理；
- factual_accurate：**只**判断是否存在无明显事实错误或与自身陈述冲突之处；
- 若本节点是"检索/抽取"类节点，其职责就是如实罗列语料中的多篇文献，
  条目涉及不同主题是正常的，**不属于**"与子任务主题不匹配"，不要据此扣分；
- 只有当内容与子任务标题明显无关（例如子任务要求写综述却只输出目录）才判 false。

请以 JSON 格式返回（仅输出 JSON）：
{{
  "logic_consistent": true/false,
  "factual_accurate": true/false,
  "issues": ["问题描述1", "问题描述2"],
  "suggestions": ["改进建议1"]
}}
"""

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

        # 步骤3：LLM 语义自检（仅当确定性安全/完整性检查通过）
        integrity_failed = (
            citation_report is not None
            and not citation_report.passed
            and not citation_report.retryable
        )
        semantic_report = None if integrity_failed else await self._check_semantic(title, result)

        if citation_report is None:
            report = self._legacy_report(rule_report, semantic_report)
        else:
            report = self._citation_report(rule_report, citation_report, semantic_report)

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

        response = await self.llm.chat(
            [
                Message(role=MessageRole.SYSTEM, content="你是质量校验专家，只输出 JSON。"),
                Message(
                    role=MessageRole.USER,
                    content=_CHECK_PROMPT.format(title=title, result=result),
                ),
            ],
            temperature=0.2,
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
