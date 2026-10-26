"""引用样式 HTTP Schema（Issue #4 M2 / 计划 §1.3）。

- `numbering` / `ordering` 直接复用 `app.tools.csl.models` 的冻结 Literal 类型，不另造并行类型；
- 响应模型 `extra="forbid"`：字段集合即契约，前端（`frontend/src/api/types.ts`）按此消费。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.tools.csl.models import Numbering, Ordering

__all__ = ["CitationStyleList", "CitationStyleOut", "CitationStylePreview", "CitationStylePreviews"]


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


class CitationStylePreview(BaseModel):
    """单个样式的**真实渲染**预览（2026-10-26 样式页重设计）。

    ## 为什么由后端渲染

    渲染必须走 citeproc + 真实 `.csl`。前端自己写一套格式化器 = 第二份 CSL 实现，
    必然与导出结果不一致 —— 用户会看到「预览是这样、下载出来不是」，
    比没有预览更糟。导出走的就是同一个 `CitationRenderer`。
    """

    model_config = ConfigDict(extra="forbid")

    style_id: str
    title: str
    source: Literal["preset", "custom"]
    is_default: bool = False
    """是否为产品默认样式（前端打「默认」徽标）。"""

    entries: list[str] = Field(description="按该样式渲染的参考文献条目（样例文献）")
    markers: dict[str, str] = Field(
        default_factory=dict, description="正文行内标记：ref_id → 标记文本"
    )
    warnings: list[str] = Field(
        default_factory=list,
        description=(
            "渲染告警。**非空即表示走了兜底格式化器** —— 此时输出不代表该样式，"
            "前端必须显式提示，不能当正常预览展示。"
        ),
    )


class CitationStylePreviews(BaseModel):
    """全部样式的预览（顺序与 `CitationStyleList.styles` 一致）。"""

    model_config = ConfigDict(extra="forbid")

    previews: list[CitationStylePreview]
    sample_count: int = Field(description="样例文献条数（前端可据此说明预览口径）")
