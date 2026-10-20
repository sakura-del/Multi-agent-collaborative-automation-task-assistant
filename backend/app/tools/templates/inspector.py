"""Word 模板检查器（Phase 1 · P1-A / 计划 §1.1）。

职责：
1. **占位符抽取**：docxtpl 不解析 `w:sdt`（内容控件），因此约定 `{{ key }}` 命名，
   用 `get_undeclared_template_variables()` 扫描出待填字段清单（路线图 §4.3）；
2. **styleId → 语义角色**：只按 `styles.xml` 的 **styleId** 推断，**绝不按本地化 name**
   （坑 #3：中文 Word 的「标题 1」与 `Heading 1` 的 name/styleId 不一致）；
3. **安全校验**（坑 #7：模板是不可信输入）：只接受 `.docx`，拒绝 `.docm` / 宏
   （`vbaProject.bin`）/ OLE 嵌入 / zip 炸弹 / 路径穿越条目；
4. **外部关系剥离**：`w:attachedTemplate`、`TargetMode="External"` 关系与 `w:altChunk`
   在渲染前被剥离（`sanitize_template_bytes`），而不是直接拒绝——学校模板常带
   `attachedTemplate`，拒绝会让用户完全无法使用；
5. **docxtpl 语法约束**（坑 #5）：`{%p%}` / `{%tr%}` 必须独占段落，否则作废；
   Jinja 语法非法（如 `{{` 未闭合）同样拒绝。
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from docxtpl import DocxTemplate
from lxml import etree

from app.core.logging import get_logger
from app.tools.templates.models import (
    PlaceholderField,
    StyleRole,
    TemplateMeta,
    TemplateSecurityError,
    TemplateValidationError,
)

logger = get_logger(__name__)

#: 只接受 .docx（.docm 启用宏，明确拒绝）
DOCX_SUFFIX = ".docx"
#: zip 炸弹防线：条目数与解压后总字节上限（声明大小即可判定，无需真正解压）
MAX_ENTRY_COUNT = 2000
MAX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024

_DOCUMENT_PART = "word/document.xml"
_STYLES_PART = "word/styles.xml"
_SETTINGS_PART = "word/settings.xml"
_CONTENT_TYPES_PART = "[Content_Types].xml"

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

#: docxtpl 段落级标签：必须独占段落（坑 #5）
_PARAGRAPH_LEVEL_TAG = re.compile(r"\s*\{%-?\s*(?:p|tr)\b.*?%\}\s*", re.DOTALL)
#: 关系属性（r:id / r:embed / r:link 等），用于定位引用被剥离关系的元素
_RELATIONSHIP_ATTR_PREFIX = f"{{{_R_NS}}}"

#: Word 内置段落样式 **styleId** → 语义角色（坑 #3：仅按 styleId）
_STYLE_ID_ROLES: dict[str, StyleRole] = {
    "title": StyleRole.TITLE,
    "标题": StyleRole.TITLE,
    "heading1": StyleRole.H1,
    "标题1": StyleRole.H1,
    "1": StyleRole.H1,
    "heading2": StyleRole.H2,
    "标题2": StyleRole.H2,
    "2": StyleRole.H2,
    "heading3": StyleRole.H3,
    "标题3": StyleRole.H3,
    "3": StyleRole.H3,
    "bodytext": StyleRole.BODY,
    "body": StyleRole.BODY,
    "normal": StyleRole.BODY,
    "正文": StyleRole.BODY,
    "caption": StyleRole.CAPTION,
    "题注": StyleRole.CAPTION,
    "bibliography": StyleRole.REFERENCE,
    "reference": StyleRole.REFERENCE,
    "references": StyleRole.REFERENCE,
    "参考文献": StyleRole.REFERENCE,
}


# ===== 公开接口 =====


def inspect_template(path: Path) -> TemplateMeta:
    """扫描 `.docx` 模板，返回占位符清单、styleId→角色映射与指纹。

    安全前提：调用方必须把本函数当作**不可信输入的入口**——先校验后解析，
    解析失败一律抛 `TemplateValidationError`（不会把底层异常泄漏给调用方）。
    """
    template_path = Path(path)
    if not template_path.is_file():
        raise TemplateValidationError(f"模板文件不存在：{template_path.name}")
    data = template_path.read_bytes()
    _validate_suffix(template_path)
    validate_template_bytes(data, source=template_path.name)

    checksum = hashlib.sha256(data).hexdigest()
    keys = extract_placeholder_keys(data)
    placeholders = [PlaceholderField(key=key) for key in keys]
    name = (template_path.stem or "template")[:256]
    meta = TemplateMeta(
        template_id=f"tpl-{checksum[:16]}",
        name=name,
        placeholders=placeholders,
        style_map=scan_style_map(data),
        checksum=checksum,
    )
    logger.info(
        "template_inspected",
        template_id=meta.template_id,
        placeholders=len(placeholders),
        styles=len(meta.style_map),
    )
    return meta


def validate_template_bytes(data: bytes, *, source: str = "模板") -> None:
    """校验模板包结构、安全约束与 docxtpl 语法；不通过则抛领域异常。"""
    _validate_package(data, source=source)
    _validate_paragraph_level_tags(data, source=source)
    extract_placeholder_keys(data)


def extract_placeholder_keys(data: bytes) -> list[str]:
    """用 docxtpl 抽取未声明模板变量（含页眉页脚），返回按 key 排序的列表。"""
    try:
        template = DocxTemplate(io.BytesIO(data))
        variables = template.get_undeclared_template_variables()
    except TemplateValidationError:
        raise
    except Exception as exc:  # docxtpl / jinja2 / python-docx 的底层异常统一收敛
        raise TemplateValidationError(f"模板 Jinja 语法不合法：{exc}") from exc
    return sorted(str(name) for name in variables)


def scan_style_map(data: bytes) -> dict[str, StyleRole]:
    """从 `styles.xml` 抽段落样式的 styleId → 语义角色（跳过无法识别与字符样式）。"""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        styles_xml = archive.read(_STYLES_PART)
    root = etree.fromstring(styles_xml)
    style_map: dict[str, StyleRole] = {}
    for style in root.iter(f"{{{_W_NS}}}style"):
        style_type = style.get(f"{{{_W_NS}}}type", "paragraph")
        if style_type != "paragraph":
            continue
        style_id = style.get(f"{{{_W_NS}}}styleId")
        if not style_id:
            continue
        role = infer_style_role(style_id)
        if role is not None:
            style_map[style_id] = role
    return style_map


def infer_style_role(style_id: str) -> StyleRole | None:
    """按 styleId 推断语义角色；未知样式返回 None（**不看 name**，坑 #3）。"""
    normalized = re.sub(r"[\s_\-]+", "", (style_id or "").strip().lower())
    return _STYLE_ID_ROLES.get(normalized)


