"""引用样式 HTTP Schema（Issue #4 M2 / 计划 §1.3）。

- `numbering` / `ordering` 直接复用 `app.tools.csl.models` 的冻结 Literal 类型，不另造并行类型；
- 响应模型 `extra="forbid"`：字段集合即契约，前端（`frontend/src/api/types.ts`）按此消费。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.tools.csl.models import Numbering, Ordering

__all__ = ["CitationStyleList", "CitationStyleOut"]


class CitationStyleOut(BaseModel):
    """单个引用样式（`source="preset"` 为预置，`"custom"` 为该用户上传）。"""

    model_config = ConfigDict(extra="forbid")

    style_id: str
    title: str
    numbering: Numbering
    ordering: Ordering
    default_locale: str
    source: Literal["preset", "custom"]
    is_default: bool = False


class CitationStyleList(BaseModel):
    """样式列表响应：预置样式在前，`default_style_id` 由服务端给出。"""

    model_config = ConfigDict(extra="forbid")

    styles: list[CitationStyleOut]
    default_style_id: str
