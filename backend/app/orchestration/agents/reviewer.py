"""复盘 Agent：日志收集 → 错误归因 → 性能统计。

职责：
1. 日志收集：汇总全链路执行日志（成功/失败/重试）
2. 错误归因：LLM 驱动的根因分析，定位失败原因
3. 性能统计：成功率/重试次数/平均耗时等指标

设计要点：
- 日志通过 Agent 间消息异步采集，不阻塞主流程
- LLM 归因可降级：无 LLM 时返回规则统计，保证可用
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.logging import get_logger
from app.llm.models import Message, MessageRole
from app.orchestration.base_agent import BaseAgent
from app.orchestration.messages import AgentMessage

logger = get_logger(__name__)

_ATTRIBUTION_PROMPT = """请分析以下任务执行日志，定位失败根因并给出改进建议。

执行日志（JSON）：
{logs}

请以 JSON 格式返回（仅输出 JSON）：
{{
  "root_cause": "根因分析（一句话）",
  "failure_pattern": "失败模式分类（如 超时/依赖缺失/逻辑错误）",
  "improvement": "改进建议",
  "confidence": 0.0-1.0
}}
"""


class ReviewerAgent(BaseAgent):
    """复盘 Agent：收集执行日志，归因错误，生成性能统计报告。"""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # 执行日志缓冲：按 trace_id 聚合，避免跨任务混淆
        self._logs: dict[str, list[dict[str, Any]]] = {}

    @property
    def agent_type(self) -> str:
        return "reviewer"

    async def _handle_message(self, message: AgentMessage) -> dict[str, Any] | None:
        """接收并缓存执行日志/错误消息。

        复盘 Agent 被动采集，消息驱动而非主动拉取（解耦）。
        """
        trace_id = message.metadata.get("trace_id", "default")
        entry = {
            "from_agent": message.from_agent,
            "message_type": message.message_type.value,
            "content": message.content,
            "timestamp": message.timestamp.isoformat(),
        }
        self._logs.setdefault(trace_id, []).append(entry)
        return None

    async def _run(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """执行复盘：文献场景基于持久化结果组装；否则走日志统计 → 归因。

        input_data 需包含 trace_id（指定要复盘的任务链路）。
        """
        if isinstance(input_data.get("results"), list):
            return await self._build_literature_review(input_data)

        trace_id = input_data.get("trace_id", "default")

        # 步骤1：收集该链路日志
        logs = self._logs.get(trace_id, [])
        if not logs:
            # 兼容直接传入 logs 的场景（如测试）
            logs = input_data.get("logs", [])

        # 步骤2：性能统计
        stats = self._compute_stats(logs)

        # 步骤3：错误归因（仅在有失败时执行）
        root_cause: dict[str, Any] = {}
        if stats["error_count"] > 0:
            root_cause = await self._attribute_errors(logs)

        report = {
            "trace_id": trace_id,
            "summary": self._build_summary(stats, root_cause),
            "stats": stats,
            "root_cause": root_cause,
            "log_count": len(logs),
        }
        logger.info(
            "reviewer_done",
            trace_id=trace_id,
            total_logs=len(logs),
            error_count=stats["error_count"],
        )
        return report

    @staticmethod
    def _normalize_validations(payloads: list[Any]) -> list[Any]:
        """把已持久化的校验结果规范为 `ValidationReport`。

        通用（非文献）校验器会额外返回 rule_check / semantic_check / issues 等键，
        这里只取 ValidationReport 字段；无法解析的条目直接跳过，避免复盘整体失败。
        """
        from app.orchestration.contracts import ValidationReport

        known_fields = set(ValidationReport.model_fields)
        normalized: list[Any] = []
        for item in payloads:
            if isinstance(item, ValidationReport):
                normalized.append(item)
                continue
            if not isinstance(item, dict):
                continue
            try:
                normalized.append(
                    ValidationReport.model_validate(
                        {key: value for key, value in item.items() if key in known_fields}
                    )
                )
            except Exception:
                logger.warning("reviewer_validation_skipped")
        return normalized

    async def _build_literature_review(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """文献场景复盘：只消费已持久化的 ExecutionResult / ValidationReport / 事件统计。

        不调用 arXiv、RAG 或文件工具；LLM 仅用于润色摘要与局限性（无 LLM 时纯规则组装，
        结果保持不变）。
        """
        results_payload = input_data.get("results") or []
        if not results_payload:
            raise ValueError("复盘缺少节点结果")
        from app.orchestration.contracts import ExecutionResult

        results = [ExecutionResult.model_validate(item) for item in results_payload]
        validations = self._normalize_validations(input_data.get("validations") or [])
        topic = str(input_data.get("topic") or "文献综述")
        event_stats = dict(input_data.get("event_stats") or {})

        body_parts: list[str] = []
        citations: dict[str, dict[str, Any]] = {}
        for result in results:
            content = (result.content or "").strip()
            if content:
                body_parts.append(content)
            for item in result.evidence:
                url = str(item.source_url) if item.source_url else ""
                if url and url not in citations:
                    citations[url] = {
                        "title": item.excerpt[:60],
                        "source_url": url,
                        "doc_id": str(item.doc_id) if item.doc_id else None,
                        "chunk_id": str(item.chunk_id) if item.chunk_id else None,
                    }
            for artifact in result.artifacts:
                url = str(artifact.get("source_url") or "")
                if url and url not in citations:
                    citations[url] = {
                        "title": str(artifact.get("title") or artifact.get("source_id") or ""),
                        "source_url": url,
                        "source_id": artifact.get("source_id"),
                    }

        bibliography = [
            {"index": index, **entry} for index, entry in enumerate(citations.values(), start=1)
        ]
        scores = self._literature_scores(validations)
        issues = [
            check.message
            for validation in validations
            for check in validation.checks
            if not check.passed
        ] + [validation.feedback for validation in validations if validation.feedback]

        abstract = self._first_paragraph(body_parts[0] if body_parts else "")
        limitations: list[str] = [
            validation.feedback for validation in validations if validation.feedback
        ]

        if self.llm is not None:
            polished = await self._polish_literature_review(
                topic, results, validations, event_stats
            )
            abstract = str(polished.get("abstract") or abstract)
            refined = polished.get("limitations")
            if isinstance(refined, list) and refined:
                limitations = [str(item) for item in refined]

        final_markdown = self._compose_review_markdown(
            topic=topic,
            abstract=abstract,
            body="\n\n".join(body_parts),
            limitations=limitations or ["（无）"],
            bibliography=bibliography,
        )
        stats = {
            "node_count": len(results),
            "event_count": int(event_stats.get("event_count", 0)),
            "total_tokens": sum(
                result.prompt_tokens + result.completion_tokens for result in results
            ),
            "documents": len(citations),
            "validations": len(validations),
            "title": topic,
            "abstract": abstract,
            "limitations": limitations,
        }
        logger.info(
            "reviewer_literature_done",
            topic=topic,
            nodes=len(results),
            citations=len(citations),
        )
        return {
            "summary": f"共 {len(results)} 个节点完成，引用 {len(citations)} 条文献。",
            "final_markdown": final_markdown,
            "bibliography": bibliography,
            "scores": scores,
            "issues": issues,
            "stats": stats,
        }

    async def _polish_literature_review(
        self,
        topic: str,
        results: list[Any],
        validations: list[Any],
        event_stats: dict[str, Any],
    ) -> dict[str, Any]:
        """可选 LLM 润色：失败时静默回退到确定性组装。"""
        # 方法内导入：避免 agents 包与 workflows 包互相导入
        from app.orchestration.workflows.prompts import render_reviewer_prompt

        llm = self.llm
        if llm is None:  # 无 LLM 时直接跳过润色（调用方已做确定性组装）
            return {}

        prompt = render_reviewer_prompt(
            topic=topic,
            results_json=json.dumps(
                [item.model_dump(mode="json") for item in results], ensure_ascii=False
            ),
            validations_json=json.dumps(
                [item.model_dump(mode="json") for item in validations], ensure_ascii=False
            ),
            event_stats_json=json.dumps(event_stats, ensure_ascii=False),
        )
        try:
            response = await llm.chat(
                [
                    Message(
                        role=MessageRole.SYSTEM,
                        content="你是文献综述复盘专家，只输出 JSON。",
                    ),
                    Message(role=MessageRole.USER, content=prompt),
                ],
                temperature=0.2,
            )
            payload = json.loads(response.content or "{}")
        except Exception as exc:
            logger.warning("reviewer_polish_failed", error=str(exc))
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _literature_scores(validations: list[Any]) -> dict[str, float]:
        if not validations:
            return {"citation_traceability": 0.0, "quality": 0.0}
        mean = sum(item.score for item in validations) / len(validations) / 100.0
        score = round(mean, 4)
        return {"citation_traceability": score, "quality": score}

    @staticmethod
    def _first_paragraph(text: str) -> str:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and not stripped.startswith("-"):
                return stripped[:300]
        return ""

    @staticmethod
    def _compose_review_markdown(
        *,
        topic: str,
        abstract: str,
        body: str,
        limitations: list[str],
        bibliography: list[dict[str, Any]],
    ) -> str:
        parts = [f"# {topic}"]
        if abstract:
            parts.append(f"## 摘要\n{abstract}")
        if body:
            parts.append(body)
        parts.append("## 局限性\n" + "\n".join(f"- {item}" for item in limitations))
        if bibliography:
            lines = []
            for entry in bibliography:
                title = entry.get("title") or entry.get("source_id") or "未命名文献"
                url = entry.get("source_url") or ""
                lines.append(f"- [{entry['index']}] {title} — {url}".rstrip(" —"))
            parts.append("## 参考文献\n" + "\n".join(lines))
        return "\n\n".join(parts)

    def _compute_stats(self, logs: list[dict[str, Any]]) -> dict[str, Any]:
        """从日志列表计算性能统计指标。"""
        total = len(logs)
        # 按消息类型统计
        by_type: dict[str, int] = {}
        error_count = 0
        agent_set: set[str] = set()

        for entry in logs:
            msg_type = entry.get("message_type", "unknown")
            by_type[msg_type] = by_type.get(msg_type, 0) + 1
            if msg_type == "error":
                error_count += 1
            agent_set.add(entry.get("from_agent", "unknown"))

        success_count = by_type.get("result", 0)
        # 成功率：成功结果数 / (成功+失败)，无数据时为 0
        total_attempts = success_count + error_count
        success_rate = (success_count / total_attempts) if total_attempts > 0 else 0.0

        return {
            "total_logs": total,
            "by_type": by_type,
            "success_count": success_count,
            "error_count": error_count,
            "success_rate": round(success_rate, 4),
            "agents_involved": sorted(agent_set),
        }

    async def _attribute_errors(self, logs: list[dict[str, Any]]) -> dict[str, Any]:
        """LLM 驱动的错误根因分析。

        无 LLM 时降级为规则归因：提取首个错误的 error_type。
        """
        if self.llm is None:
            return self._rule_based_attribution(logs)

        # 仅传入错误相关日志，减少 token 消耗（计算优化）
        error_logs = [e for e in logs if e.get("message_type") == "error"]
        response = await self.llm.chat(
            [
                Message(role=MessageRole.SYSTEM, content="你是根因分析专家，只输出 JSON。"),
                Message(
                    role=MessageRole.USER,
                    content=_ATTRIBUTION_PROMPT.format(
                        logs=json.dumps(error_logs, ensure_ascii=False)
                    ),
                ),
            ],
            temperature=0.3,
        )
        try:
            return json.loads(response.content or "{}")
        except json.JSONDecodeError:
            logger.warning("reviewer_attribution_parse_failed")
            return self._rule_based_attribution(logs)

    @staticmethod
    def _rule_based_attribution(logs: list[dict[str, Any]]) -> dict[str, Any]:
        """规则归因降级：提取首个错误类型作为根因。"""
        for entry in logs:
            if entry.get("message_type") == "error":
                content = entry.get("content", {})
                return {
                    "root_cause": content.get("error", "未知错误"),
                    "failure_pattern": content.get("error_type", "UNKNOWN"),
                    "improvement": "建议检查错误来源并重试",
                    "confidence": 0.5,
                }
        return {
            "root_cause": "无错误日志",
            "failure_pattern": "NONE",
            "improvement": "",
            "confidence": 1.0,
        }

    @staticmethod
    def _build_summary(stats: dict[str, Any], root_cause: dict[str, Any]) -> str:
        """生成人类可读的复盘摘要。"""
        rate = stats["success_rate"] * 100
        if stats["error_count"] == 0:
            return f"任务执行成功，成功率 {rate:.1f}%，共 {stats['total_logs']} 条日志。"
        cause = root_cause.get("root_cause", "未定位")
        return (
            f"任务存在异常，成功率 {rate:.1f}%，" f"错误 {stats['error_count']} 次，根因：{cause}。"
        )

    async def collect_from(self, source: Callable[[], Awaitable[list[dict[str, Any]]]]) -> None:
        """从外部数据源（如数据库日志表）异步采集日志。

        阶段4+ 可用此方法从 DB 拉取历史日志做批量复盘。
        """
        logs = await source()
        for entry in logs:
            trace_id = entry.get("trace_id", "default")
            self._logs.setdefault(trace_id, []).append(entry)
