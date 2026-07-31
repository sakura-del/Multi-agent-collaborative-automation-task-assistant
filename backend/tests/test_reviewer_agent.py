"""复盘 Agent 单元测试：日志收集、性能统计、错误归因。"""

import json

import pytest

from app.llm.models import LLMResponse, MessageRole, Usage
from app.orchestration.agents.reviewer import ReviewerAgent
from app.orchestration.messages import AgentMessage, MessageType
from tests.llm.mock_llm import MockLLM


@pytest.mark.asyncio
class TestReviewerStats:
    """性能统计测试。"""

    async def test_stats_with_no_errors(self) -> None:
        """全成功日志的统计指标正确。"""
        agent = ReviewerAgent()
        result = await agent.execute(
            {
                "trace_id": "t1",
                "logs": [
                    {"from_agent": "executor", "message_type": "result", "content": {}},
                    {"from_agent": "executor", "message_type": "result", "content": {}},
                ],
            }
        )
        assert result["stats"]["error_count"] == 0
        assert result["stats"]["success_count"] == 2
        assert result["stats"]["success_rate"] == 1.0

    async def test_stats_with_errors(self) -> None:
        """含错误的统计指标正确。"""
        agent = ReviewerAgent()
        result = await agent.execute(
            {
                "trace_id": "t1",
                "logs": [
                    {"from_agent": "executor", "message_type": "result", "content": {}},
                    {
                        "from_agent": "executor",
                        "message_type": "error",
                        "content": {"error": "超时"},
                    },
                    {
                        "from_agent": "executor",
                        "message_type": "error",
                        "content": {"error": "失败"},
                    },
                ],
            }
        )
        assert result["stats"]["error_count"] == 2
        assert result["stats"]["success_count"] == 1
        assert result["stats"]["success_rate"] == pytest.approx(1 / 3, abs=0.001)

    async def test_stats_agents_involved(self) -> None:
        """统计涉及的全部 Agent。"""
        agent = ReviewerAgent()
        result = await agent.execute(
            {
                "trace_id": "t1",
                "logs": [
                    {"from_agent": "planner", "message_type": "result", "content": {}},
                    {"from_agent": "executor", "message_type": "result", "content": {}},
                ],
            }
        )
        assert set(result["stats"]["agents_involved"]) == {"planner", "executor"}


@pytest.mark.asyncio
class TestReviewerAttribution:
    """错误归因测试。"""

    async def test_llm_attribution(self) -> None:
        """LLM 驱动的根因分析。"""
        response = LLMResponse(
            content=json.dumps(
                {
                    "root_cause": "API 超时",
                    "failure_pattern": "超时",
                    "improvement": "增加重试",
                    "confidence": 0.85,
                }
            ),
            finish_reason="stop",
            usage=Usage(),
            role=MessageRole.ASSISTANT,
        )
        mock = MockLLM(responses=[response])
        agent = ReviewerAgent(llm=mock)

        result = await agent.execute(
            {
                "trace_id": "t1",
                "logs": [
                    {
                        "from_agent": "executor",
                        "message_type": "error",
                        "content": {"error": "timeout"},
                    },
                ],
            }
        )
        assert result["root_cause"]["root_cause"] == "API 超时"
        assert result["root_cause"]["confidence"] == 0.85

    async def test_rule_based_attribution_fallback(self) -> None:
        """无 LLM 时降级为规则归因。"""
        agent = ReviewerAgent()
        result = await agent.execute(
            {
                "trace_id": "t1",
                "logs": [
                    {
                        "from_agent": "executor",
                        "message_type": "error",
                        "content": {"error": "连接失败", "error_type": "ConnectionError"},
                    },
                ],
            }
        )
        assert result["root_cause"]["root_cause"] == "连接失败"
        assert result["root_cause"]["failure_pattern"] == "ConnectionError"

    async def test_no_errors_skips_attribution(self) -> None:
        """无错误时不执行归因。"""
        agent = ReviewerAgent()
        result = await agent.execute(
            {
                "trace_id": "t1",
                "logs": [
                    {"from_agent": "executor", "message_type": "result", "content": {}},
                ],
            }
        )
        assert result["root_cause"] == {}


@pytest.mark.asyncio
class TestReviewerMessageCollection:
    """消息驱动日志采集测试。"""

    async def test_collect_logs_via_message(self) -> None:
        """通过消息接收并缓存日志。"""
        agent = ReviewerAgent()
        msg = AgentMessage(
            from_agent="executor",
            to_agent="reviewer",
            message_type=MessageType.RESULT,
            content={"result": "done"},
            metadata={"trace_id": "trace-abc"},
        )
        await agent.handle_message(msg)

        result = await agent.execute({"trace_id": "trace-abc"})
        assert result["log_count"] == 1
        assert result["stats"]["success_count"] == 1

    async def test_collect_error_via_message(self) -> None:
        """通过 ERROR 消息采集错误日志。"""
        agent = ReviewerAgent()
        msg = AgentMessage(
            from_agent="executor",
            to_agent="reviewer",
            message_type=MessageType.ERROR,
            content={"error": "boom", "error_type": "RuntimeError"},
            metadata={"trace_id": "trace-err"},
        )
        await agent.handle_message(msg)

        result = await agent.execute({"trace_id": "trace-err"})
        assert result["stats"]["error_count"] == 1


class TestReviewerSummary:
    """摘要生成测试。"""

    def test_summary_no_errors(self) -> None:
        from app.orchestration.agents.reviewer import ReviewerAgent

        stats = {"success_rate": 1.0, "error_count": 0, "total_logs": 3}
        summary = ReviewerAgent._build_summary(stats, {})
        assert "成功" in summary
        assert "100.0%" in summary

    def test_summary_with_errors(self) -> None:
        from app.orchestration.agents.reviewer import ReviewerAgent

        stats = {"success_rate": 0.5, "error_count": 2, "total_logs": 4}
        root_cause = {"root_cause": "API 限流"}
        summary = ReviewerAgent._build_summary(stats, root_cause)
        assert "异常" in summary
        assert "API 限流" in summary