def sanitize_template_bytes(data: bytes) -> bytes:
    """剥离模板中的外部关系与内容包含风险（坑 #7），返回可安全渲染的 docx 字节。

    处理项：
    - `w:attachedTemplate`（外部模板链接）；
    - `TargetMode="External"` 的关系（外部超链接/图片/OLE 目标）及其引用元素；
    - `w:altChunk`（可嵌入任意外部 HTML/docx 的包含机制）及其关系。

    产物是可正常打开的 docx（内部关系、样式、numbering 原样保留）。
    """
    entries = _read_entries(data)
    dropped_ids: dict[str, set[str]] = {}

    # 1) 收集每个部件的外部关系 id
    external_ids: dict[str, set[str]] = {}
    for name, blob in entries.items():
        if not name.endswith(".rels"):
            continue
        ids = _external_relationship_ids(blob)
        if ids:
            external_ids[_part_for_rels(name)] = ids
    for part, ids in external_ids.items():
        dropped_ids.setdefault(part, set()).update(ids)

    # 2) 清理部件 XML：attachedTemplate / altChunk / 引用外部关系的元素
    for name in list(entries):
        if not _is_word_xml_part(name):
            continue
        cleaned, extra_ids = _strip_unsafe_elements(entries[name], external_ids.get(name, set()))
        if extra_ids:
            dropped_ids.setdefault(name, set()).update(extra_ids)
        if cleaned is not None:
            entries[name] = cleaned

    # 3) 重写关系部件：删除外部关系与被剥离元素引用的关系
    for name in list(entries):
        if not name.endswith(".rels"):
            continue
        removable = dropped_ids.get(_part_for_rels(name), set())
        cleaned = _strip_relationships(entries[name], removable)
        if cleaned is not None:
            entries[name] = cleaned

    return _write_entries(entries)


