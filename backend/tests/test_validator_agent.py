"""校验 Agent 单元测试：规则引擎、LLM 自检、综合评分。"""

import json

import pytest

from app.llm.models import LLMResponse, MessageRole, Usage
from app.orchestration.agents.validator import ValidatorAgent
from app.orchestration.communication import InMemoryMessageBus
from app.orchestration.messages import MessageType
from tests.llm.mock_llm import MockLLM


@pytest.mark.asyncio
class TestValidatorRules:
    """规则引擎测试。"""

    async def test_short_result_fails_min_length(self) -> None:
        """结果过短未通过最小长度规则。"""
        agent = ValidatorAgent(rules={"min_length": 50})
        report = await agent.execute({"title": "任务", "result": "太短了", "trace_id": "t1"})
        assert not report["passed"]
        assert report["rule_check"]["score"] < 100
        assert any("长度不足" in issue for issue in report["issues"])

    async def test_required_keyword_missing(self) -> None:
        """缺失必含关键词不通过。"""
        agent = ValidatorAgent(rules={"min_length": 1, "required_keywords": ["结论"]})
        report = await agent.execute(
            {"title": "任务", "result": "这是没有关键词的结果内容", "trace_id": "t1"}
        )
        assert not report["passed"]
        assert any("结论" in issue for issue in report["issues"])

    async def test_valid_result_passes_rules(self) -> None:
        """符合规则的结果通过。"""
        agent = ValidatorAgent(rules={"min_length": 5, "required_keywords": []})
        report = await agent.execute(
            {"title": "任务", "result": "这是一个有效的结果内容", "trace_id": "t1"}
        )
        assert report["rule_check"]["passed"]
        assert report["rule_check"]["score"] == 100


@pytest.mark.asyncio
class TestValidatorSemantic:
    """LLM 语义自检测试。"""

    async def test_llm_semantic_check_high_score(self) -> None:
        """LLM 判定逻辑一致且事实准确时评分高。"""
        response = LLMResponse(
            content=json.dumps(
                {
                    "logic_consistent": True,
                    "factual_accurate": True,
                    "issues": [],
                    "suggestions": [],
                }
            ),
            finish_reason="stop",
            usage=Usage(),
            role=MessageRole.ASSISTANT,
        )
        mock = MockLLM(responses=[response])
        agent = ValidatorAgent(llm=mock, rules={"min_length": 1, "required_keywords": []})
        report = await agent.execute(
            {"title": "任务", "result": "有效的结果内容", "trace_id": "t1"}
        )
        assert report["semantic_check"]["score"] == 100
        assert report["passed"]

    async def test_llm_semantic_check_low_score(self) -> None:
        """LLM 判定有逻辑问题时评分低。"""
        response = LLMResponse(
            content=json.dumps(
                {
                    "logic_consistent": False,
                    "factual_accurate": True,
                    "issues": ["逻辑不连贯"],
                    "suggestions": ["重新组织"],
                }
            ),
            finish_reason="stop",
            usage=Usage(),
            role=MessageRole.ASSISTANT,
        )
        mock = MockLLM(responses=[response])
        agent = ValidatorAgent(llm=mock, rules={"min_length": 1, "required_keywords": []})
        report = await agent.execute(
            {"title": "任务", "result": "有效的结果内容", "trace_id": "t1"}
        )
        assert report["semantic_check"]["score"] == 50

    async def test_no_llm_degrades_to_default_score(self) -> None:
        """无 LLM 时语义校验降级为默认 80 分。"""
        agent = ValidatorAgent(rules={"min_length": 1, "required_keywords": []})
        report = await agent.execute(
            {"title": "任务", "result": "有效的结果内容", "trace_id": "t1"}
        )
        assert report["semantic_check"]["score"] == 80


@pytest.mark.asyncio
class TestValidatorIntegration:
    """校验 Agent 集成测试。"""

    async def test_sends_result_to_reviewer(self) -> None:
        """校验结果通过消息总线通知复盘 Agent。"""
        bus = InMemoryMessageBus()
        reviewer_q = await bus.subscribe("reviewer")

        agent = ValidatorAgent(message_bus=bus, rules={"min_length": 1, "required_keywords": []})
        await agent.execute({"node_id": "n1", "title": "任务", "result": "结果", "trace_id": "t1"})

        msg = await __import__("asyncio").wait_for(reviewer_q.get(), timeout=1.0)
        assert msg.message_type is MessageType.RESULT
        assert msg.to_agent == "reviewer"
        assert "validation" in msg.content

    async def test_agent_type_is_validator(self) -> None:
        assert ValidatorAgent().agent_type == "validator"

    async def test_scoring_weights(self) -> None:
        """综合评分 = 规则 40% + 语义 60%。"""
        # 规则满分 100，语义 0 分 → 总分 40
        response = LLMResponse(
            content=json.dumps(
                {
                    "logic_consistent": False,
                    "factual_accurate": False,
                    "issues": [],
                    "suggestions": [],
                }
            ),
            finish_reason="stop",
            usage=Usage(),
            role=MessageRole.ASSISTANT,
        )
        mock = MockLLM(responses=[response])
        agent = ValidatorAgent(llm=mock, rules={"min_length": 1, "required_keywords": []})
        report = await agent.execute({"title": "任务", "result": "有效结果", "trace_id": "t1"})
        assert report["score"] == 40  # 100*0.4 + 0*0.6
