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

    def test_supports_narrative_stage_phrasing(self) -> None:
        """真实开题报告常把阶段写成「第一阶段为……」的叙述段落（生产实测）。"""
        markdown = """## 技术路线

技术路线遵循“硬件选型—算法开发—系统集成—性能评估”的逻辑闭环。

第一阶段为硬件平台搭建。确定 STM32 主控芯片型号，设计包含 PPG 传感器与电源管理单元的 PCB 电路。

第二阶段为信号处理算法研发。构建“采样-处理-休眠”周期性工作模式，并部署自适应滤波基线算法。

第三阶段为通信与电源管理集成。实现 BLE 数据传输模块，采用批量发送策略减少射频开启时间。

第四阶段为系统测试与评估。在实验室环境下模拟不同强度的运动场景，对比心率误差与平均电流消耗。

## 研究计划进度安排

周期为 12 个月。
"""
        steps = extract_roadmap_steps(markdown)

        assert [name for name, _detail in steps] == [
            "硬件平台搭建",
            "信号处理算法研发",
            "通信与电源管理集成",
            "系统测试与评估",
        ]

    def test_supports_bold_chinese_stage_headers(self) -> None:
        markdown = """## 技术路线

**第一阶段：需求分析与架构设计（第1-3个月）**

重点完成硬件选型与系统架构定义。

**第二阶段：核心算法开发与仿真（第4-7个月）**

聚焦信号处理与低功耗调度算法。
"""
        steps = extract_roadmap_steps(markdown)

        assert [name for name, _detail in steps] == ["需求分析与架构设计", "核心算法开发与仿真"]

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