# ===== 内部：后缀与包结构校验 =====


def _validate_suffix(path: Path) -> None:
    suffix = path.suffix.lower()
    if suffix == DOCX_SUFFIX:
        return
    if suffix == ".docm":
        raise TemplateSecurityError("仅支持 .docx 模板；.docm（启用宏）模板已拒绝")
    raise TemplateValidationError(f"只接受 .docx 模板文件，收到：{suffix or '无后缀'}")


def _validate_package(data: bytes, *, source: str) -> None:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise TemplateValidationError(f"{source} 不是合法的 docx（zip）包") from exc

    names: set[str] = set()
    total_bytes = 0
    for info in archive.infolist():
        name = info.filename
        if name.startswith("/") or ".." in name.replace("\\", "/").split("/"):
            raise TemplateSecurityError(f"模板包含非法路径条目：{name}")
        total_bytes += info.file_size
        if total_bytes > MAX_UNCOMPRESSED_BYTES:
            raise TemplateSecurityError("模板解压后大小超限（疑似 zip 炸弹），已拒绝")
        names.add(name)
        lowered = name.lower()
        if lowered.endswith("vbaproject.bin"):
            raise TemplateSecurityError("模板包含宏（vbaProject.bin），已拒绝")
        if lowered.startswith("word/embeddings/"):
            raise TemplateSecurityError("模板包含 OLE 嵌入对象，已拒绝")
    if len(names) > MAX_ENTRY_COUNT:
        raise TemplateSecurityError(f"模板条目数 {len(names)} 超过上限 {MAX_ENTRY_COUNT}，已拒绝")
    if _DOCUMENT_PART not in names or _STYLES_PART not in names:
        raise TemplateValidationError(
            "模板缺少 word/document.xml 或 word/styles.xml，不是有效 docx"
        )

    if _CONTENT_TYPES_PART in names:
        content_types = archive.read(_CONTENT_TYPES_PART).decode("utf-8", errors="ignore")
        if "macroEnabled" in content_types or "vbaProject" in content_types:
            raise TemplateSecurityError("模板声明了宏内容（macroEnabled），已拒绝")


def _validate_paragraph_level_tags(data: bytes, *, source: str) -> None:
    """`{%p%}` / `{%tr%}` 必须独占段落（坑 #5：否则 docxtpl 产出错乱文档）。"""
    for part, blob in _iter_word_xml_parts(data):
        for text in _iter_paragraph_texts(blob):
            if "{%p" not in text and "{%tr" not in text:
                continue
            if _PARAGRAPH_LEVEL_TAG.fullmatch(text):
                continue
            raise TemplateValidationError(
                f"{source} 的 {part} 中 docxtpl 段落级标签（{{%p%}}/{{%tr%}}）必须独占段落，"
                f"当前段落含其他内容：{text.strip()[:80]}"
            )


# ===== 内部：XML 遍历与清理 =====


def _iter_word_xml_parts(data: bytes) -> Iterator[tuple[str, bytes]]:
    """遍历正文与页眉页脚 XML（正文 + word/header*.xml + word/footer*.xml）。"""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for name in archive.namelist():
            lowered = name.lower()
            if name == _DOCUMENT_PART or (
                lowered.startswith("word/")
                and (lowered.startswith("word/header") or lowered.startswith("word/footer"))
                and lowered.endswith(".xml")
            ):
                yield name, archive.read(name)


