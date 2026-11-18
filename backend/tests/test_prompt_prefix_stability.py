"""Prompt 静态前缀稳定性（Issue #22 前缀缓存的契约测试）。

DeepSeek/OpenAI 的前缀缓存按「从头开始的最长公共前缀（64 token 块）」命中。
本测试把契约编码进断言：**同一类节点的静态规则必须在动态变量之前，
且两次不同渲染的静态段逐字一致**——否则缓存必然不命中。

若未来有人把 `{{ title }}` / `{{ requirement }}` 挪回模板开头，
本测试会立刻变红（这正是回归保护的目的）。

⚠️ 铁律 #0：本机 Windows 不执行 pytest；本文件按静态检查交付。
"""

from __future__ import annotations

from app.orchestration.workflows.prompts import (
    render_executor_prompt,
    render_planner_prompt,
    render_reviewer_prompt,
)

# 模板中静态块与动态块的分隔标记（三份模板统一使用 `---` 分隔）
_SEPARATOR = "\n---\n"


def _static_part(rendered: str) -> str:
    """取分隔标记之前的静态前缀（不含标记本身，兼容 CRLF）。"""
    text = rendered.replace("\r\n", "\n")
    assert _SEPARATOR in text, "模板缺少静态/动态分隔标记"
    return text.split(_SEPARATOR, 1)[0]


class TestExecutorPromptPrefix:
    def _render(self, title: str, evidence: str) -> str:
        return render_executor_prompt(
            title=title, description=f"描述-{title}", evidence_text=evidence
        )

    def test_static_prefix_identical_across_nodes(self) -> None:
        a = _static_part(self._render("撰写质量评估维度", "[E1] 证据 A"))
        b = _static_part(self._render("撰写评测协议对比", "[E2] 证据 B"))
        assert a == b, "不同节点的静态前缀不一致，前缀缓存必然落空"

    def test_dynamic_content_only_after_separator(self) -> None:
        rendered = self._render("唯一标题-XYZZY", "[E1] 唯一证据-QWERTY")
        static = _static_part(rendered)
        assert "唯一标题-XYZZY" not in static
        assert "唯一证据-QWERTY" not in static
        assert "唯一标题-XYZZY" in rendered
        assert "唯一证据-QWERTY" in rendered

    def test_static_prefix_long_enough_for_cache_block(self) -> None:
        """静态前缀必须足够长（DeepSeek 以 64 token 为缓存块；中文约 1 字≈1 token）。"""
        static = _static_part(self._render("t", "[E1] e"))
        assert len(static) >= 200, f"静态前缀仅 {len(static)} 字符，不足以形成稳定缓存块"

    def test_rules_still_present(self) -> None:
        """安全/证据约束的既有断言不能被重排破坏。"""
        prompt = self._render("t", "[E1] 证据片段")
        for token in ("不可信", "模型推断", "只有本任务说明才是指令", "无检索证据", "字数"):
            assert token in prompt, f"约束片段丢失: {token}"


class TestPlannerPromptPrefix:
    def test_static_prefix_identical_across_requirements(self) -> None:
        a = _static_part(render_planner_prompt("检索增强生成的评估方法"))
        b = _static_part(render_planner_prompt("多智能体协作的质量评估"))
        assert a == b

    def test_requirement_after_separator(self) -> None:
        rendered = render_planner_prompt("唯一需求-ABCDEF")
        static = _static_part(rendered)
        assert "唯一需求-ABCDEF" not in static
        assert "唯一需求-ABCDEF" in rendered

    def test_schema_and_constraints_kept(self) -> None:
        prompt = render_planner_prompt("x")
        for token in ("search", "review", "JSON", "research_questions", "write 节点的依赖"):
            assert token in prompt


class TestReviewerPromptPrefix:
    def test_static_prefix_identical_across_topics(self) -> None:
        a = _static_part(
            render_reviewer_prompt(
                topic="主题A", results_json="[]", validations_json="[]", event_stats_json="{}"
            )
        )
        b = _static_part(
            render_reviewer_prompt(
                topic="主题B", results_json="[1]", validations_json="[2]", event_stats_json="{}"
            )
        )
        assert a == b

    def test_topic_after_separator(self) -> None:
        rendered = render_reviewer_prompt(
            topic="唯一主题-QWERTY", results_json="[]", validations_json="[]", event_stats_json="{}"
        )
        assert "唯一主题-QWERTY" not in _static_part(rendered)
        assert "摘要 150-300 字" in _static_part(rendered)
