"""Word 富内容排版器、真实年份绘图与内置高校标准母版单测（Phase 1）。

覆盖：
1. Markdown 管道表 → 标准三线表 XML（顶/底 1.5pt、表头 0.75pt、无竖线、tblHeader/cantSplit、表题 keep_with_next）；
2. `FigureBlock` → PNG 嵌入、图片段 keep_with_next、下方图题 `图 1.1`；
3. `FormulaBlock` → 右对齐制表位与 `（1.1）` 公式编号；
4. `build_year_distribution_figure` 有效年份 `<3` 返回 `None`、`>=3` 产出合法 PNG；
5. 内置母版 `literature_review_cn_standard.docx` 通过 `inspect_template()` 校验且渲染产物无残留哨兵。
"""

from __future__ import annotations

import io
import pathlib
import re
import zipfile

from docx import Document
from docx.enum.text import WD_LINE_SPACING
from docx.oxml.ns import qn
from docx.shared import RGBColor

from app.tools.templates.charts import build_year_distribution_figure
from app.tools.templates.inspector import inspect_template
from app.tools.templates.models import StyleRole
from app.tools.templates.renderer import render_document
from app.tools.templates.rich_blocks import (
    FigureAsset,
    FigureBlock,
    FormulaBlock,
    LineBlock,
    TableBlock,
    parse_rich_blocks,
)

BUILTIN_DOCX = (
    pathlib.Path(__file__).resolve().parents[1]
    / "app"
    / "tools"
    / "templates"
    / "builtin"
    / "literature_review_cn_standard.docx"
)

#: 最小 1×1 PNG（有效 PNG 魔数 + IHDR/IDAT/IEND）
_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?"
    b"\x00\x05\xfe\x02\xfe\xa75\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82"
)


class TestParseRichBlocks:
    def test_parses_table_figure_formula_and_text(self) -> None:
        md = """## 研究现状

表 1.1 代表性方法对比
| 方法 | 年份 | 指标 |
| :--- | :---: | :---: |
| RAG | 2024 | 92.1% |

![年份分布图](fig://year_dist)

$$
Loss = \\alpha L_1 + \\beta L_2
$$

总结段落。
"""
        blocks = parse_rich_blocks(md)
        assert isinstance(blocks[0], LineBlock)
        assert blocks[0].line == "## 研究现状"
        assert isinstance(blocks[1], TableBlock)
        assert blocks[1].caption == "表 1.1 代表性方法对比"
        assert blocks[1].headers == ["方法", "年份", "指标"]
        assert blocks[1].rows == [["RAG", "2024", "92.1%"]]
        assert isinstance(blocks[2], FigureBlock)
        assert blocks[2].asset_id == "year_dist"
        assert blocks[2].caption == "年份分布图"
        assert isinstance(blocks[3], FormulaBlock)
        assert "Loss" in blocks[3].expression
        assert isinstance(blocks[4], LineBlock)
        assert blocks[4].line == "总结段落。"


class TestCharts:
    def test_returns_none_when_fewer_than_three_valid_years(self) -> None:
        assert build_year_distribution_figure([{"year": 2023}, {"year": "2024"}]) is None
        assert build_year_distribution_figure([{"year": "invalid"}]) is None

    def test_builds_png_figure_when_three_or_more_years(self) -> None:
        fig = build_year_distribution_figure(
            [
                {"year": 2022},
                {"year": 2023},
                {"issued_year": "2024-05-01"},
                {"year": 2024},
            ]
        )
        assert fig is not None
        assert fig.asset_id == "year_distribution"
        assert fig.image_bytes.startswith(b"\x89PNG\r\n\x1a\n")


