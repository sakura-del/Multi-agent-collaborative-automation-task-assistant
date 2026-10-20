"""Word 模板渲染器测试（Phase 1 · P1-A / 计划 §1.1）。

覆盖：
1. 章节 + 调用方传入的**已 CSL 渲染参考文献行**写入模板，并按 `style_map` 落样式
   （Title / H1 / H2 / BodyText / Bibliography，全部按 **styleId**，坑 #3）；
2. XML 转义与「内容不被当模板执行」（坑 #5：autoescape / 注入面）；
3. 域（TOC）与 numbering 原样保留（坑 #2/#4）；
4. `w:rFonts` 四属性补全（坑 #1）；
5. 参考文献占位符缺失时追加到正文末尾；空章节删除占位段落；
6. 契约错误（未知键 / 缺内容 / 宏包）与渲染确定性；
7. 渲染前剥离外部关系（坑 #7 的落地点）。
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import pytest
from docx import Document
from lxml import etree

from app.tools.templates import (
    StyleRole,
    TemplateRenderError,
    TemplateSecurityError,
    inspect_template,
    render_document,
)

FIXTURES = Path(__file__).parent / "fixtures" / "templates"
TEMPLATE = FIXTURES / "school_template.docx"

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

SECTIONS = {
    "title": "面向多智能体的引用可信度研究",
    "abstract": "本文研究引用核验的确定性方法。",
    "chapter_1": "引言",
    "chapter_1_1": "研究背景",
    "body_main": "正文第一段。\n正文第二段。",
    "author": "张伟",
}
BIBLIOGRAPHY = [
    "[1] 张三. 文献综述方法. 北京: 科学出版社, 2024.",
    "[2] 李四. 引用核验研究. 2025.",
]


# ===== 工具 =====


def _meta():
    return inspect_template(TEMPLATE)


def _render(
    *,
    sections: dict[str, str] | None = None,
    bibliography: list[str] | None = None,
    style_map: dict[str, StyleRole] | None = None,
    template: Path = TEMPLATE,
) -> bytes:
    meta = _meta()
    return render_document(
        template=template,
        sections=SECTIONS if sections is None else sections,
        bibliography=BIBLIOGRAPHY if bibliography is None else bibliography,
        style_map=meta.style_map if style_map is None else style_map,
    )


def _paragraphs(payload: bytes) -> list[tuple[str, str]]:
    document = Document(io.BytesIO(payload))
    return [(paragraph.style.style_id, paragraph.text) for paragraph in document.paragraphs]


def _style_of(paragraphs: list[tuple[str, str]], text: str) -> str:
    matches = [style_id for style_id, content in paragraphs if content == text]
    assert matches, f"输出中找不到段落文本: {text!r}；实际: {paragraphs}"
    return matches[0]


def _xml(payload: bytes, part: str) -> str:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        return archive.read(part).decode("utf-8")


def _read_entries(path: Path = TEMPLATE) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _write_package(target: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, blob in entries.items():
            archive.writestr(name, blob)
    return target


def _variant(tmp_path: Path, *, old: str | None = None, new: str = "") -> Path:
    entries = _read_entries()
    if old is not None:
        xml = entries["word/document.xml"].decode("utf-8")
        assert old in xml, f"fixture 中不存在待替换文本: {old!r}"
        entries["word/document.xml"] = xml.replace(old, new).encode("utf-8")
    return _write_package(tmp_path / "variant.docx", entries)


def _add_attached_template(entries: dict[str, bytes]) -> None:
    root = etree.fromstring(entries["word/settings.xml"])
    attached = etree.SubElement(root, f"{{{_W_NS}}}attachedTemplate")
    attached.set(f"{{{_R_NS}}}id", "rIdAttached")
    entries["word/settings.xml"] = etree.tostring(root, xml_declaration=True, encoding="UTF-8")
    entries["word/_rels/settings.xml.rels"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Relationships xmlns="{_PKG_REL_NS}">'
        '<Relationship Id="rIdAttached"'
        ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/attachedTemplate"'
        ' Target="file:///C:/evil/school.dotx" TargetMode="External"/>'
        "</Relationships>"
    ).encode()


# ===== 1. 正常渲染 =====


class TestRenderDocument:
    def test_fills_sections_and_bibliography_with_mapped_styles(self) -> None:
        payload = _render()
        paragraphs = _paragraphs(payload)

        # 文本与顺序：标题 → 摘要 → 章节（Heading 按 styleId 应用）
        assert _style_of(paragraphs, SECTIONS["title"]) == "Title"
        assert _style_of(paragraphs, SECTIONS["abstract"]) == "BodyText"
        assert _style_of(paragraphs, SECTIONS["chapter_1"]) == "Heading1"
        assert _style_of(paragraphs, SECTIONS["chapter_1_1"]) == "Heading2"
        # 多行正文 → 多个 BodyText 段落
        assert _style_of(paragraphs, "正文第一段。") == "BodyText"
        assert _style_of(paragraphs, "正文第二段。") == "BodyText"
        # 参考文献由调用方传入的「已 CSL 渲染行」逐行落样式
        for line in BIBLIOGRAPHY:
            assert _style_of(paragraphs, line) == "Bibliography"
        assert [text for style_id, text in paragraphs if style_id == "Bibliography"] == BIBLIOGRAPHY
        # 行内占位符：只在原位置替换，前后文本保留
        assert ("Normal", "撰写人：张伟") in paragraphs
        assert all("{{" not in text for _style_id, text in paragraphs)

    def test_escapes_xml_special_chars(self) -> None:
        dangerous = '<script>alert("x")</script> & 中文 < 符号 >'
        payload = _render(sections={**SECTIONS, "body_main": dangerous})

        paragraphs = _paragraphs(payload)
        assert _style_of(paragraphs, dangerous) == "BodyText"
        raw = _xml(payload, "word/document.xml")
        assert "<script>" not in raw
        assert "&lt;script&gt;" in raw
        assert "&amp;" in raw

    def test_content_is_not_evaluated_as_template(self) -> None:
        """正文内容是不可信输入：不得被二次当作模板执行（注入面）。"""
        risky = "{{ title }} {% if true %}恶意{% endif %}"
        payload = _render(sections={**SECTIONS, "body_main": risky})

        paragraphs = _paragraphs(payload)
        assert _style_of(paragraphs, risky) == "BodyText"
        assert _style_of(paragraphs, SECTIONS["title"]) == "Title"

    def test_markdown_heading_prefixes_apply_styles(self) -> None:
        payload = _render(
            sections={
                **SECTIONS,
                "body_main": "## 方法\n正文内容\n### 数据来源\n更多内容",
            }
        )
        paragraphs = _paragraphs(payload)

        assert _style_of(paragraphs, "方法") == "Heading1"
        assert _style_of(paragraphs, "正文内容") == "BodyText"
        assert _style_of(paragraphs, "数据来源") == "Heading2"
        assert _style_of(paragraphs, "更多内容") == "BodyText"

    def test_empty_section_removes_placeholder_paragraph(self) -> None:
        payload = _render(sections={**SECTIONS, "chapter_1_1": "  "})

        paragraphs = _paragraphs(payload)
        assert not [text for style_id, text in paragraphs if style_id == "Heading2"]

    def test_style_map_empty_falls_back_to_placeholder_style(self) -> None:
        payload = _render(style_map={})
        paragraphs = _paragraphs(payload)

        assert _style_of(paragraphs, SECTIONS["title"]) == "Title"
        assert _style_of(paragraphs, SECTIONS["body_main"].splitlines()[0]) == "BodyText"

    def test_render_is_deterministic(self) -> None:
        first = _paragraphs(_render())
        second = _paragraphs(_render())

        assert first == second
        # 内容层面摘要一致（zip 时间戳不同，不比较字节）
        assert (
            hashlib.sha256(repr(first).encode()).hexdigest()
            == hashlib.sha256(repr(second).encode()).hexdigest()
        )


# ===== 2. 模板既有特性保留（坑 #1/#2/#4）=====


class TestTemplatePreservation:
    def test_preserves_toc_field_and_numbering(self) -> None:
        payload = _render()
        document_xml = _xml(payload, "word/document.xml")
        styles_xml = _xml(payload, "word/styles.xml")

        # 坑 #2：TOC 域原样保留（刷新由 Word/LibreOffice 负责，渲染器不破坏域）
        assert "TOC" in document_xml
        assert document_xml.count("w:fldChar") == _xml(
            TEMPLATE.read_bytes(), "word/document.xml"
        ).count("w:fldChar")
        # 坑 #4：numbering 与列表段落原样保留
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            assert "word/numbering.xml" in archive.namelist()
        assert "ListNumber" in styles_xml
        assert "w:numPr" in styles_xml
        assert ("ListNumber", "编号条目一") in _paragraphs(payload)

    def test_completes_partial_rfonts(self) -> None:
        """坑 #1：只设 ascii/hAnsi 的中文字体会回退宋体，需补全 eastAsia/cs。"""
        payload = _render()
        root = etree.fromstring(_xml(payload, "word/document.xml").encode("utf-8"))
        fonts = root.findall(f".//{{{_W_NS}}}rFonts")
        targeted = [node for node in fonts if node.get(f"{{{_W_NS}}}ascii") == "Times New Roman"]

        assert targeted, "正文占位符的 rFonts 未随段落生成"
        for node in targeted:
            assert node.get(f"{{{_W_NS}}}hAnsi") == "Times New Roman"
            assert node.get(f"{{{_W_NS}}}eastAsia") == "Times New Roman"
            assert node.get(f"{{{_W_NS}}}cs") == "Times New Roman"


