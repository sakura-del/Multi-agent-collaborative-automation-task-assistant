"""Word 模板层（Phase 1 · P1-A：「Word 模板导入 + 一键切换」）。

对外接口（计划 §1.1 冻结）：

    inspect_template(path) -> TemplateMeta
    render_document(*, template, sections, bibliography, style_map) -> bytes

设计边界：
- 本模块**不碰引用引擎**：参考文献行由调用方用 CSL 渲染好后传入；
- `style_map` 是「Word styleId → 语义角色」映射，切换模板只做角色重映射，正文不动；
- 模板是**不可信输入**：`.docm`/宏/OLE/zip 炸弹拒绝，外部关系剥离后再渲染；
- 依赖 `docxtpl`（LGPL-2.1，仅 import 使用，不修改其源码）。
"""

from __future__ import annotations

from app.tools.templates.inspector import (
    DOCX_SUFFIX,
    MAX_ENTRY_COUNT,
    MAX_UNCOMPRESSED_BYTES,
    extract_placeholder_keys,
    infer_style_role,
    inspect_template,
    sanitize_template_bytes,
    scan_style_map,
    validate_template_bytes,
)
from app.tools.templates.models import (
    PlaceholderField,
    StyleRole,
    TemplateError,
    TemplateMeta,
    TemplateRenderError,
    TemplateSecurityError,
    TemplateValidationError,
)
from app.tools.templates.renderer import BIBLIOGRAPHY_KEYS, render_document

__all__ = [
    "BIBLIOGRAPHY_KEYS",
    "DOCX_SUFFIX",
    "MAX_ENTRY_COUNT",
    "MAX_UNCOMPRESSED_BYTES",
    "PlaceholderField",
    "StyleRole",
    "TemplateError",
    "TemplateMeta",
    "TemplateRenderError",
    "TemplateSecurityError",
    "TemplateValidationError",
    "extract_placeholder_keys",
    "infer_style_role",
    "inspect_template",
    "render_document",
    "sanitize_template_bytes",
    "scan_style_map",
    "validate_template_bytes",
]
