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
import zipfile

from docx import Document
from docx.oxml.ns import qn

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
