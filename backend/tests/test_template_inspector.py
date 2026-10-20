"""Word 模板检查器测试（Phase 1 · P1-A / 计划 §1.1）。

覆盖：
1. 占位符抽取（docxtpl `get_undeclared_template_variables`）与模板指纹（template_id / checksum）；
2. `style_map` 只按 **styleId** 映射（路线图坑 #3：本地化 name 不可信）；
3. 安全校验（坑 #7）：拒绝 `.docm`、宏 `vbaProject.bin`、OLE 嵌入、zip 炸弹、非法包；
4. 外部关系剥离：`w:attachedTemplate` 与 `TargetMode="External"` 关系（坑 #7）；
5. docxtpl 段落级标签 `{%p%}` / `{%tr%}` 必须独占段落（坑 #5）。
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
    TemplateMeta,
    TemplateSecurityError,
    TemplateValidationError,
    inspect_template,
    sanitize_template_bytes,
)

FIXTURES = Path(__file__).parent / "fixtures" / "templates"
TEMPLATE = FIXTURES / "school_template.docx"

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_RELATED_TEMPLATE_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/attachedTemplate"
)
_HYPERLINK_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"


# ===== 包构造工具 =====


def _read_entries(path: Path = TEMPLATE) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _write_package(target: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, blob in entries.items():
            archive.writestr(name, blob)
    return target


def _patch_document_xml(entries: dict[str, bytes], old: str, new: str) -> None:
    xml = entries["word/document.xml"].decode("utf-8")
    assert old in xml, f"fixture 中不存在待替换文本: {old!r}"
    entries["word/document.xml"] = xml.replace(old, new).encode("utf-8")


def _add_attached_template(entries: dict[str, bytes]) -> None:
    """注入 `w:attachedTemplate`（外部模板引用）与其外部关系。"""
    root = etree.fromstring(entries["word/settings.xml"])
    attached = etree.SubElement(root, f"{{{_W_NS}}}attachedTemplate")
    attached.set(f"{{{_R_NS}}}id", "rIdAttached")
    entries["word/settings.xml"] = etree.tostring(root, xml_declaration=True, encoding="UTF-8")
    entries["word/_rels/settings.xml.rels"] = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<Relationships xmlns="{_PKG_REL_NS}">'
        f'<Relationship Id="rIdAttached" Type="{_RELATED_TEMPLATE_TYPE}"'
        ' Target="file:///C:/evil/school.dotx" TargetMode="External"/>'
        "</Relationships>"
    ).encode()


def _add_external_hyperlink(entries: dict[str, bytes]) -> None:
    """注入外部超链接关系与引用元素。"""
    rels = etree.fromstring(entries["word/_rels/document.xml.rels"])
    relation = etree.SubElement(rels, f"{{{_PKG_REL_NS}}}Relationship")
    relation.set("Id", "rIdEvilLink")
    relation.set("Type", _HYPERLINK_TYPE)
    relation.set("Target", "http://evil.example.com/steal")
    relation.set("TargetMode", "External")
    entries["word/_rels/document.xml.rels"] = etree.tostring(
        rels, xml_declaration=True, encoding="UTF-8"
    )
    root = etree.fromstring(entries["word/document.xml"])
    body = root.find(f"{{{_W_NS}}}body")
    hyperlink = etree.Element(f"{{{_W_NS}}}hyperlink")
    hyperlink.set(f"{{{_R_NS}}}id", "rIdEvilLink")
    run = etree.SubElement(hyperlink, f"{{{_W_NS}}}r")
    text = etree.SubElement(run, f"{{{_W_NS}}}t")
    text.text = "外部链接"
    section = body.find(f"{{{_W_NS}}}sectPr")
    if section is not None:
        section.addprevious(hyperlink)
    else:
        body.append(hyperlink)
    entries["word/document.xml"] = etree.tostring(root, xml_declaration=True, encoding="UTF-8")


# ===== 1. 基本信息 =====


class TestInspectTemplate:
    def test_extracts_placeholders_and_fingerprint(self) -> None:
        meta = inspect_template(TEMPLATE)

        assert isinstance(meta, TemplateMeta)
        assert meta.name == "school_template"
        assert {field.key for field in meta.placeholders} == {
            "abstract",
            "author",
            "body_main",
            "chapter_1",
            "chapter_1_1",
            "references",
            "title",
        }
        assert all(field.required for field in meta.placeholders)
        # 顺序稳定（按 key 排序），便于 UI 渲染与快照对比
        assert [field.key for field in meta.placeholders] == sorted(
            field.key for field in meta.placeholders
        )
        digest = hashlib.sha256(TEMPLATE.read_bytes()).hexdigest()
        assert meta.checksum == digest
        assert meta.template_id.startswith("tpl-")
        assert digest.startswith(meta.template_id.removeprefix("tpl-"))

    def test_style_map_uses_style_id_not_localized_name(self) -> None:
        """坑 #3：中文 Word 把 Heading 1 的 name 本地化为「标题 1」，必须仍按 styleId 映射。"""
        meta = inspect_template(TEMPLATE)

        # 五个核心角色必须存在，且键全部是 styleId
        assert meta.style_map["Title"] is StyleRole.TITLE
        assert meta.style_map["Heading1"] is StyleRole.H1
        assert meta.style_map["Heading2"] is StyleRole.H2
        assert meta.style_map["BodyText"] is StyleRole.BODY
        assert meta.style_map["Bibliography"] is StyleRole.REFERENCE
        # 本地化 name 不得成为映射键
        assert "标题 1" not in meta.style_map
        assert "标题 2" not in meta.style_map
        assert all("标题" not in style_id for style_id in meta.style_map)
        # 模板自带的其它可识别样式（Normal/Caption 等）也在映射里，不影响核心角色
        assert meta.style_map["Normal"] is StyleRole.BODY

    def test_accepts_path_and_missing_file_raises(self, tmp_path: Path) -> None:
        assert inspect_template(str(TEMPLATE)).checksum == inspect_template(TEMPLATE).checksum
        with pytest.raises(TemplateValidationError):
            inspect_template(tmp_path / "not-there.docx")


