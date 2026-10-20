"""PRISMA 渲染 golden 测试（Phase 2 · PRISMA Task 4）。

## 为什么需要 golden

PRISMA 流程图与计数表是**要写进论文的交付物**。内容悄悄变化（多一个空格、
数字口径变了、文案改了）在功能测试里测不出来——测试只断言「含 60」，
但真实渲染结果可能已经不可用。

golden 文件锁定完整输出，任何改动都会失败并需要显式更新，
逼开发者确认「这次改动是否符合预期」。

## 更新方式

**只有确认改动是预期的**才更新：
1. 跑测试看 diff（pytest 会显示不一致）；
2. 重新生成：见本文件 `_regenerate()`；
3. 人工 review golden 文件 diff 后再提交。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.tools.prisma.flow import render_prisma_markdown, render_prisma_mermaid

GOLDEN_DIR = Path(__file__).resolve().parent / "fixtures" / "prisma"

#: 与容器内实测一致的典型数据（137 命中 → 2 条样本 → 1 排除 → 1 纳入）
SAMPLE_COUNTS: dict = {
    "identified_by_source": {"arxiv": 100, "openalex": 37},
    "identified_total": 137,
    "duplicates_removed": 135,
    "records_screened": 2,
    "title_abstract": {"screened": 2, "excluded": 1},
    "fulltext": {"assessed": 1, "excluded": 0},
    "included": 1,
    "exclusion_reasons": {"not relevant": 1},
}


def _regenerate() -> None:
    """重新生成 golden（仅在确认改动符合预期后手动调用）。"""
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    (GOLDEN_DIR / "flow.zh.mmd").write_text(render_prisma_mermaid(SAMPLE_COUNTS), encoding="utf-8")
    (GOLDEN_DIR / "flow.en.mmd").write_text(
        render_prisma_mermaid(SAMPLE_COUNTS, language="en"), encoding="utf-8"
    )
    (GOLDEN_DIR / "report.zh.md").write_text(
        render_prisma_markdown(SAMPLE_COUNTS), encoding="utf-8"
    )
    (GOLDEN_DIR / "report.en.md").write_text(
        render_prisma_markdown(SAMPLE_COUNTS, language="en"), encoding="utf-8"
    )


class TestGoldenFiles:
    @pytest.mark.parametrize(
        ("filename", "render"),
        [
            ("flow.zh.mmd", lambda: render_prisma_mermaid(SAMPLE_COUNTS)),
            ("flow.en.mmd", lambda: render_prisma_mermaid(SAMPLE_COUNTS, language="en")),
            ("report.zh.md", lambda: render_prisma_markdown(SAMPLE_COUNTS)),
            ("report.en.md", lambda: render_prisma_markdown(SAMPLE_COUNTS, language="en")),
        ],
    )
    def test_output_matches_golden(self, filename: str, render) -> None:
        golden_path = GOLDEN_DIR / filename
        assert golden_path.is_file(), f"缺少 golden 文件：{golden_path}（跑 _regenerate() 生成）"

        expected = golden_path.read_text(encoding="utf-8")
        actual = render()

        assert actual == expected, (
            f"{filename} 与 golden 不一致。\n"
            "若改动是预期的，跑 _regenerate() 并 review diff 后提交；\n"
            "否则这是回归，需修正实现。\n"
            f"--- 期望（golden）---\n{expected}\n"
            f"--- 实际 ---\n{actual}"
        )

    def test_golden_numbers_are_self_consistent(self) -> None:
        """golden 数据本身要自洽（识别 = 去重后 + 去除）。"""
        assert (
            SAMPLE_COUNTS["identified_total"]
            == SAMPLE_COUNTS["records_screened"] + SAMPLE_COUNTS["duplicates_removed"]
        )

    def test_golden_flow_closes(self) -> None:
        """golden 的筛选阶段要闭合：筛查 = 排除 + 进入下一阶段。"""
        title = SAMPLE_COUNTS["title_abstract"]
        assert title["screened"] == title["excluded"] + SAMPLE_COUNTS["fulltext"]["assessed"]
