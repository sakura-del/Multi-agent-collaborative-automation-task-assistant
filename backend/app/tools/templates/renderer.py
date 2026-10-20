"""Word 模板渲染器（Phase 1 · P1-A / 计划 §1.1）。

入参语义（冻结接口）：
- `template`：`.docx` 模板路径（不可信输入 → 先跑安全检查与外部关系剥离）；
- `sections`：`占位符 key → 章节文本`。多行文本按段落落版；行首 `#`/`##`/`###`/`####`
  分别映射 TITLE/H1/H2/H3，其余行沿用占位符所在段落的语义角色（BODY）；
- `bibliography`：调用方传入的**已 CSL 渲染好的参考文献行**——本模块不碰引用引擎；
- `style_map`：`Word styleId → 语义角色`（来自 `inspect_template`），渲染时反查
  `角色 → styleId` 并按 **styleId** 落样式（坑 #3）。

实现要点（对应路线图 §4.4）：
- 坑 #1：生成的 run / 段落标记若带 `w:rFonts`，补齐 `ascii/hAnsi/eastAsia/cs`；
- 坑 #2/#4：TOC 域与 `numbering.xml` 原样保留，渲染器不重建域与编号；
- 坑 #5：docxtpl `render(..., autoescape=True)`；内容在渲染**之后**注入，
  因此用户内容永远不会被二次当作模板执行（防注入）；
- 坑 #7：渲染前剥离外部关系（`w:attachedTemplate` / External 关系 / `w:altChunk`）。
"""

from __future__ import annotations

import copy
import hashlib
import io
import re
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docxtpl import DocxTemplate

from app.core.logging import get_logger
from app.tools.templates.inspector import (
    DOCX_SUFFIX,
    extract_placeholder_keys,
    sanitize_template_bytes,
    validate_template_bytes,
)
from app.tools.templates.models import (
    StyleRole,
    TemplateRenderError,
    TemplateSecurityError,
)

logger = get_logger(__name__)

#: 参考文献占位符的候选 key（模板作者只需命中任意一个）
BIBLIOGRAPHY_KEYS: tuple[str, ...] = (
    "references",
    "reference",
    "bibliography",
    "refs",
    "参考文献",
)

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
#: 独占段落的占位符（块级注入；其余按行内替换）
_SOLE_PLACEHOLDER_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
#: 行首 Markdown 标题前缀 → 语义角色
_HEADING_ROLES: dict[int, StyleRole] = {
    1: StyleRole.TITLE,
    2: StyleRole.H1,
    3: StyleRole.H2,
    4: StyleRole.H3,
}
_FONT_ATTRIBUTES = ("ascii", "hAnsi", "eastAsia", "cs")
#: 块级哨兵前缀（渲染后仍出现 = 内容丢失，必须报错）
_SENTINEL_MARKER = "__MAATPLBLOCK_"

#: 反查角色 → styleId 时的优先候选（多个 styleId 映射同一角色时按此挑选）
_PREFERRED_STYLE_IDS: dict[StyleRole, tuple[str, ...]] = {
    StyleRole.TITLE: ("Title",),
    StyleRole.H1: ("Heading1", "Heading 1"),
    StyleRole.H2: ("Heading2", "Heading 2"),
    StyleRole.H3: ("Heading3", "Heading 3"),
    StyleRole.BODY: ("BodyText", "Normal"),
    StyleRole.CAPTION: ("Caption",),
    StyleRole.REFERENCE: ("Bibliography", "References", "Reference"),
}