# ===== 2. 安全校验（坑 #7）=====


class TestTemplateSecurity:
    def test_rejects_docm_extension(self, tmp_path: Path) -> None:
        target = tmp_path / "macro.docm"
        target.write_bytes(TEMPLATE.read_bytes())

        with pytest.raises(TemplateSecurityError, match=r"docm|宏"):
            inspect_template(target)

    def test_rejects_non_docx_extension(self, tmp_path: Path) -> None:
        target = tmp_path / "template.txt"
        target.write_bytes(TEMPLATE.read_bytes())

        with pytest.raises(TemplateValidationError, match="docx"):
            inspect_template(target)

    def test_rejects_macro_part_even_in_docx_container(self, tmp_path: Path) -> None:
        entries = _read_entries()
        entries["word/vbaProject.bin"] = b"\xd0\xcf\x11\xe0macro"
        target = _write_package(tmp_path / "macro.docx", entries)

        with pytest.raises(TemplateSecurityError, match=r"宏|vba"):
            inspect_template(target)

    def test_rejects_ole_embedding(self, tmp_path: Path) -> None:
        entries = _read_entries()
        entries["word/embeddings/oleObject1.bin"] = b"\xd0\xcf\x11\xe0ole"
        target = _write_package(tmp_path / "ole.docx", entries)

        with pytest.raises(TemplateSecurityError, match=r"OLE|嵌入"):
            inspect_template(target)

    def test_rejects_zip_bomb_by_declared_size(self, tmp_path: Path) -> None:
        entries = _read_entries()
        target = tmp_path / "bomb.docx"
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, blob in entries.items():
                archive.writestr(name, blob)
            with archive.open(zipfile.ZipInfo("word/big.bin"), "w") as handle:
                chunk = b"\0" * (1024 * 1024)
                for _ in range(51):
                    handle.write(chunk)

        with pytest.raises(TemplateSecurityError, match=r"zip 炸弹|大小"):
            inspect_template(target)

    def test_rejects_zip_slip_entry_names(self, tmp_path: Path) -> None:
        entries = _read_entries()
        entries["../escape.txt"] = b"x"
        target = _write_package(tmp_path / "slip.docx", entries)

        with pytest.raises(TemplateSecurityError, match=r"路径|非法"):
            inspect_template(target)

    def test_rejects_broken_package(self, tmp_path: Path) -> None:
        target = tmp_path / "broken.docx"
        target.write_bytes(b"definitely-not-a-zip")

        with pytest.raises(TemplateValidationError, match=r"docx|zip"):
            inspect_template(target)

    def test_rejects_missing_document_part(self, tmp_path: Path) -> None:
        entries = _read_entries()
        entries.pop("word/document.xml")
        target = _write_package(tmp_path / "missing.docx", entries)

        with pytest.raises(TemplateValidationError):
            inspect_template(target)


