"""Issue #32 Phase 1：预设工作流契约、任务需求注入与意图分类单测。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.orchestration.contracts import LiteratureReviewRequest
from app.orchestration.services.task_execution import TaskExecution
from app.schemas.task import TaskCreate
from app.services.intent_service import fallback, parse_llm_json


def test_task_create_defaults_to_literature_review_and_rejects_invalid_preset() -> None:
    """未传 workflow_preset 时默认 literature_review；非法值必须被拒绝。"""
    payload = TaskCreate(topic="多智能体协作综述分析", keywords=["multi-agent"])
    assert payload.workflow_preset == "literature_review"
    contract = payload.to_contract()
    assert contract.workflow_preset == "literature_review"

    gaps_payload = TaskCreate(
        topic="检索增强生成的研究空白分析",
        keywords=["RAG", "research gaps"],
        workflow_preset="research_gaps",
    )
    assert gaps_payload.to_contract().workflow_preset == "research_gaps"

    with pytest.raises(ValidationError):
        TaskCreate(
            topic="非法预设测试主题",
            keywords=["test"],
            workflow_preset="unknown_preset",  # type: ignore[arg-type]
        )


def test_describe_and_requirement_inject_preset_directive_and_fallback_for_legacy_task() -> None:
    """_describe 与 _requirement 均按 preset 注入目标指令；旧任务缺键回落默认。"""
    req = LiteratureReviewRequest(
        topic="大语言模型幻觉缓解方法研究空白",
        keywords=["LLM", "hallucination"],
        workflow_preset="research_gaps",
    )
    desc = TaskExecution._describe(req)
    assert "研究空白分析" in desc

    # replan 场景：从 task.config 重建需求，仍保留研究空白导向
    task_gaps = SimpleNamespace(
        title=req.topic,
        config=req.model_dump(mode="json"),
    )
    requirement_gaps = TaskExecution._requirement(task_gaps)  # type: ignore[arg-type]
    assert task_gaps.config["workflow_preset"] == "research_gaps"
    assert "研究空白分析" in requirement_gaps

    # 历史任务缺失 workflow_preset 键：安全回落 literature_review
    legacy_task = SimpleNamespace(
        title="历史任务主题",
        config={"keywords": ["RAG"], "output_language": "zh"},
    )
    requirement_legacy = TaskExecution._requirement(legacy_task)  # type: ignore[arg-type]
    assert "文献综述" in requirement_legacy


def test_intent_service_parses_research_gaps_and_fail_opens_on_invalid_preset() -> None:
    """意图分类支持 research_gaps 识别，非法或缺失值 fail-open 回落 literature_review。"""
    parsed = parse_llm_json(
        '{"intent":"literature_review","workflow_preset":"research_gaps",'
        '"topic":"RAG 评估方法的研究空白","keywords":["RAG","evaluation"]}',
        "帮我找一下 RAG 评估还有哪些研究空白",
    )
    assert parsed is not None
    assert parsed.intent == "literature_review"
    assert parsed.workflow_preset == "research_gaps"

    # 非法预设名 fail-open 回落 literature_review
    bad_preset = parse_llm_json(
        '{"intent":"literature_review","workflow_preset":"hacked_mode",'
        '"topic":"RAG 综述","keywords":["RAG"]}',
        "综述 RAG",
    )
    assert bad_preset is not None
    assert bad_preset.workflow_preset == "literature_review"

    fb = fallback("帮我找一下多智能体空白")
    assert fb.workflow_preset == "literature_review"


def test_planner_template_keeps_prefix_cache_separator() -> None:
    """保护 Issue #22 前缀缓存：规划模板静态区仍通过 --- 与动态需求区隔离。"""
    template_path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "llm"
        / "templates"
        / "literature_planner_system.j2"
    )
    content = template_path.read_text(encoding="utf-8")
    assert "\n---\n" in content
    static_head, dynamic_tail = content.split("\n---\n", 1)
    assert "{{ requirement }}" not in static_head
    assert "{{ requirement }}" in dynamic_tail
