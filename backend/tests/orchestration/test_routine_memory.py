"""Routines 跨期增量记忆单元测试（Issue #42）。"""

from __future__ import annotations

from types import SimpleNamespace

from app.orchestration.services.routine_memory import (
    MAX_PROMPT_CHARS,
    MAX_RECENT_UPDATES,
    RoutineMemoryBrief,
    bootstrap_memory_brief,
    format_memory_for_prompt,
    parse_memory_brief,
    update_memory_brief,
)


class TestBootstrapAndParseMemoryBrief:
    """初始化与容错解析测试。"""

    def test_bootstrap_prefers_evidence_matrix_gaps_and_weak_cells(self) -> None:
        task = SimpleNamespace(
            title="多智能体 RAG 综述",
            plan={
                "research_questions": [
                    "规划问题 A（不应覆盖矩阵）",
                ]
            },
            review_report={
                "final_markdown": (
                    "# 多智能体 RAG 综述\n\n"
                    "本文系统梳理了多智能体检索增强生成在忠实度评估与引文溯源中的最新进展。\n\n"
                    "## 细节"
                ),
                "evidence_matrix": {
                    "questions": [
                        "检索召回率是否提升？",
                        "长上下文延迟是否存在瓶颈？",
                        "跨模态医学幻觉是否缺少实证？",
                    ],
                    "cells": [
                        {"question_index": 0, "bucket": "strong", "claim_ids": ["c1"]},
                        {"question_index": 1, "bucket": "weak_or_caveat", "claim_ids": ["c2"]},
                        {"question_index": 2, "bucket": "gap", "claim_ids": []},
                    ],
                },
            },
        )

        brief = bootstrap_memory_brief(task)

        assert brief.open_gaps == [
            "长上下文延迟是否存在瓶颈？",
            "跨模态医学幻觉是否缺少实证？",
        ]
        assert brief.topic_summary.startswith("本文系统梳理了多智能体检索增强生成")
        assert brief.recent_updates == []
        assert brief.stale_queries == []
        assert brief.updated_at is not None

    def test_bootstrap_falls_back_to_plan_research_questions_without_matrix(self) -> None:
        task = SimpleNamespace(
            title="图神经网络综述",
            plan={"research_questions": ["动态图扩展性如何？", "异构图泛化能力？"]},
            review_report=None,
        )

        brief = bootstrap_memory_brief(task)

        assert brief.topic_summary == "图神经网络综述"
        assert brief.open_gaps == ["动态图扩展性如何？", "异构图泛化能力？"]

    def test_parse_memory_brief_handles_corrupted_or_extra_fields(self) -> None:
        assert parse_memory_brief(None) is None
        assert parse_memory_brief("not-a-dict") is None
        # extra="forbid"：未知字段降级返回 None 触发安全重建，不阻断刷新
        assert parse_memory_brief({"unexpected_field": 1}) is None

        valid = parse_memory_brief({"topic_summary": "基线", "open_gaps": ["空白1"]})
        assert valid is not None
        assert valid.open_gaps == ["空白1"]


class TestFormatMemoryForPrompt:
    """LOAD 渲染长度与空值约束测试。"""

    def test_none_returns_empty_string(self) -> None:
        assert format_memory_for_prompt(None) == ""

    def test_truncates_strictly_within_max_prompt_chars(self) -> None:
        brief = RoutineMemoryBrief(
            topic_summary="基" * 240,
            open_gaps=[f"空白{i}_" + "长" * 100 for i in range(6)],
            recent_updates=[f"更新{i}_" + "新" * 100 for i in range(5)],
        )

        rendered = format_memory_for_prompt(brief)

        assert rendered.startswith("已有研究状态")
        assert len(rendered) <= MAX_PROMPT_CHARS


class TestUpdateMemoryBrief:
    """OFFLOAD 确定性更新：引用闸门防污染、FIFO 截断、失效检索式计数与清零。"""

    def test_ignores_bullets_when_citation_not_ok(self) -> None:
        initial = RoutineMemoryBrief(topic_summary="基线", recent_updates=["旧要点"])

        updated = update_memory_brief(
            initial,
            bullets="- 越界引用的不可信要点 [E99]",
            citation_ok=False,
            new_count_by_query={"q1": 2},
        )

        assert updated.recent_updates == ["旧要点"]
        assert updated.query_empty_streaks == {"q1": 0}

    def test_fifo_truncates_recent_updates_to_max_five(self) -> None:
        current = RoutineMemoryBrief(topic_summary="基线")
        for idx in range(1, 8):
            current = update_memory_brief(
                current,
                bullets=f"- 第 {idx} 期新增关键结论 [E1]",
                citation_ok=True,
                new_count_by_query={"q1": 1},
            )

        assert len(current.recent_updates) == MAX_RECENT_UPDATES
        assert current.recent_updates == [f"第 {idx} 期新增关键结论 [E1]" for idx in range(3, 8)]

    def test_stale_query_after_three_empty_runs_and_resets_on_new_hit(self) -> None:
        current = RoutineMemoryBrief(topic_summary="基线")

        for expected_streak in (1, 2):
            current = update_memory_brief(
                current,
                bullets=None,
                citation_ok=True,
                new_count_by_query={"dead_q": 0, "alive_q": 1},
            )
            assert current.query_empty_streaks["dead_q"] == expected_streak
            assert current.stale_queries == []

        # 第 3 期仍为 0 新增 → 进入 stale_queries
        current = update_memory_brief(
            current,
            bullets=None,
            citation_ok=True,
            new_count_by_query={"dead_q": 0, "alive_q": 0},
        )
        assert current.query_empty_streaks["dead_q"] == 3
        assert current.stale_queries == ["dead_q"]

        # 一旦有新增即清零计数并移出 stale_queries
        current = update_memory_brief(
            current,
            bullets="- 复苏的新文献 [E1]",
            citation_ok=True,
            new_count_by_query={"dead_q": 2},
        )
        assert current.query_empty_streaks["dead_q"] == 0
        assert current.stale_queries == []
