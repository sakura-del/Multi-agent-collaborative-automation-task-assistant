"""开题报告技术路线图（Phase 2）：阶段抽取与流程图生成。"""

from __future__ import annotations

from app.tools.templates.charts import build_tech_roadmap_figure, extract_roadmap_steps

_ROADMAP_MARKDOWN = """## 2.2 技术路线

本研究按以下四个阶段推进：

1. 需求分析与文献调研：梳理运动手环心率监测的研究现状与关键指标
2. 系统总体设计：确定低功耗架构、传感器选型与数据流
3. 算法实现与联调：实现心率滤波与异常检测算法
4. 实验验证与总结：对比基线方案并撰写论文

## 2.3 可行性分析

技术、经济与进度均可行。
"""


class TestExtractRoadmapSteps:
    def test_extracts_numbered_steps_from_roadmap_section(self) -> None:
        steps = extract_roadmap_steps(_ROADMAP_MARKDOWN)

        assert len(steps) == 4
        assert steps[0][0] == "需求分析与文献调研"
        assert "运动手环" in steps[0][1]
        assert steps[3][0] == "实验验证与总结"

    def test_supports_chinese_number_and_paren_styles(self) -> None:
        markdown = """## 技术路线
（1）文献调研：梳理现状
（2）方案设计：确定架构
（3）系统实现：完成编码
（4）实验评估：对比基线
"""
        steps = extract_roadmap_steps(markdown)

        assert [name for name, _detail in steps] == [
            "文献调研",
            "方案设计",
            "系统实现",
            "实验评估",
        ]

    def test_ignores_other_sections(self) -> None:
        markdown = """## 1. 问题提出的背景
1. 这是背景条目，不应被当作技术路线阶段
2. 第二条背景条目

## 2.2 技术路线
1. 需求分析：明确目标
2. 系统设计：确定架构
3. 实验验证：评估效果
"""
        steps = extract_roadmap_steps(markdown)

        assert len(steps) == 3
        assert steps[0][0] == "需求分析"

    def test_returns_empty_when_less_than_two_steps(self) -> None:
        markdown = """## 2.2 技术路线
1. 只有一步，不足以画流程图
"""
        assert extract_roadmap_steps(markdown) == []

    def test_returns_empty_without_roadmap_section(self) -> None:
        assert extract_roadmap_steps("# 文献综述\n\n1. 正文条目") == []


class TestBuildTechRoadmapFigure:
    def test_builds_300dpi_png_for_four_steps(self) -> None:
        steps = extract_roadmap_steps(_ROADMAP_MARKDOWN)

        figure = build_tech_roadmap_figure(steps)

        assert figure is not None
        assert figure.asset_id == "tech_roadmap"
        assert figure.image_bytes[:4] == b"\x89PNG"
        assert len(figure.image_bytes) > 10_000
        assert figure.caption_zh == "本文研究技术路线图"
        assert figure.width_cm == 13.5

    def test_returns_none_when_steps_too_few(self) -> None:
        assert build_tech_roadmap_figure([("阶段一", "唯一阶段")]) is None
