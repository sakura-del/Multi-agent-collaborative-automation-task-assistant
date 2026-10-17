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
from app.knowledge.services.citation_validator import CitationValidator
from app.llm.models import Message, MessageRole
from app.orchestration.base_agent import BaseAgent
from app.orchestration.contracts import (
    Evidence,
    ExecutionResult,
    ValidationCheck,
    ValidationReport,
)
from app.orchestration.messages import MessageType

logger = get_logger(__name__)

_CHECK_PROMPT = """请校验以下执行结果的逻辑一致性与事实准确性。

子任务标题：{title}
执行结果：{result}

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

        # 步骤1：规则引擎校验（确定性，即时返回）
        rule_report = self._check_rules(result)

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
        """确定性引用校验；未配置校验器或缺少类型化结果时返回 None。"""
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
        )

    def _legacy_report(
        self,
        rule_report: dict[str, Any],
        semantic_report: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """非文献场景：保持原有「规则 40% + 语义 60%」评分语义。"""
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
            "retryable": not rule_report["passed"],
            "feedback": "; ".join(issues),
        }

    def _citation_report(
        self,
        rule_report: dict[str, Any],
        citation_report: ValidationReport,
        semantic_report: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """文献场景：确定性检查优先，语义校验只作为补充。"""
        checks = list(citation_report.checks)
        checks.append(
            ValidationCheck(
                name="rules",
                passed=rule_report["passed"],
                message="; ".join(rule_report["issues"]) or "规则校验通过",
            )
        )

        if semantic_report is None:
            passed = False
            score = round(citation_report.score * 0.6 + rule_report["score"] * 0.4, 2)
        else:
            semantic_passed = semantic_report["score"] >= 60
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
            "retryable": bool(citation_report.retryable or not rule_report["passed"]),
            "feedback": "; ".join(issues),
            "rule_check": rule_report,
            "semantic_check": semantic_report or {},
            "issues": issues,
            "suggestions": (semantic_report or {}).get("suggestions", []),
        }

    def _check_rules(self, result: str) -> dict[str, Any]:
        """规则引擎：格式/长度/关键词硬性校验。"""
        issues: list[str] = []
        length = len(result)

        min_len = self._rules.get("min_length", 0)
        max_len = self._rules.get("max_length", 999999)
        required_keywords: list[str] = self._rules.get("required_keywords", [])

        if length < min_len:
            issues.append(f"长度不足：{length} < {min_len}")
        if length > max_len:
            issues.append(f"长度超限：{length} > {max_len}")
        for kw in required_keywords:
            if kw not in result:
                issues.append(f"缺失关键词：{kw}")

        passed = len(issues) == 0
        # 规则评分：无问题 100，每个问题扣 20，最低 0
        score = max(0, 100 - len(issues) * 20)
        return {"passed": passed, "score": score, "issues": issues}

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
