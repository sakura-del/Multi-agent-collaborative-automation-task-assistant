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
        """执行复盘：统计 → 归因 → 报告。

        input_data 需包含 trace_id（指定要复盘的任务链路）。
        返回 {summary, stats, root_cause}。
        """
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