class TestBuiltinTemplateAndRichRendering:
    def test_builtin_template_passes_inspector_and_renders_rich_content(self) -> None:
        assert BUILTIN_DOCX.is_file(), f"缺少内置母版：{BUILTIN_DOCX}"
        meta = inspect_template(BUILTIN_DOCX)
        keys = {p.key for p in meta.placeholders}
        assert {"title", "author", "advisor", "major", "college", "body", "references"} <= keys

        body_md = """# 1 引言

国内外研究持续演进 [1]。

表 1.1 主流框架对比
| 框架 | 年份 | 准确率 |
| --- | --- | --- |
| Naive RAG | 2023 | 78% |
| GraphRAG | 2024 | 89% |

![参考文献年份统计](fig://year_distribution)

$$
Precision = TP / (TP + FP)
$$
"""
        out_bytes = render_document(
            template=BUILTIN_DOCX,
            sections={
                "title": "基于多智能体的学术文献综述质量评估",
                "author": "张三",
                "advisor": "李四",
                "major": "计算机科学",
                "college": "信息学院",
                "body": body_md,
            },
            bibliography=["[1] Lewis P. Retrieval-Augmented Generation[J]. NeurIPS, 2020."],
            style_map=meta.style_map,
            figures={
                "year_distribution": FigureAsset(
                    asset_id="year_distribution",
                    image_bytes=_TINY_PNG,
                    caption_zh="文献年份分布",
                )
            },
            chapter_no=1,
        )

        # 确认不残留内部块级哨兵（含正文及全部页眉/页脚），且奇数页眉已填入真实题目
        with zipfile.ZipFile(io.BytesIO(out_bytes)) as zf:
            doc_xml = zf.read("word/document.xml").decode("utf-8")
            assert "__MAATPLBLOCK_" not in doc_xml
            header_texts: list[str] = []
            for name in zf.namelist():
                if name.startswith(("word/header", "word/footer")) and name.endswith(".xml"):
                    part_xml = zf.read(name).decode("utf-8")
                    assert "__MAATPLBLOCK_" not in part_xml, f"Sentinel leaked into {name}"
                    if name.startswith("word/header"):
                        header_texts.append(part_xml)
            assert any("基于多智能体的学术文献综述质量评估" in txt for txt in header_texts)
            assert any(name.startswith("word/media/") for name in zf.namelist())

        doc = Document(io.BytesIO(out_bytes))
        # 正文与参考文献应存在表格：表0=封面无框信息表，表1=三线表
        assert len(doc.tables) >= 2
        three_line = doc.tables[1]
        tbl_borders = three_line._tbl.tblPr.find(qn("w:tblBorders"))
        assert tbl_borders is not None
        assert tbl_borders.find(qn("w:top")).get(qn("w:sz")) == "12"
        assert tbl_borders.find(qn("w:bottom")).get(qn("w:sz")) == "12"
        assert tbl_borders.find(qn("w:insideH")).get(qn("w:val")) == "none"
        assert tbl_borders.find(qn("w:insideV")).get(qn("w:val")) == "none"
        tr_pr = three_line.rows[0]._tr.trPr
        assert tr_pr.find(qn("w:tblHeader")) is not None
        assert tr_pr.find(qn("w:cantSplit")) is not None

        # 表题 & 图题 & 公式编号核查
        paragraphs = list(doc.paragraphs)
        table_cap = next(p for p in paragraphs if "表 1.1" in p.text)
        assert table_cap.paragraph_format.keep_with_next is True

        fig_cap_idx = next(i for i, p in enumerate(paragraphs) if "图 1.1" in p.text)
        assert fig_cap_idx > 0
        img_para = paragraphs[fig_cap_idx - 1]
        assert img_para.paragraph_format.keep_with_next is True

        formula_p = next(p for p in paragraphs if "Precision = TP / (TP + FP)" in p.text)
        assert "（1.1）" in formula_p.text
        assert len(formula_p.paragraph_format.tab_stops) >= 1

        # 验证 render_document 原有 style_map 参数类型无破坏
        assert any(role is StyleRole.BODY for role in meta.style_map.values())

        # P0-2：图片所在段落必须使用单倍行距（不得继承正文固定 20pt 行距导致裁切）
        w_ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        drawing_paras = [p for p in doc.paragraphs if p._p.findall(f".//{w_ns}drawing")]
        assert len(drawing_paras) >= 1
        for dp in drawing_paras:
            assert dp.paragraph_format.line_spacing_rule != WD_LINE_SPACING.EXACTLY

        # P1-1 / P1-2：内置母版封面主标题单倍行距分两行；Heading1/2/3 纯黑；含「一、文献综述」
        assert doc.paragraphs[1].paragraph_format.line_spacing_rule == WD_LINE_SPACING.SINGLE
        assert doc.paragraphs[1].text == "毕业论文（设计）"
        assert "/" not in doc.paragraphs[1].text
        assert doc.paragraphs[2].paragraph_format.line_spacing_rule == WD_LINE_SPACING.SINGLE
        assert doc.paragraphs[2].text == "文献综述"
        assert any("一、文献综述" in p.text for p in doc.paragraphs)
        for style_name in ("Heading 1", "Heading 2", "Heading 3"):
            assert doc.styles[style_name].font.color.rgb == RGBColor(0x00, 0x00, 0x00)

    def test_superscript_citations_and_bullet_cleaning(self) -> None:
        """P0-3 行内数字引文上标 & P1-3 无序列表转为规范项目符号段；参考文献号不上标。"""
        meta = inspect_template(BUILTIN_DOCX)
        body_md = """## 1. 研究进展

已有工作提出了轻量化模型 [1] 与多模态融合方案 [1, 5-6]。

- 语料覆盖时间跨度有限。
* 缺少真机功耗基准对比。
"""
        out_bytes = render_document(
            template=BUILTIN_DOCX,
            sections={
                "title": "轻量级边缘模型综述",
                "author": "王五",
                "advisor": "赵六",
                "major": "软件工程",
                "college": "软件学院",
                "body": body_md,
            },
            bibliography=["[1] Lewis P. Retrieval-Augmented Generation. 2020."],
            style_map=meta.style_map,
        )
        doc = Document(io.BytesIO(out_bytes))
        body_para = next(p for p in doc.paragraphs if "已有工作提出了" in p.text)
        sup_runs = [r.text for r in body_para.runs if r.font.superscript]
        assert sup_runs == ["[1]", "[1, 5-6]"]

        # 参考文献行首 [1] 严禁变成上标
        bib_para = next(p for p in doc.paragraphs if "Retrieval-Augmented Generation" in p.text)
        assert not any(r.font.superscript for r in bib_para.runs)

        # 无序列表前缀 `- ` / `* ` 不得原样印出
        joined = "\n".join(p.text for p in doc.paragraphs)
        assert "• 语料覆盖时间跨度有限。" in joined
        assert "• 缺少真机功耗基准对比。" in joined
        assert "- 语料覆盖时间跨度有限" not in joined
        assert "* 缺少真机功耗基准对比" not in joined

        # 出口全 XML 零内部哨兵 / [E#] / HTML / 模型推断
        with zipfile.ZipFile(io.BytesIO(out_bytes)) as zf:
            for name in zf.namelist():
                if name.endswith(".xml") and name.startswith("word/"):
                    xml_txt = zf.read(name).decode("utf-8", "ignore")
                    assert "__MAATPLBLOCK_" not in xml_txt
                    assert not re.search(r"\[E\d+\]", xml_txt)