def render_document(
    *,
    template: Path,
    sections: dict[str, str],
    bibliography: Sequence[str],
    style_map: dict[str, StyleRole],
) -> bytes:
    """把章节正文与已渲染的参考文献行写入模板，返回 docx 字节。"""
    template_path = Path(template)
    if template_path.suffix.lower() != DOCX_SUFFIX:
        raise TemplateSecurityError("只接受 .docx 模板文件")
    if not template_path.is_file():
        raise TemplateRenderError(f"模板文件不存在：{template_path.name}")

    data = template_path.read_bytes()
    validate_template_bytes(data, source=template_path.name)
    sanitized = sanitize_template_bytes(data)
    template_keys = extract_placeholder_keys(sanitized)
    bibliography_key = next((key for key in BIBLIOGRAPHY_KEYS if key in template_keys), None)

    _validate_contract(
        template_keys=template_keys,
        sections=sections,
        bibliography_key=bibliography_key,
    )

    block_keys = _block_placeholder_keys(sanitized, template_keys)
    sentinels = {
        key: _sentinel(key) for key in template_keys if key in block_keys or key == bibliography_key
    }
    context: dict[str, Any] = {}
    for key in template_keys:
        if key in sentinels:
            context[key] = sentinels[key]
        else:
            context[key] = sections.get(key, "")

    rendered = _render_with_docxtpl(sanitized, context)
    document = _load_rendered(rendered)
    role_to_style = _invert_style_map(style_map)

    bibliography_lines = [
        line.strip() for line in bibliography if isinstance(line, str) and line.strip()
    ]
    bibliography_handled = False
    for paragraph in list(_iter_paragraphs(document)):
        text = paragraph.text
        for key, sentinel in sentinels.items():
            if sentinel not in text:
                continue
            if key == bibliography_key:
                bibliography_handled = True
            if text.strip() == sentinel:
                if key == bibliography_key:
                    lines: list[str] = bibliography_lines
                    default_role = StyleRole.REFERENCE
                else:
                    lines = _section_lines(sections.get(key, ""))
                    default_role = StyleRole.BODY
                _replace_block(
                    paragraph,
                    lines,
                    default_role=default_role,
                    style_map=style_map,
                    role_to_style=role_to_style,
                )
                break
            replacement = (
                "\n".join(bibliography_lines) if key == bibliography_key else sections.get(key, "")
            )
            _replace_inline(paragraph, sentinel, replacement)

    if bibliography_lines and not bibliography_handled:
        _append_bibliography(document, bibliography_lines, role_to_style)
    _assert_no_sentinel_left(document)

    buffer = io.BytesIO()
    document.save(buffer)
    logger.info(
        "template_rendered",
        template=template_path.name,
        sections=len(sections),
        references=len(bibliography_lines),
    )
    return buffer.getvalue()


# ===== 契约校验 =====


def _assert_no_sentinel_left(document: Any) -> None:
    """兜底：块级哨兵未命中说明内容会被静默丢弃，必须报错而不是产出残缺文档。"""
    for paragraph in _iter_paragraphs(document):
        if _SENTINEL_MARKER in paragraph.text:
            raise TemplateRenderError(
                "模板中存在无法定位的块级占位符（可能被拆到多个 run），已拒绝静默丢内容"
            )


def _validate_contract(
    *,
    template_keys: Iterable[str],
    sections: dict[str, str],
    bibliography_key: str | None,
) -> None:
    keys = set(template_keys)
    unknown = sorted(set(sections) - keys)
    if unknown:
        raise TemplateRenderError(f"模板中不存在这些占位符：{unknown}")
    if bibliography_key is not None and bibliography_key in sections:
        raise TemplateRenderError(
            f"占位符 {bibliography_key!r} 由 bibliography 参数提供，不能同时出现在 sections 中"
        )
    missing = sorted(keys - set(sections) - ({bibliography_key} if bibliography_key else set()))
    if missing:
        raise TemplateRenderError(f"缺少占位符内容（拒绝静默丢内容）：{missing}")


# ===== docxtpl 渲染 =====


def _render_with_docxtpl(data: bytes, context: dict[str, Any]) -> bytes:
    try:
        template = DocxTemplate(io.BytesIO(data))
        # 坑 #5：autoescape 防止内容里的 < > & 破坏 XML
        template.render(context, autoescape=True)
        buffer = io.BytesIO()
        template.save(buffer)
    except TemplateRenderError:
        raise
    except Exception as exc:
        raise TemplateRenderError(f"模板渲染失败：{exc}") from exc
    return buffer.getvalue()