# ===== 3. 参考文献/占位符边界 =====


class TestBibliographyAndContracts:
    def test_bibliography_appended_when_placeholder_missing(self, tmp_path: Path) -> None:
        template = _variant(tmp_path, old="{{ references }}", new="参考文献")
        payload = render_document(
            template=template,
            sections=SECTIONS,
            bibliography=BIBLIOGRAPHY,
            style_map=_meta().style_map,
        )

        paragraphs = _paragraphs(payload)
        # 模板自带的静态「参考文献」标题段落保留原样式（占位符段落本来就是 Bibliography）
        assert ("Bibliography", "参考文献") in paragraphs
        for line in BIBLIOGRAPHY:
            assert _style_of(paragraphs, line) == "Bibliography"
        # 追加在正文末尾，顺序与入参一致
        tail = [text for _style_id, text in paragraphs[-2:]]
        assert tail == BIBLIOGRAPHY

    def test_unknown_section_key_rejected(self) -> None:
        with pytest.raises(TemplateRenderError, match="nope"):
            _render(sections={**SECTIONS, "nope": "多余内容"})

    def test_missing_section_content_rejected(self) -> None:
        sections = {key: value for key, value in SECTIONS.items() if key != "abstract"}
        with pytest.raises(TemplateRenderError, match="abstract"):
            _render(sections=sections)

    def test_macro_template_rejected(self, tmp_path: Path) -> None:
        entries = _read_entries()
        entries["word/vbaProject.bin"] = b"\xd0\xcf\x11\xe0macro"
        template = _write_package(tmp_path / "macro.docx", entries)

        with pytest.raises(TemplateSecurityError):
            render_document(
                template=template,
                sections=SECTIONS,
                bibliography=BIBLIOGRAPHY,
                style_map=_meta().style_map,
            )


# ===== 4. 外部关系剥离（坑 #7 的渲染侧落点）=====


class TestRenderSanitizesExternalRelations:
    def test_render_strips_attached_template(self, tmp_path: Path) -> None:
        entries = _read_entries()
        _add_attached_template(entries)
        template = _write_package(tmp_path / "attached.docx", entries)

        payload = render_document(
            template=template,
            sections=SECTIONS,
            bibliography=BIBLIOGRAPHY,
            style_map=_meta().style_map,
        )

        settings_xml = _xml(payload, "word/settings.xml")
        assert "attachedTemplate" not in settings_xml
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = set(archive.namelist())
            if "word/_rels/settings.xml.rels" in names:
                rels = archive.read("word/_rels/settings.xml.rels").decode("utf-8")
                assert 'TargetMode="External"' not in rels
        Document(io.BytesIO(payload))
        assert _style_of(_paragraphs(payload), SECTIONS["title"]) == "Title"
