"""PRISMA 2020 流程图渲染测试（Phase 2 · PRISMA Task 4）。

输入是 `ScreeningService.prisma_counts()` 的派生计数；输出：
- `render_prisma_markdown`：可直接放进综述「方法」章节的 Markdown 表格；
- `render_prisma_mermaid`：前端渲染用的 Mermaid 流程图。

**纯函数、无 IO**——同一输入永远同一输出，便于 golden 测试与回归。
"""

from __future__ import annotations

from typing import Any

from app.tools.prisma.flow import render_prisma_markdown, render_prisma_mermaid


def _counts(**overrides: Any) -> dict[str, Any]:
    """完整的 PRISMA 计数（与 `ScreeningService.prisma_counts` 同结构）。"""
    base: dict[str, Any] = {
        "identified_by_source": {"arxiv": 40, "openalex": 20},
        "identified_total": 60,
        "duplicates_removed": 10,
        "records_screened": 50,
        "title_abstract": {"screened": 50, "excluded": 30},
        "fulltext": {"assessed": 20, "excluded": 5},
        "included": 15,
        "exclusion_reasons": {
            "not relevant": 25,
            "no fulltext": 5,
            "wrong population": 5,
        },
    }
    base.update(overrides)
    return base


class TestMarkdown:
    def test_contains_all_stage_counts(self) -> None:
        markdown = render_prisma_markdown(_counts())

        # 识别 / 去重 / 筛查 / 排除 / 全文 / 纳入
        assert "60" in markdown
        assert "10" in markdown
        assert "50" in markdown
        assert "30" in markdown
        assert "20" in markdown
        assert "15" in markdown

    def test_lists_sources_with_counts(self) -> None:
        markdown = render_prisma_markdown(_counts())

        assert "arxiv" in markdown
        assert "openalex" in markdown
        assert "40" in markdown

    def test_lists_exclusion_reasons_with_counts(self) -> None:
        markdown = render_prisma_markdown(_counts())

        assert "not relevant" in markdown
        assert "25" in markdown
        assert "wrong population" in markdown

    def test_is_valid_markdown_with_headings(self) -> None:
        markdown = render_prisma_markdown(_counts())

        assert markdown.startswith("# ")
        assert "## 识别" in markdown
        assert "## 筛选" in markdown
        assert "## 纳入" in markdown

    def test_mentions_prisma_2020(self) -> None:
        """报告里要能看出这是 PRISMA 2020 口径（审稿人对照用）。"""
        assert "PRISMA 2020" in render_prisma_markdown(_counts())

    def test_empty_counts_render_without_error(self) -> None:
        """零数据（刚建任务）也要能出图，不能抛异常。"""
        empty: dict[str, Any] = {
            "identified_by_source": {},
            "identified_total": 0,
            "duplicates_removed": 0,
            "records_screened": 0,
            "title_abstract": {"screened": 0, "excluded": 0},
            "fulltext": {"assessed": 0, "excluded": 0},
            "included": 0,
            "exclusion_reasons": {},
        }

        markdown = render_prisma_markdown(empty)

        assert "# " in markdown
        assert "0" in markdown

    def test_tolerates_missing_nested_keys(self) -> None:
        """历史/不完整载荷缺字段时按 0 处理，不抛 KeyError。"""
        markdown = render_prisma_markdown({"identified_total": 5})

        assert "5" in markdown


class TestMermaid:
    def test_starts_with_flowchart_directive(self) -> None:
        mermaid = render_prisma_mermaid(_counts())

        assert mermaid.startswith("flowchart TD")

    def test_nodes_are_balanced(self) -> None:
        """方括号必须成对（否则 Mermaid 渲染报错）。"""
        mermaid = render_prisma_mermaid(_counts())

        assert mermaid.count("[") == mermaid.count("]")

    def test_contains_stage_counts(self) -> None:
        mermaid = render_prisma_mermaid(_counts())

        for value in ("60", "10", "50", "30", "20", "15"):
            assert value in mermaid

    def test_has_decision_branches(self) -> None:
        """PRISMA 流程图要有「排除」分支（否则画出来是直线，不是流程图）。"""
        mermaid = render_prisma_mermaid(_counts())

        assert mermaid.count("-->|") >= 2, "至少要有标题摘要与全文两处排除分支"

    def test_empty_counts_render_without_error(self) -> None:
        mermaid = render_prisma_mermaid({"identified_total": 0})

        assert mermaid.startswith("flowchart TD")
        assert mermaid.count("[") == mermaid.count("]")

    def test_is_deterministic(self) -> None:
        """纯函数：同一输入两次调用输出完全一致（golden 可回归）。"""
        counts = _counts()

        assert render_prisma_mermaid(counts) == render_prisma_mermaid(counts)
        assert render_prisma_markdown(counts) == render_prisma_markdown(counts)

    def test_source_order_is_stable(self) -> None:
        """来源排序稳定（按名字排序），不随 dict 插入顺序漂移。"""
        first = render_prisma_markdown(_counts(identified_by_source={"openalex": 20, "arxiv": 40}))
        second = render_prisma_markdown(_counts(identified_by_source={"arxiv": 40, "openalex": 20}))

        assert first == second, "来源排序不稳定，golden 会漂移"


class TestConsistency:
    def test_markdown_and_mermaid_agree_on_numbers(self) -> None:
        """两种输出必须用同一份数字（不能一个渲染识别 60、另一个 50）。"""
        counts = _counts()
        markdown = render_prisma_markdown(counts)
        mermaid = render_prisma_mermaid(counts)

        for key in ("identified_total", "records_screened", "included"):
            value = str(counts[key])
            assert value in markdown
            assert value in mermaid


class TestLanguage:
    """系统契约 `output_language` 是 `zh | en`，生成的 Markdown 要能直接进论文。"""

    def test_default_is_chinese(self) -> None:
        markdown = render_prisma_markdown(_counts())

        assert "识别" in markdown
        assert "筛选" in markdown
        assert "纳入" in markdown

    def test_english_markdown(self) -> None:
        markdown = render_prisma_markdown(_counts(), language="en")

        assert "Identification" in markdown
        assert "Screening" in markdown
        assert "Included" in markdown
        assert "识别" not in markdown

    def test_english_mermaid(self) -> None:
        mermaid = render_prisma_mermaid(_counts(), language="en")

        assert "Identified" in mermaid
        assert "Screened" in mermaid
        assert mermaid.startswith("flowchart TD")

    def test_unknown_language_falls_back_to_chinese(self) -> None:
        """未知语言回退默认（中文），不抛异常、不产出空文案。"""
        markdown = render_prisma_markdown(_counts(), language="fr")

        assert "识别" in markdown

    def test_numbers_identical_across_languages(self) -> None:
        """语言只影响文案，不影响数字（否则中英版本会互相矛盾）。"""
        counts = _counts()

        for key in ("identified_total", "duplicates_removed", "records_screened", "included"):
            value = str(counts[key])
            assert value in render_prisma_markdown(counts, language="zh")
            assert value in render_prisma_markdown(counts, language="en")

    def test_english_is_valid_mermaid(self) -> None:
        """英文标签也必须通过真实 Mermaid 语法（括号/引号不能破坏结构）。"""
        mermaid = render_prisma_mermaid(_counts(), language="en")

        assert mermaid.count("[") == mermaid.count("]")
        assert mermaid.count('"') % 2 == 0, "引号必须成对"