def _load_rendered(data: bytes) -> Any:
    try:
        return Document(io.BytesIO(data))
    except Exception as exc:  # 渲染产物必须仍可被 python-docx 打开
        raise TemplateRenderError(f"渲染产物不是合法 docx：{exc}") from exc


# ===== 块/行内注入 =====


def _replace_block(
    paragraph: Any,
    lines: Sequence[str],
    *,
    default_role: StyleRole,
    style_map: dict[str, StyleRole],
    role_to_style: dict[StyleRole, str],
) -> None:
    """用若干段落替换占位符段落；样式按语义角色→styleId 应用（坑 #3）。"""
    placeholder = paragraph._p
    parent = placeholder.getparent()
    if parent is None:  # pragma: no cover - 防御：脱离文档树的段落
        return
    position = list(parent).index(placeholder)
    paragraph_properties = placeholder.find(qn("w:pPr"))
    run_properties = _first_run_properties(placeholder)
    placeholder_style_id = _paragraph_style_id(paragraph)
    base_role = (
        style_map.get(placeholder_style_id, default_role) if placeholder_style_id else default_role
    )

    if not lines:
        parent.remove(placeholder)
        return

    for offset, raw_line in enumerate(lines):
        role, content = _line_role_and_text(raw_line, base_role)
        style_id = role_to_style.get(role) or placeholder_style_id
        new_paragraph = _build_paragraph(content, style_id, paragraph_properties, run_properties)
        parent.insert(position + offset, new_paragraph)
    parent.remove(placeholder)


def _replace_inline(paragraph: Any, sentinel: str, replacement: str) -> None:
    """行内占位符替换：优先原地替换 run 文本，跨 run 时兜底重建段落文本。"""
    for run in paragraph.runs:
        if sentinel in run.text:
            run.text = run.text.replace(sentinel, replacement)
            return
    original_text = paragraph.text
    if sentinel not in original_text:  # pragma: no cover - 防御：已被上层删改
        return
    for run in list(paragraph.runs):
        element = run._r
        parent = element.getparent()
        if parent is not None:
            parent.remove(element)
    paragraph.add_run(original_text.replace(sentinel, replacement))


def _append_bibliography(
    document: Any, lines: Sequence[str], role_to_style: dict[StyleRole, str]
) -> None:
    """模板没有参考文献占位符时，追加到正文末尾（同样按角色落样式）。"""
    style_id = role_to_style.get(StyleRole.REFERENCE)
    for line in lines:
        paragraph = document.add_paragraph(line)
        if style_id:
            _apply_paragraph_style(paragraph._p, style_id)


def _build_paragraph(
    text: str,
    style_id: str | None,
    paragraph_properties: Any,
    run_properties: Any,
) -> Any:
    new_paragraph = OxmlElement("w:p")
    if paragraph_properties is not None:
        new_paragraph.append(copy.deepcopy(paragraph_properties))
    paragraph_props = new_paragraph.find(qn("w:pPr"))
    if style_id:
        if paragraph_props is None:
            paragraph_props = OxmlElement("w:pPr")
            new_paragraph.insert(0, paragraph_props)
        _apply_paragraph_style(new_paragraph, style_id)
    if paragraph_props is not None:
        _complete_paragraph_mark_fonts(paragraph_props)

    run = OxmlElement("w:r")
    if run_properties is not None:
        run.append(copy.deepcopy(run_properties))
    run_props = run.find(qn("w:rPr"))
    if run_props is not None:
        # 坑 #1：中文字体需同时给 eastAsia/cs，否则只设 ascii 会回退宋体
        _complete_run_fonts(run_props)

    text_element = OxmlElement("w:t")
    text_element.set(qn("xml:space"), "preserve")
    text_element.text = text
    run.append(text_element)
    new_paragraph.append(run)
    return new_paragraph


