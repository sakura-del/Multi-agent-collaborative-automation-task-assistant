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

from app.core.logging import get_logger
from app.llm.models import Message, MessageRole
from app.orchestration.base_agent import BaseAgent
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

    def __init__(self, *, rules: dict[str, Any] | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # 合并默认规则与自定义规则，允许按任务覆盖
        self._rules = {**_DEFAULT_RULES, **(rules or {})}

    @property
    def agent_type(self) -> str:
        return "validator"

    async def _run(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """执行校验：规则引擎 → LLM 自检 → 评分 → 报告。

        input_data 需包含 title、result。
        返回 {passed, score, rule_check, semantic_check, report}。
        """
        title = input_data["title"]
        result = input_data.get("result", "")
        trace_id = input_data.get("trace_id", "")

        # 步骤1：规则引擎校验（确定性，即时返回）
        rule_report = self._check_rules(result)

        # 步骤2：LLM 语义自检（逻辑一致性/事实准确性）
        semantic_report = await self._check_semantic(title, result)

        # 步骤3：综合评分（规则 40% + 语义 60%）
        rule_score = rule_report["score"]
        semantic_score = semantic_report["score"]
        overall = int(rule_score * 0.4 + semantic_score * 0.6)
        passed = overall >= 60 and rule_report["passed"]

        # 生成校验报告
        issues = rule_report["issues"] + semantic_report.get("issues", [])
        suggestions = semantic_report.get("suggestions", [])

        report = {
            "passed": passed,
            "score": overall,
            "rule_check": rule_report,
            "semantic_check": semantic_report,
            "issues": issues,
            "suggestions": suggestions,
        }

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
            passed=passed,
            score=overall,
        )
        return report

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
