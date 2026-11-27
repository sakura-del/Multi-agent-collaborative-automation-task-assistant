"""Word 富内容块解析与国标/高校学术排版（Phase 1）。

支持从 Markdown 正文识别：
- 学术三线表（| col | ...）：顶线 1.5pt、表头线 0.75pt、底线 1.5pt、无竖线、跨页重复表头
- 高清插图（![图题](fig://<asset_id>)）：居中插入 + keep_with_next 防与图题分页
- 块级公式（$$...$$）：右对齐制表位排「公式 …… (chapter.n)」
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT, WD_TAB_LEADER
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Cm, Pt

_FIGURE_RE = re.compile(r"^!\[(?P<caption>[^\]]*)\]\(fig://(?P<asset_id>[A-Za-z0-9_-]+)\)$")
_TABLE_SEP_RE = re.compile(r"^\|?[\s:-]+\|[\s|:-]*$")
_MAX_FIGURE_WIDTH_CM = 14.5  # A4 宽度 21cm - 左右页边距 3.17cm×2 ≈ 14.66cm


@dataclass(frozen=True)
class FigureAsset:
    """可插入 Word 的图片资产（PNG/JPEG 字节 + 可选英文副图题）。"""

    asset_id: str
    image_bytes: bytes
    caption_zh: str = ""
    caption_en: str = ""
    width_cm: float = _MAX_FIGURE_WIDTH_CM


@dataclass(frozen=True)
class LineBlock:
    line: str


@dataclass(frozen=True)
class TableBlock:
    headers: list[str]
    rows: list[list[str]]
    caption: str = ""


@dataclass(frozen=True)
class FigureBlock:
    asset_id: str
    caption: str = ""


@dataclass(frozen=True)
class FormulaBlock:
    expression: str


RichBlock = LineBlock | TableBlock | FigureBlock | FormulaBlock


def parse_rich_blocks(text: str) -> list[RichBlock]:
    """把 Markdown 章节文本解析为有序富内容块，保持普通段落 100% 兼容。"""
    lines = text.replace("\r\n", "\n").splitlines()
    blocks: list[RichBlock] = []
    i = 0
    n = len(lines)
    while i < n:
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            continue

        # 1. 插图块：![图题](fig://asset_id)
        fig_match = _FIGURE_RE.match(stripped)
        if fig_match:
            blocks.append(
                FigureBlock(
                    asset_id=fig_match.group("asset_id"),
                    caption=fig_match.group("caption").strip(),
                )
            )
            i += 1
            continue

        # 2. 块级公式：$$ ... $$
        if stripped.startswith("$$"):
            if stripped.endswith("$$") and len(stripped) > 4:
                blocks.append(FormulaBlock(expression=stripped[2:-2].strip()))
                i += 1
                continue
            formula_lines: list[str] = []
            i += 1
            while i < n and not lines[i].strip().endswith("$$"):
                formula_lines.append(lines[i].strip())
                i += 1
            i += 1
            blocks.append(FormulaBlock(expression=" ".join(formula_lines).strip()))
            continue

        # 3. Markdown 管道表
        if "|" in stripped and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1].strip()):
            caption = ""
            # 若上一块是「表 X.Y ...」提示行，吸收为表题
            if blocks:
                last_block = blocks[-1]
                if isinstance(last_block, LineBlock) and re.match(r"^表\s*\d", last_block.line):
                    caption = last_block.line
                    blocks.pop()
            headers = _split_md_row(stripped)
            i += 2
            rows: list[list[str]] = []
            while i < n and "|" in lines[i].strip() and lines[i].strip():
                rows.append(_split_md_row(lines[i].strip()))
                i += 1
            blocks.append(TableBlock(headers=headers, rows=rows, caption=caption))
            continue

        blocks.append(LineBlock(line=stripped))
        i += 1
    return blocks


def _split_md_row(line: str) -> list[str]:
    trimmed = line.strip()
    if trimmed.startswith("|"):
        trimmed = trimmed[1:]
    if trimmed.endswith("|"):
        trimmed = trimmed[:-1]
    return [cell.strip() for cell in trimmed.split("|")]


def _set_run_fonts(run: object, *, size_pt: float, bold: bool = False) -> None:
    """中仿宋 + 英 Times New Roman（补齐 ascii/hAnsi/eastAsia/cs，规避坑 #1）。"""
    run.bold = bold  # type: ignore[attr-defined]
    run.font.size = Pt(size_pt)  # type: ignore[attr-defined]
    run.font.name = "Times New Roman"  # type: ignore[attr-defined]
    rpr = run._r.get_or_add_rPr()  # type: ignore[attr-defined]
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    rfonts.set(qn("w:ascii"), "Times New Roman")
    rfonts.set(qn("w:hAnsi"), "Times New Roman")
    rfonts.set(qn("w:eastAsia"), "仿宋")
    rfonts.set(qn("w:cs"), "Times New Roman")


