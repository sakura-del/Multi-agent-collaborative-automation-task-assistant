"""Word 模板 SSTI 防护测试（安全 Issue #9）。

背景
----
`docxtpl` 默认使用 **非沙箱** 的 Jinja2 `Environment`。用户上传的 `.docx`
模板里只要写入 `{{ ... }}` 表达式，就会在渲染时被执行，可升级为远程命令执行
（认证用户即可触发）。修复分两层：

1. **上传校验**：`validate_template_bytes` 只允许简单占位符
   （`{{ identifier }}`、`{%p identifier %}`、`{%tr identifier %}`），
   拒绝任意表达式与语句；
2. **渲染沙箱**：渲染时使用 `SandboxedEnvironment`，即使校验被绕过，
   `__class__` 之类的属性访问也会被拦截。

本文件锁定这两层，防止回归。
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from docx import Document

from app.tools.templates.inspector import (
    inspect_template,
    validate_template_bytes,
)
from app.tools.templates.models import TemplateValidationError
from app.tools.templates.renderer import _render_with_docxtpl, render_document

# 经典的字符串属性逃逸链：不引入任何未声明变量，
# 因此旧实现里 `find_undeclared_variables()` 返回空集，契约校验拦不住。
ESCAPE_PAYLOAD = (
    "{{ (''.__class__.__mro__[1].__subclasses__()"
    "|selectattr('__name__','equalto','_wrap_close')|first)"
    ".__init__.__globals__['__builtins__']['__import__']('os')"
    ".popen('echo SSTI_MARKER').read() }}"
)


def _build_docx(paragraphs: list[str]) -> bytes:
    document = Document()
    for text in paragraphs:
        document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# ===== 第一层：上传校验拒绝任意表达式 =====


class TestValidationRejectsExpressions:
    """`validate_template_bytes` 必须拒绝任何非常见占位符的 Jinja 代码。"""

    def test_arithmetic_expression_rejected(self) -> None:
        data = _build_docx(["结果：{{ 7 * 7 }}"])
        with pytest.raises(TemplateValidationError, match="模板表达式"):
            validate_template_bytes(data, source="arith.docx")

    def test_escape_chain_rejected(self) -> None:
        data = _build_docx([ESCAPE_PAYLOAD])
        with pytest.raises(TemplateValidationError, match="模板表达式"):
            validate_template_bytes(data, source="escape.docx")

    def test_control_statement_rejected(self) -> None:
        data = _build_docx(["{% if x %}条件段落{% endif %}"])
        with pytest.raises(TemplateValidationError, match="模板表达式"):
            validate_template_bytes(data, source="stmt.docx")

    def test_filter_expression_rejected(self) -> None:
        data = _build_docx(["{{ title|upper }}"])
        with pytest.raises(TemplateValidationError, match="模板表达式"):
            validate_template_bytes(data, source="filter.docx")

    def test_attribute_access_rejected(self) -> None:
        data = _build_docx(["{{ user.__class__ }}"])
        with pytest.raises(TemplateValidationError, match="模板表达式"):
            validate_template_bytes(data, source="attr.docx")


# ===== 第一层：合法占位符必须继续通过 =====


class TestValidationAcceptsSimplePlaceholders:
    """收紧校验不能误伤现有模板形态。"""

    def test_simple_placeholder_accepted(self) -> None:
        data = _build_docx(["标题：{{ title }}", "作者：{{ author }}"])
        validate_template_bytes(data, source="normal.docx")

    def test_paragraph_level_control_tag_rejected(self) -> None:
        """docxtpl 的 `{%p ... %}` 后面只能跟 Jinja 语句（`if`/`for` 等），
        而语句正是 SSTI 载体，因此一律拒绝。标签独占段落以保证先通过
        段落级校验、真正命中表达式白名单。"""
        data = _build_docx(["{%p if flag %}"])
        with pytest.raises(TemplateValidationError, match="模板表达式"):
            validate_template_bytes(data, source="block.docx")

    def test_table_row_control_tag_rejected(self) -> None:
        data = _build_docx(["{%tr for item in items %}"])
        with pytest.raises(TemplateValidationError, match="模板表达式"):
            validate_template_bytes(data, source="block.docx")

    def test_underscore_and_digits_accepted(self) -> None:
        data = _build_docx(["{{ chapter_1_1 }}", "{{ _private }}"])
        validate_template_bytes(data, source="names.docx")


# ===== 第二层：渲染沙箱兜底 =====


class TestRenderSandbox:
    """即使上传校验被绕过，渲染也不得执行任意代码。"""

    def test_sandbox_blocks_escape_chain(self) -> None:
        data = _build_docx([ESCAPE_PAYLOAD])
        # 直接调用渲染层，模拟「校验被绕过」的场景
        with pytest.raises(Exception, match=r"渲染失败|unsafe|SecurityError"):
            _render_with_docxtpl(data, {})

    def test_sandbox_blocks_single_class_access(self) -> None:
        """单独 `{{ ''.__class__ }}` 被沙箱求值为空（不泄露类型对象）；
        任何进一步的属性访问都会被 `SecurityError` 拦截。"""
        data = _build_docx(["{{ ''.__class__ }}"])
        output = _render_with_docxtpl(data, {})
        text = "\n".join(paragraph.text for paragraph in Document(io.BytesIO(output)).paragraphs)
        assert "<class" not in text
        assert "str" not in text

    def test_sandbox_blocks_mro_traversal(self) -> None:
        data = _build_docx(["{{ ''.__class__.__mro__ }}"])
        with pytest.raises(Exception, match=r"unsafe|SecurityError|渲染失败"):
            _render_with_docxtpl(data, {})


# ===== 端到端：正常模板渲染行为不变 =====


FIXTURES = Path(__file__).parent / "fixtures" / "templates"
TEMPLATE = FIXTURES / "school_template.docx"

SECTIONS = {
    "title": "面向多智能体的引用可信度研究",
    "abstract": "本文研究引用核验的确定性方法。",
    "chapter_1": "引言",
    "chapter_1_1": "研究背景",
    "body_main": "正文第一段。\n正文第二段。",
    "author": "张伟",
}
BIBLIOGRAPHY = ["[1] 张三. 文献综述方法. 北京: 科学出版社, 2024."]


class TestNormalRenderingUnchanged:
    """沙箱替换后，正常模板的渲染结果必须与之前一致。"""

    def test_normal_template_still_renders(self) -> None:
        meta = inspect_template(TEMPLATE)
        output = render_document(
            template=TEMPLATE,
            sections=SECTIONS,
            bibliography=BIBLIOGRAPHY,
            style_map=meta.style_map,
        )
        document = Document(io.BytesIO(output))
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        assert "面向多智能体的引用可信度研究" in text
        assert "本文研究引用核验的确定性方法。" in text
        assert "{{" not in text
        assert "{%" not in text

    def test_autoescape_preserved(self) -> None:
        meta = inspect_template(TEMPLATE)
        output = render_document(
            template=TEMPLATE,
            sections={**SECTIONS, "body_main": "不等式 a < b & c > d"},
            bibliography=BIBLIOGRAPHY,
            style_map=meta.style_map,
        )
        document = Document(io.BytesIO(output))
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        assert "a < b & c > d" in text
