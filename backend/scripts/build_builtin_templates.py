"""生成并校验内置高校标准「文献综述」.docx 母版（Phase 1）。

运行方式：
    cd backend
    python scripts/build_builtin_templates.py
"""

from __future__ import annotations

import pathlib
import sys
from typing import Any

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Cm, Pt, RGBColor

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

BUILTIN_DIR = BACKEND_DIR / "app" / "tools" / "templates" / "builtin"
OUTPUT_PATH = BUILTIN_DIR / "literature_review_cn_standard.docx"
PROPOSAL_OUTPUT_PATH = BUILTIN_DIR / "thesis_proposal_cn_standard.docx"


def _set_fonts(target: Any, *, east_asia: str, latin: str = "Times New Roman") -> None:
    """补齐 w:rFonts 四属性，避免中文回退宋体（坑 #1）。"""
    rpr = target.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    rfonts.set(qn("w:ascii"), latin)
    rfonts.set(qn("w:hAnsi"), latin)
    rfonts.set(qn("w:eastAsia"), east_asia)
    rfonts.set(qn("w:cs"), latin)


def _configure_a4_margins(section: Any) -> None:
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(2.54)
    section.bottom_margin = Cm(2.54)
    section.left_margin = Cm(3.17)
    section.right_margin = Cm(3.17)


def _add_page_number_field(paragraph: Any) -> None:
    """向页脚段落插入居中阿拉伯数字 PAGE 域（五号 Times New Roman）。"""
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run_begin = paragraph.add_run()
    run_begin.font.size = Pt(10.5)
    run_begin.font.name = "Times New Roman"
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    run_begin._r.append(fld_begin)

    run_instr = paragraph.add_run(" PAGE ")
    run_instr.font.size = Pt(10.5)
    run_instr.font.name = "Times New Roman"

    run_sep = paragraph.add_run()
    fld_sep = OxmlElement("w:fldChar")
    fld_sep.set(qn("w:fldCharType"), "separate")
    run_sep._r.append(fld_sep)

    run_val = paragraph.add_run("1")
    run_val.font.size = Pt(10.5)
    run_val.font.name = "Times New Roman"

    run_end = paragraph.add_run()
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    run_end._r.append(fld_end)


def _ensure_style(document: Any, name: str, *, base_style: str = "Normal") -> Any:
    styles = document.styles
    if name in styles:
        return styles[name]
    style = styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
    if base_style in styles:
        style.base_style = styles[base_style]
    return style


def _configure_styles(document: Any) -> None:
    # 1. 正文基线 Normal / Body Text（中仿宋 + 英 TNR 小四 12pt，固定行距 20pt，首行缩进 2 字符）
    for style_name in ("Normal", "Body Text"):
        style = _ensure_style(document, style_name)
        style.font.size = Pt(12)
        style.font.name = "Times New Roman"
        style.font.color.rgb = RGBColor(0x00, 0x00, 0x00)
        _set_fonts(style, east_asia="仿宋")
        pf = style.paragraph_format
        pf.line_spacing_rule = WD_LINE_SPACING.EXACTLY
        pf.line_spacing = Pt(20)
        pf.space_before = Pt(0)
        pf.space_after = Pt(0)
        if style_name == "Body Text":
            pf.first_line_indent = Pt(24)  # 小四 12pt × 2 字符 = 24pt

    # 2. 标题 1：仿宋四号加粗（14pt，纯黑），段前 24pt 段后 6pt，keep_with_next
    h1 = _ensure_style(document, "Heading 1")
    h1.font.size = Pt(14)
    h1.font.bold = True
    h1.font.name = "Times New Roman"
    h1.font.color.rgb = RGBColor(0x00, 0x00, 0x00)
    _set_fonts(h1, east_asia="仿宋")
    h1.paragraph_format.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    h1.paragraph_format.line_spacing = Pt(20)
    h1.paragraph_format.space_before = Pt(24)
    h1.paragraph_format.space_after = Pt(6)
    h1.paragraph_format.first_line_indent = Pt(0)
    h1.paragraph_format.keep_with_next = True

    # 3. 标题 2 / 3：仿宋小四加粗（12pt，纯黑），段前 12pt 段后 6pt，keep_with_next
    for heading_name in ("Heading 2", "Heading 3"):
        h_style = _ensure_style(document, heading_name)
        h_style.font.size = Pt(12)
        h_style.font.bold = True
        h_style.font.name = "Times New Roman"
        h_style.font.color.rgb = RGBColor(0x00, 0x00, 0x00)
        _set_fonts(h_style, east_asia="仿宋")
        h_style.paragraph_format.line_spacing_rule = WD_LINE_SPACING.EXACTLY
        h_style.paragraph_format.line_spacing = Pt(20)
        h_style.paragraph_format.space_before = Pt(12)
        h_style.paragraph_format.space_after = Pt(6)
        h_style.paragraph_format.first_line_indent = Pt(0)
        h_style.paragraph_format.keep_with_next = True

    # 4. 参考文献：五号（10.5pt，纯黑），固定行距 20pt，悬挂缩进 2 字符（21pt）
    bib = _ensure_style(document, "Bibliography")
    bib.font.size = Pt(10.5)
    bib.font.name = "Times New Roman"
    bib.font.color.rgb = RGBColor(0x00, 0x00, 0x00)
    _set_fonts(bib, east_asia="仿宋")
    bib.paragraph_format.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    bib.paragraph_format.line_spacing = Pt(20)
    bib.paragraph_format.space_before = Pt(0)
    bib.paragraph_format.space_after = Pt(3)
    bib.paragraph_format.left_indent = Pt(21)
    bib.paragraph_format.first_line_indent = Pt(-21)