def _apply_paragraph_style(paragraph_element: Any, style_id: str) -> None:
    paragraph_props = paragraph_element.find(qn("w:pPr"))
    if paragraph_props is None:
        paragraph_props = OxmlElement("w:pPr")
        paragraph_element.insert(0, paragraph_props)
    style_element = paragraph_props.find(qn("w:pStyle"))
    if style_element is None:
        style_element = OxmlElement("w:pStyle")
        paragraph_props.insert(0, style_element)
    style_element.set(qn("w:val"), style_id)


# ===== 文本与样式辅助 =====


def _section_lines(text: str) -> list[str]:
    return [line.rstrip() for line in str(text).splitlines() if line.strip()]


def _line_role_and_text(line: str, default_role: StyleRole) -> tuple[StyleRole, str]:
    """行首 `#` 前缀覆盖语义角色（用于在正文块内落 H1/H2/H3）。"""
    stripped = line.lstrip()
    hashes = len(stripped) - len(stripped.lstrip("#"))
    if hashes and stripped[hashes : hashes + 1] == " ":
        content = stripped[hashes:].strip()
        if content:
            return _HEADING_ROLES.get(min(hashes, 4), default_role), content
    return default_role, line.strip()


def _invert_style_map(style_map: dict[str, StyleRole]) -> dict[StyleRole, str]:
    """角色 → styleId：同一角色有多个候选时按内置优先顺序取第一个。"""
    inverse: dict[StyleRole, str] = {}
    for role in StyleRole:
        candidates = sorted(style_id for style_id, mapped in style_map.items() if mapped is role)
        if not candidates:
            continue
        preferred = next(
            (item for item in _PREFERRED_STYLE_IDS.get(role, ()) if item in candidates), None
        )
        inverse[role] = preferred or candidates[0]
    return inverse


def _paragraph_style_id(paragraph: Any) -> str | None:
    try:
        return paragraph.style.style_id
    except Exception:  # pragma: no cover - 防御：样式表缺失时退回 None
        return None


def _first_run_properties(paragraph_element: Any) -> Any:
    for run in paragraph_element.findall(qn("w:r")):
        properties = run.find(qn("w:rPr"))
        if properties is not None:
            return properties
    return None


def _complete_run_fonts(run_properties: Any) -> None:
    fonts = run_properties.find(qn("w:rFonts"))
    if fonts is None:
        return
    values = [fonts.get(qn(f"w:{name}")) for name in _FONT_ATTRIBUTES]
    fallback = next((value for value in values if value), None)
    if not fallback:
        return
    for name in _FONT_ATTRIBUTES:
        if not fonts.get(qn(f"w:{name}")):
            fonts.set(qn(f"w:{name}"), fallback)


def _complete_paragraph_mark_fonts(paragraph_properties: Any) -> None:
    run_properties = paragraph_properties.find(qn("w:rPr"))
    if run_properties is not None:
        _complete_run_fonts(run_properties)


def _iter_paragraphs(document: Any) -> Iterable[Any]:
    """遍历正文（含表格单元格）中的全部段落。"""
    from docx.text.paragraph import Paragraph

    body = document.element.body
    for element in body.iter(qn("w:p")):
        yield Paragraph(element, document)


def _sentinel(key: str) -> str:
    """块级占位符的哨兵文本：只含 ASCII，避免被 XML/Jinja 解释。"""
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
    return f"{_SENTINEL_MARKER}{digest}__"


def _block_placeholder_keys(data: bytes, keys: Iterable[str]) -> set[str]:
    """识别「独占段落」的占位符（块级注入），其余按行内替换。"""
    wanted = set(keys)
    blocks: set[str] = set()
    document = Document(io.BytesIO(data))
    for paragraph in _iter_paragraphs(document):
        match = _SOLE_PLACEHOLDER_RE.fullmatch(paragraph.text.strip())
        if match and match.group(1).strip() in wanted:
            blocks.add(match.group(1).strip())
    return blocks


__all__ = ["BIBLIOGRAPHY_KEYS", "render_document"]