def insert_three_line_table(
    document: object,
    anchor_element: object,
    block: TableBlock,
    *,
    chapter_no: int,
    table_no: int,
) -> list[object]:
    """在 anchor 之后插入「表题（居上、防拆页）+ 学术三线表」，返回新插入的 XML 节点。"""
    # 1. 表题（五号居中，段前 6 磅段后 6 磅，表序加粗空一字，keep_with_next=True）
    cap_p = document.add_paragraph()  # type: ignore[attr-defined]
    cap_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cap_p.paragraph_format.space_before = Pt(6)
    cap_p.paragraph_format.space_after = Pt(6)
    cap_p.paragraph_format.keep_with_next = True
    clean_cap = re.sub(r"^表\s*\d+(?:\.\d+)?[.\s]*", "", block.caption).strip()
    r_num = cap_p.add_run(f"表 {chapter_no}.{table_no}　")
    _set_run_fonts(r_num, size_pt=10.5, bold=True)
    r_title = cap_p.add_run(clean_cap or "文献方法与核心结果对比")
    _set_run_fonts(r_title, size_pt=10.5, bold=False)

    # 2. 表格主体
    col_count = max(len(block.headers), max((len(r) for r in block.rows), default=0))
    table = document.add_table(rows=1 + len(block.rows), cols=col_count)  # type: ignore[attr-defined]
    table.alignment = WD_TABLE_ALIGNMENT.CENTER

    # 三线表外框（顶 1.5pt = sz 12，底 1.5pt = sz 12，左右与内部无边框）
    tbl_borders = parse_xml(
        f"<w:tblBorders {nsdecls('w')}>"
        '  <w:top w:val="single" w:sz="12" w:space="0" w:color="000000"/>'
        '  <w:bottom w:val="single" w:sz="12" w:space="0" w:color="000000"/>'
        '  <w:left w:val="none"/><w:right w:val="none"/>'
        '  <w:insideH w:val="none"/><w:insideV w:val="none"/>'
        "</w:tblBorders>"
    )
    table._tbl.tblPr.append(tbl_borders)

    # 填表头 + 栏目线（0.75pt = sz 6）+ 跨页重复表头
    header_row = table.rows[0]
    tr_pr = header_row._tr.get_or_add_trPr()
    tr_pr.append(OxmlElement("w:tblHeader"))
    tr_pr.append(OxmlElement("w:cantSplit"))
    for col_idx in range(col_count):
        cell = header_row.cells[col_idx]
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(3)
        p.paragraph_format.space_after = Pt(3)
        run = p.add_run(block.headers[col_idx] if col_idx < len(block.headers) else "")
        _set_run_fonts(run, size_pt=10.5, bold=True)
        tc_borders = parse_xml(
            f"<w:tcBorders {nsdecls('w')}>"
            '  <w:bottom w:val="single" w:sz="6" w:space="0" w:color="000000"/>'
            "</w:tcBorders>"
        )
        cell._tc.get_or_add_tcPr().append(tc_borders)

    # 填数据行（五号，cantSplit）
    for r_idx, row_values in enumerate(block.rows, start=1):
        row = table.rows[r_idx]
        row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
        for col_idx in range(col_count):
            cell = row.cells[col_idx]
            p = cell.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_before = Pt(2)
            p.paragraph_format.space_after = Pt(2)
            run = p.add_run(row_values[col_idx] if col_idx < len(row_values) else "")
            _set_run_fonts(run, size_pt=10.5, bold=False)

    # 将新建的 cap_p 与 table 按顺序移动到 anchor 之后
    anchor_element.addnext(table._tbl)  # type: ignore[attr-defined]
    anchor_element.addnext(cap_p._p)  # type: ignore[attr-defined]
    return [cap_p._p, table._tbl]


def insert_figure_block(
    document: object,
    anchor_element: object,
    asset: FigureAsset,
    *,
    chapter_no: int,
    figure_no: int,
    caption_override: str = "",
) -> list[object]:
    """插入居中高清插图 + 下方五号图题（图片段 keep_with_next=True 保证同页）。"""
    img_p = document.add_paragraph()  # type: ignore[attr-defined]
    img_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    img_p.paragraph_format.space_before = Pt(6)
    img_p.paragraph_format.space_after = Pt(4)
    img_p.paragraph_format.keep_with_next = True
    img_p.add_run().add_picture(
        io.BytesIO(asset.image_bytes),
        width=Cm(min(asset.width_cm, _MAX_FIGURE_WIDTH_CM)),
    )

    cap_p = document.add_paragraph()  # type: ignore[attr-defined]
    cap_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cap_p.paragraph_format.space_before = Pt(4)
    cap_p.paragraph_format.space_after = Pt(12)
    cap_text = (caption_override or asset.caption_zh).strip() or "文献统计分析图"
    r_num = cap_p.add_run(f"图 {chapter_no}.{figure_no}　")
    _set_run_fonts(r_num, size_pt=10.5, bold=True)
    r_title = cap_p.add_run(cap_text)
    _set_run_fonts(r_title, size_pt=10.5, bold=False)

    anchor_element.addnext(cap_p._p)  # type: ignore[attr-defined]
    anchor_element.addnext(img_p._p)  # type: ignore[attr-defined]
    return [img_p._p, cap_p._p]


def insert_formula_block(
    document: object,
    anchor_element: object,
    block: FormulaBlock,
    *,
    chapter_no: int,
    formula_no: int,
) -> list[object]:
    """插入带右对齐编号的块级公式（Times New Roman 五号，带引导点）。"""
    p = document.add_paragraph()  # type: ignore[attr-defined]
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(6)
    p.paragraph_format.tab_stops.add_tab_stop(
        Cm(_MAX_FIGURE_WIDTH_CM), WD_TAB_ALIGNMENT.RIGHT, WD_TAB_LEADER.DOTS
    )
    run = p.add_run(f"　　{block.expression}\t（{chapter_no}.{formula_no}）")
    _set_run_fonts(run, size_pt=10.5, bold=False)
    anchor_element.addnext(p._p)  # type: ignore[attr-defined]
    return [p._p]
