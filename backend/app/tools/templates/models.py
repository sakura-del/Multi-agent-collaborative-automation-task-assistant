"""Word 模板层的契约模型与领域异常（Phase 1 · P1-A / 计划 §1.1）。

设计要点：
- 占位符契约自研：docxtpl **不解析** `w:sdt`（内容控件），因此约定 `{{ key }}` 命名，
  用 `get_undeclared_template_variables()` 扫描待填字段清单（路线图 §4.3）；
- `style_map` 是「Word **styleId** → 语义角色」映射：切换模板只做角色重映射，正文不动，
  这是「一键切换」的架构前提；**绝不按本地化 name 映射**（坑 #3）；
- `TemplateMeta` / `PlaceholderField` 为严格模型（`extra="forbid"`）。
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.core.exceptions import ValidationException


class TemplateError(ValidationException):
    """模板层错误基类（HTTP 422：模板内容不可信或契约不满足）。"""

    default_message = "模板处理失败"
    error_code = "TEMPLATE_ERROR"


class TemplateSecurityError(TemplateError):
    """模板未通过安全校验：`.docm` / 宏 / OLE 嵌入 / zip 炸弹 / 路径穿越（坑 #7）。"""

    default_message = "模板未通过安全校验"
    error_code = "TEMPLATE_SECURITY"


class TemplateValidationError(TemplateError):
    """模板结构或 Jinja 语法不合法（不是可用的 .docx 模板）。"""

    default_message = "模板格式不合法"
    error_code = "TEMPLATE_INVALID"


class TemplateRenderError(TemplateError):
    """渲染契约不满足：未知占位符 / 缺少必需内容（拒绝静默丢内容）。"""

    default_message = "模板渲染失败"
    error_code = "TEMPLATE_RENDER"


class PlaceholderField(BaseModel):
    """模板中的一个 `{{ key }}` 占位符（UI 上让用户映射到综述章节）。"""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=128)
    label: str = Field(default="", max_length=128)
    required: bool = True
    sample: str = Field(default="", max_length=500)


class StyleRole(StrEnum):
    """语义角色（`style_map` 的值）：与具体 Word 模板解耦的排版意图。

    计划 §1.1 写作 `class StyleRole(str, Enum)`；这里用等价的 `StrEnum`
    （Python 3.11+ 标准写法，仓库既有 `ReferenceVerdict` 同款，且满足 ruff UP042）。
    成员本身仍是 `str`，JSON 序列化结果与 `str, Enum` 完全一致。
    """

    TITLE = "title"
    H1 = "h1"
    H2 = "h2"
    H3 = "h3"
    BODY = "body"
    CAPTION = "caption"
    REFERENCE = "reference"


class TemplateMeta(BaseModel):
    """模板检查结果：占位符清单 + styleId→角色映射 + 指纹。"""

    model_config = ConfigDict(extra="forbid")

    template_id: str = Field(min_length=1, max_length=64)
    """由源文件内容 sha256 派生（`tpl-<前 16 位>`），同一模板内容稳定不变。"""

    name: str = Field(min_length=1, max_length=256)
    placeholders: list[PlaceholderField] = Field(default_factory=list)
    style_map: dict[str, StyleRole] = Field(default_factory=dict)
    checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    """源 docx 文件字节的 sha256（完整性/去重）。"""


__all__ = [
    "PlaceholderField",
    "StyleRole",
    "TemplateError",
    "TemplateMeta",
    "TemplateRenderError",
    "TemplateSecurityError",
    "TemplateValidationError",
]