# ===== 3. Jinja / docxtpl 语法校验（坑 #5）=====


class TestTemplateSyntax:
    def test_rejects_unclosed_jinja_expression(self, tmp_path: Path) -> None:
        entries = _read_entries()
        _patch_document_xml(entries, "{{ author }}", "{{ author")
        target = _write_package(tmp_path / "bad-jinja.docx", entries)

        with pytest.raises(TemplateValidationError, match=r"Jinja|语法"):
            inspect_template(target)

    def test_rejects_paragraph_tag_not_alone_in_paragraph(self, tmp_path: Path) -> None:
        """`{%p%}` 必须独占段落，否则 docxtpl 会产出错乱文档（坑 #5）。"""
        entries = _read_entries()
        _patch_document_xml(entries, "撰写人：{{ author }}", "撰写人：{%p if x %}")
        target = _write_package(tmp_path / "bad-tag.docx", entries)

        with pytest.raises(TemplateValidationError, match=r"独占段落|{%p"):
            inspect_template(target)


# ===== 4. 外部关系剥离（坑 #7）=====


class TestSanitizeTemplate:
    def test_strips_attached_template_and_external_relations(self, tmp_path: Path) -> None:
        entries = _read_entries()
        _add_attached_template(entries)
        _add_external_hyperlink(entries)
        target = _write_package(tmp_path / "external.docx", entries)

        # 剥离而不是拒绝：学校模板常带 attachedTemplate，直接拒绝会让用户无法使用
        meta = inspect_template(target)
        assert meta.checksum

        sanitized = sanitize_template_bytes(target.read_bytes())

        with zipfile.ZipFile(io.BytesIO(sanitized)) as archive:
            names = set(archive.namelist())
            settings = archive.read("word/settings.xml").decode("utf-8")
            settings_rels = (
                archive.read("word/_rels/settings.xml.rels").decode("utf-8")
                if "word/_rels/settings.xml.rels" in names
                else ""
            )
            document_rels = archive.read("word/_rels/document.xml.rels").decode("utf-8")
            document_xml = archive.read("word/document.xml").decode("utf-8")

        assert "attachedTemplate" not in settings
        assert 'TargetMode="External"' not in settings_rels
        assert 'TargetMode="External"' not in document_rels
        assert "rIdEvilLink" not in document_rels
        assert "rIdEvilLink" not in document_xml
        # 内部关系与正文必须保留，产物仍是可打开的 docx
        assert "rId" in document_rels
        assert "{{ title }}" in document_xml
        Document(io.BytesIO(sanitized))

    def test_sanitize_is_idempotent(self) -> None:
        """幂等性按**语义**断言，不按压缩字节。

        ⚠️ 不能写 `assert once == twice`：`sanitize_template_bytes` 会重新打包 zip，
        而 zip 条目头含**时间戳**、且不同 zlib 版本/平台的压缩输出可能不同 →
        逐字节比较在 CI 上失败而本地通过（实测：GitHub runner 上 `assert once == twice`
        报 `b'PK\\x03\\x04...' != b'PK\\x03\\x04...'`）。
        正确口径：**条目名集合与每个条目的解压后内容**都相同。
        """
        sample = TEMPLATE.read_bytes()
        once = sanitize_template_bytes(sample)
        twice = sanitize_template_bytes(once)

        def entries(data: bytes) -> dict[str, bytes]:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                return {name: archive.read(name) for name in archive.namelist()}

        assert entries(once) == entries(twice), "二次清洗改变了包内容（不幂等）"

    def test_sanitize_keeps_internal_hyperlinks(self, tmp_path: Path) -> None:
        """内部关系（styles / numbering / image）不得被误删。"""
        sanitized = sanitize_template_bytes(TEMPLATE.read_bytes())

        with zipfile.ZipFile(io.BytesIO(sanitized)) as archive:
            rels = archive.read("word/_rels/document.xml.rels").decode("utf-8")
            names = set(archive.namelist())

        assert "styles.xml" in rels
        assert "numbering.xml" in rels
        assert "word/styles.xml" in names
        assert "word/numbering.xml" in names
        Document(io.BytesIO(sanitized))