class TestScheduleAggregation:
    """Fix 5：研究计划进度安排章节的叙述式阶段段落 → 四列三线表。"""

    _SCHEDULE_MARKDOWN = """## 3 研究计划进度安排

本课题周期为 12 个月，分四个阶段推进。

第一阶段：需求分析与架构设计（第 1-3 个月）

调研运动手环心率监测现状，明确低功耗与精度指标，完成系统总体架构。此阶段需输出需求规格说明书与总体架构图。

第二阶段：硬件平台搭建（第 4-6 个月）

完成 STM32 主控与 PPG 传感器选型，绘制原理图并制作样机。交付硬件原理图与低功耗预算表。

第三阶段：算法实现与联调（第 7-9 个月）

实现自适应滤波与异常检测算法，完成端到端联调。形成可运行算法原型与联调报告。

第四阶段：系统测试与论文撰写（第 10-12 个月）

开展实验室与真实场景测试，整理数据并撰写论文。提交测试报告与学位论文初稿。
"""

    def test_narrative_stages_become_four_column_three_line_table(self) -> None:
        blocks = parse_rich_blocks(self._SCHEDULE_MARKDOWN)

        tables = [block for block in blocks if isinstance(block, TableBlock)]
        assert len(tables) == 1
        table = tables[0]
        assert table.headers == ["起止时间", "研究阶段", "主要工作内容", "阶段性成果"]
        assert len(table.rows) == 4
        assert table.rows[0][0] == "第 1-3 个月"
        assert table.rows[0][1] == "需求分析与架构设计"
        assert "硬件原理图" in table.rows[1][3]
        assert table.caption == "表 2.1 研究计划进度安排表"

    def test_markdown_table_takes_precedence_over_aggregation(self) -> None:
        markdown = """## 3 研究计划进度安排

| 起止时间 | 研究阶段 | 主要工作内容 | 阶段性成果 |
| --- | --- | --- | --- |
| 第 1-3 个月 | 需求分析 | 调研现状 | 需求规格 |

第一阶段：另一段叙述（第 4-6 个月）

说明文字。

第二阶段：第三段叙述（第 7-9 个月）

说明文字。
"""
        blocks = parse_rich_blocks(markdown)

        tables = [block for block in blocks if isinstance(block, TableBlock)]
        assert len(tables) == 1
        assert tables[0].rows[0][1] == "需求分析"

    def test_technical_route_paragraphs_are_not_aggregated(self) -> None:
        markdown = """## 2.2 技术路线

第一阶段为硬件平台搭建。确定主控芯片型号并设计 PCB 电路。

第二阶段为算法研发。部署自适应滤波基线算法。

第三阶段为系统集成。实现 BLE 数据传输模块。
"""
        blocks = parse_rich_blocks(markdown)

        assert not any(isinstance(block, TableBlock) for block in blocks)
        assert any(isinstance(block, LineBlock) and "第一阶段为" in block.line for block in blocks)