def _iter_paragraph_texts(blob: bytes) -> Iterator[str]:
    try:
        root = etree.fromstring(blob)
    except etree.XMLSyntaxError:
        return
    for paragraph in root.iter(f"{{{_W_NS}}}p"):
        texts = [node.text or "" for node in paragraph.iter(f"{{{_W_NS}}}t")]
        yield "".join(texts)


def _is_word_xml_part(name: str) -> bool:
    lowered = name.lower()
    return lowered.startswith("word/") and lowered.endswith(".xml")


def _read_entries(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _write_entries(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, blob in entries.items():
            archive.writestr(name, blob)
    return buffer.getvalue()


def _part_for_rels(rels_name: str) -> str:
    """`word/_rels/document.xml.rels` → `word/document.xml`。"""
    normalized = rels_name.replace("\\", "/")
    directory, _, filename = normalized.rpartition("/")
    if directory.endswith("_rels"):
        directory = directory[: -len("_rels")].rstrip("/")
    part = filename[: -len(".rels")] if filename.endswith(".rels") else filename
    return f"{directory}/{part}" if directory else part


def _serialize(root: Any) -> bytes:
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _external_relationship_ids(blob: bytes) -> set[str]:
    try:
        root = etree.fromstring(blob)
    except etree.XMLSyntaxError as exc:
        raise TemplateValidationError("模板关系文件（.rels）不是合法 XML") from exc
    return {
        relationship.get("Id", "")
        for relationship in root
        if relationship.get("TargetMode", "").strip().lower() == "external"
    } - {""}


def _strip_unsafe_elements(blob: bytes, external_ids: set[str]) -> tuple[bytes | None, set[str]]:
    """删除 `w:attachedTemplate` / `w:altChunk` 与引用外部关系的元素。

    返回（新 XML 或 None 表示未改动，被删除的 altChunk 关系 id）。
    """
    try:
        root = etree.fromstring(blob)
    except etree.XMLSyntaxError as exc:
        raise TemplateValidationError("模板部件不是合法 XML") from exc

    changed = False
    alt_chunk_ids: set[str] = set()
    for element in list(root.iter()):
        if element.tag == f"{{{_W_NS}}}attachedTemplate":
            _drop(element)
            changed = True
            continue
        if element.tag == f"{{{_W_NS}}}altChunk":
            rel_id = element.get(f"{{{_R_NS}}}id")
            if rel_id:
                alt_chunk_ids.add(rel_id)
            _drop(element)
            changed = True
            continue
        if external_ids and any(
            attribute.startswith(_RELATIONSHIP_ATTR_PREFIX) and value in external_ids
            for attribute, value in element.attrib.items()
        ):
            _drop(element)
            changed = True
    if not changed:
        return None, alt_chunk_ids
    return _serialize(root), alt_chunk_ids


def _strip_relationships(blob: bytes, removable: set[str]) -> bytes | None:
    try:
        root = etree.fromstring(blob)
    except etree.XMLSyntaxError as exc:
        raise TemplateValidationError("模板关系文件（.rels）不是合法 XML") from exc

    changed = False
    for relationship in list(root):
        is_external = relationship.get("TargetMode", "").strip().lower() == "external"
        if is_external or relationship.get("Id") in removable:
            root.remove(relationship)
            changed = True
    if not changed:
        return None
    return _serialize(root)


def _drop(element: Any) -> None:
    parent = element.getparent()
    if parent is not None:
        parent.remove(element)


__all__ = [
    "DOCX_SUFFIX",
    "MAX_ENTRY_COUNT",
    "MAX_UNCOMPRESSED_BYTES",
    "extract_placeholder_keys",
    "infer_style_role",
    "inspect_template",
    "sanitize_template_bytes",
    "scan_style_map",
    "validate_template_bytes",
]