def _clear_table_borders(table: Any) -> None:
    tbl_borders = parse_xml(
        f"<w:tblBorders {nsdecls('w')}>"
        '  <w:top w:val="none"/><w:bottom w:val="none"/>'
        '  <w:left w:val="none"/><w:right w:val="none"/>'
        '  <w:insideH w:val="none"/><w:insideV w:val="none"/>'
        "</w:tblBorders>"
    )
    table._tbl.tblPr.append(tbl_borders)


def build_literature_review_cn_standard(output_path: pathlib.Path = OUTPUT_PATH) -> pathlib.Path:
    """生成高校标准「文献综述」母版并用 inspect_template 校验。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    document = Document()
    document.settings.odd_and_even_pages_header_footer = True
    _configure_styles(document)

    # ===== 第 1 节：封面（无页眉无页码）=====
    cover_sec = document.sections[0]
    _configure_a4_margins(cover_sec)
    cover_sec.header.is_linked_to_previous = False
    cover_sec.even_page_header.is_linked_to_previous = False
    cover_sec.footer.is_linked_to_previous = False
    cover_sec.even_page_footer.is_linked_to_previous = False

    spacer_top = document.add_paragraph()
    spacer_top.paragraph_format.space_before = Pt(36)
    spacer_top.paragraph_format.space_after = Pt(12)

    title_p = document.add_paragraph()
    title_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    # 一号 26pt 字严禁继承 Normal 的固定 20pt 行距，改为单倍行距且显式分两行居中（P1-1）
    title_p.paragraph_format.line_spacing_rule = WD_LINE_SPACING.SINGLE
    title_p.paragraph_format.first_line_indent = Pt(0)
    title_p.paragraph_format.space_before = Pt(12)
    title_p.paragraph_format.space_after = Pt(48)
    title_run = title_p.add_run("本科生/研究生毕业论文（设计）\n文献综述")
    title_run.bold = True
    title_run.font.size = Pt(26)  # 一号 26pt
    title_run.font.name = "Times New Roman"
    title_run.font.color.rgb = RGBColor(0x00, 0x00, 0x00)
    _set_fonts(title_run, east_asia="黑体")

    fields = [
        ("论文题目", "{{ title }}"),
        ("作　　者", "{{ author }}"),
        ("指导教师", "{{ advisor }}"),
        ("专　　业", "{{ major }}"),
        ("学　　院", "{{ college }}"),
    ]
    cover_table = document.add_table(rows=len(fields), cols=2)
    cover_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    _clear_table_borders(cover_table)

    for idx, (label, placeholder) in enumerate(fields):
        row = cover_table.rows[idx]
        c_label, c_val = row.cells[0], row.cells[1]
        c_label.width = Cm(3.5)
        c_val.width = Cm(10.5)
        c_label.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        c_val.vertical_alignment = WD_ALIGN_VERTICAL.CENTER

        p_l = c_label.paragraphs[0]
        p_l.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        p_l.paragraph_format.space_before = Pt(8)
        p_l.paragraph_format.space_after = Pt(8)
        r_l = p_l.add_run(f"{label}：")
        r_l.bold = True
        r_l.font.size = Pt(16)  # 三号 16pt
        r_l.font.name = "Times New Roman"
        _set_fonts(r_l, east_asia="华文仿宋")

        p_v = c_val.paragraphs[0]
        p_v.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_v.paragraph_format.line_spacing_rule = WD_LINE_SPACING.SINGLE
        p_v.paragraph_format.first_line_indent = Pt(0)
        p_v.paragraph_format.space_before = Pt(8)
        p_v.paragraph_format.space_after = Pt(8)
        r_v = p_v.add_run(placeholder)
        r_v.bold = True
        r_v.font.size = Pt(16)
        r_v.font.name = "Times New Roman"
        r_v.font.color.rgb = RGBColor(0x00, 0x00, 0x00)
        _set_fonts(r_v, east_asia="华文仿宋")
        tc_borders = parse_xml(
            f"<w:tcBorders {nsdecls('w')}>"
            '  <w:bottom w:val="single" w:sz="6" w:space="0" w:color="000000"/>'
            "</w:tcBorders>"
        )
        c_val._tc.get_or_add_tcPr().append(tc_borders)

    # ===== 第 2 节：正文（奇数页起章，奇偶页眉 + 页码）=====
    body_sec = document.add_section(WD_SECTION.ODD_PAGE)
    _configure_a4_margins(body_sec)
    body_sec.header.is_linked_to_previous = False
    body_sec.even_page_header.is_linked_to_previous = False
    body_sec.footer.is_linked_to_previous = False
    body_sec.even_page_footer.is_linked_to_previous = False

    odd_hdr = body_sec.header.paragraphs[0]
    odd_hdr.alignment = WD_ALIGN_PARAGRAPH.CENTER
    odd_hdr_run = odd_hdr.add_run("{{ title }}")
    odd_hdr_run.font.size = Pt(9)  # 小五 9pt
    odd_hdr_run.font.name = "Times New Roman"
    _set_fonts(odd_hdr_run, east_asia="宋体")

    even_hdr = body_sec.even_page_header.paragraphs[0]
    even_hdr.alignment = WD_ALIGN_PARAGRAPH.CENTER
    even_hdr_run = even_hdr.add_run("文献综述")
    even_hdr_run.font.size = Pt(9)
    even_hdr_run.font.name = "Times New Roman"
    _set_fonts(even_hdr_run, east_asia="宋体")

    _add_page_number_field(body_sec.footer.paragraphs[0])
    _add_page_number_field(body_sec.even_page_footer.paragraphs[0])

    chap_p = document.add_paragraph()
    chap_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    chap_p.paragraph_format.line_spacing_rule = WD_LINE_SPACING.SINGLE
    chap_p.paragraph_format.first_line_indent = Pt(0)
    chap_p.paragraph_format.space_before = Pt(0)
    chap_p.paragraph_format.space_after = Pt(12)
    chap_p.paragraph_format.keep_with_next = True
    chap_run = chap_p.add_run("一、文献综述")
    chap_run.bold = True
    chap_run.font.size = Pt(16)  # 三号 16pt
    chap_run.font.name = "Times New Roman"
    chap_run.font.color.rgb = RGBColor(0x00, 0x00, 0x00)
    _set_fonts(chap_run, east_asia="仿宋")

    body_p = document.add_paragraph(style="Body Text")
    body_run = body_p.add_run("{{ body }}")
    body_run.font.size = Pt(12)
    body_run.font.name = "Times New Roman"
    _set_fonts(body_run, east_asia="仿宋")

    # ===== 第 3 节：参考文献（奇数页起章）=====
    ref_sec = document.add_section(WD_SECTION.ODD_PAGE)
    _configure_a4_margins(ref_sec)
    ref_sec.header.is_linked_to_previous = True
    ref_sec.even_page_header.is_linked_to_previous = True
    ref_sec.footer.is_linked_to_previous = True
    ref_sec.even_page_footer.is_linked_to_previous = True

    ref_title_p = document.add_paragraph()
    ref_title_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    ref_title_p.paragraph_format.space_before = Pt(12)
    ref_title_p.paragraph_format.space_after = Pt(12)
    ref_title_p.paragraph_format.keep_with_next = True
    ref_title_run = ref_title_p.add_run("参考文献")
    ref_title_run.bold = True
    ref_title_run.font.size = Pt(16)  # 三号 16pt
    ref_title_run.font.name = "Times New Roman"
    _set_fonts(ref_title_run, east_asia="仿宋")

    ref_p = document.add_paragraph(style="Bibliography")
    ref_run = ref_p.add_run("{{ references }}")
    ref_run.font.size = Pt(10.5)
    ref_run.font.name = "Times New Roman"
    _set_fonts(ref_run, east_asia="仿宋")

    document.save(str(output_path))

    # 生成后立刻用 inspect_template 校验安全与契约
    from app.tools.templates.inspector import inspect_template

    meta = inspect_template(output_path)
    print(
        f"BUILT {output_path.name}: size={output_path.stat().st_size}B "
        f"placeholders={[p.key for p in meta.placeholders]} "
        f"styles={len(meta.style_map)}"
    )
    return output_path


def build_thesis_proposal_cn_standard(
    output_path: pathlib.Path = PROPOSAL_OUTPUT_PATH,
) -> pathlib.Path:
    """生成高校标准「开题报告」母版并用 inspect_template 校验（Phase 2）。

    结构与「文献综述」母版一致（封面 + 奇数页起章正文 + 参考文献），但：
    - 封面横幅为「本科生/研究生毕业论文（设计）开题报告」；
    - 正文偶数页页眉为「开题报告」；
    - 正文由三段式开题报告 Markdown 渲染（技术路线图由 template_service 注入）。
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    document = Document()
    document.settings.odd_and_even_pages_header_footer = True
    _configure_styles(document)

    # ===== 第 1 节：封面（无页眉无页码）=====
    cover_sec = document.sections[0]
    _configure_a4_margins(cover_sec)
    cover_sec.header.is_linked_to_previous = False
    cover_sec.even_page_header.is_linked_to_previous = False
    cover_sec.footer.is_linked_to_previous = False
    cover_sec.even_page_footer.is_linked_to_previous = False

    spacer_top = document.add_paragraph()
    spacer_top.paragraph_format.space_before = Pt(36)
    spacer_top.paragraph_format.space_after = Pt(12)

    title_p = document.add_paragraph()
    title_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title_p.paragraph_format.space_before = Pt(12)
    title_p.paragraph_format.space_after = Pt(48)
    title_run = title_p.add_run("本科生/研究生毕业论文（设计）开题报告")
    title_run.bold = True
    title_run.font.size = Pt(26)  # 一号 26pt
    title_run.font.name = "Times New Roman"
    _set_fonts(title_run, east_asia="黑体")

    fields = [
        ("论文题目", "{{ title }}"),
        ("作　　者", "{{ author }}"),
        ("指导教师", "{{ advisor }}"),
        ("专　　业", "{{ major }}"),
        ("学　　院", "{{ college }}"),
    ]
    cover_table = document.add_table(rows=len(fields), cols=2)
    cover_table.alignment = WD_TABLE_ALIGNMENT.CENTER
    _clear_table_borders(cover_table)

    for idx, (label, placeholder) in enumerate(fields):
        row = cover_table.rows[idx]
        c_label, c_val = row.cells[0], row.cells[1]
        c_label.width = Cm(3.5)
        c_val.width = Cm(10.5)
        c_label.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        c_val.vertical_alignment = WD_ALIGN_VERTICAL.CENTER

        p_l = c_label.paragraphs[0]
        p_l.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        p_l.paragraph_format.space_before = Pt(8)
        p_l.paragraph_format.space_after = Pt(8)
        r_l = p_l.add_run(f"{label}：")
        r_l.bold = True
        r_l.font.size = Pt(16)  # 三号 16pt
        r_l.font.name = "Times New Roman"
        _set_fonts(r_l, east_asia="华文仿宋")

        p_v = c_val.paragraphs[0]
        p_v.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_v.paragraph_format.space_before = Pt(8)
        p_v.paragraph_format.space_after = Pt(8)
        r_prefix = p_v.add_run("　")
        r_prefix.font.size = Pt(16)
        r_v = p_v.add_run(placeholder)
        r_v.bold = True
        r_v.font.size = Pt(16)
        r_v.font.name = "Times New Roman"
        _set_fonts(r_v, east_asia="华文仿宋")
        r_suffix = p_v.add_run("　")
        r_suffix.font.size = Pt(16)
        tc_borders = parse_xml(
            f"<w:tcBorders {nsdecls('w')}>"
            '  <w:bottom w:val="single" w:sz="6" w:space="0" w:color="000000"/>'
            "</w:tcBorders>"
        )
        c_val._tc.get_or_add_tcPr().append(tc_borders)

    # ===== 第 2 节：正文（奇数页起章，奇偶页眉 + 页码）=====
    body_sec = document.add_section(WD_SECTION.ODD_PAGE)
    _configure_a4_margins(body_sec)
    body_sec.header.is_linked_to_previous = False
    body_sec.even_page_header.is_linked_to_previous = False
    body_sec.footer.is_linked_to_previous = False
    body_sec.even_page_footer.is_linked_to_previous = False

    odd_hdr = body_sec.header.paragraphs[0]
    odd_hdr.alignment = WD_ALIGN_PARAGRAPH.CENTER
    odd_hdr_run = odd_hdr.add_run("{{ title }}")
    odd_hdr_run.font.size = Pt(9)  # 小五 9pt
    odd_hdr_run.font.name = "Times New Roman"
    _set_fonts(odd_hdr_run, east_asia="宋体")

    even_hdr = body_sec.even_page_header.paragraphs[0]
    even_hdr.alignment = WD_ALIGN_PARAGRAPH.CENTER
    even_hdr_run = even_hdr.add_run("开题报告")
    even_hdr_run.font.size = Pt(9)
    even_hdr_run.font.name = "Times New Roman"
    _set_fonts(even_hdr_run, east_asia="宋体")

    _add_page_number_field(body_sec.footer.paragraphs[0])
    _add_page_number_field(body_sec.even_page_footer.paragraphs[0])

    body_p = document.add_paragraph(style="Body Text")
    body_run = body_p.add_run("{{ body }}")
    body_run.font.size = Pt(12)
    body_run.font.name = "Times New Roman"
    _set_fonts(body_run, east_asia="仿宋")

    # ===== 第 3 节：参考文献（奇数页起章）=====
    ref_sec = document.add_section(WD_SECTION.ODD_PAGE)
    _configure_a4_margins(ref_sec)
    ref_sec.header.is_linked_to_previous = True
    ref_sec.even_page_header.is_linked_to_previous = True
    ref_sec.footer.is_linked_to_previous = True
    ref_sec.even_page_footer.is_linked_to_previous = True

    ref_title_p = document.add_paragraph()
    ref_title_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    ref_title_p.paragraph_format.space_before = Pt(12)
    ref_title_p.paragraph_format.space_after = Pt(12)
    ref_title_p.paragraph_format.keep_with_next = True
    ref_title_run = ref_title_p.add_run("参考文献")
    ref_title_run.bold = True
    ref_title_run.font.size = Pt(16)  # 三号 16pt
    ref_title_run.font.name = "Times New Roman"
    _set_fonts(ref_title_run, east_asia="仿宋")

    ref_p = document.add_paragraph(style="Bibliography")
    ref_run = ref_p.add_run("{{ references }}")
    ref_run.font.size = Pt(10.5)
    ref_run.font.name = "Times New Roman"
    _set_fonts(ref_run, east_asia="仿宋")

    document.save(str(output_path))

    # 生成后立刻用 inspect_template 校验安全与契约
    from app.tools.templates.inspector import inspect_template

    meta = inspect_template(output_path)
    print(
        f"BUILT {output_path.name}: size={output_path.stat().st_size}B "
        f"placeholders={[p.key for p in meta.placeholders]} "
        f"styles={len(meta.style_map)}"
    )
    return output_path


if __name__ == "__main__":
    build_literature_review_cn_standard()
    build_thesis_proposal_cn_standard()
