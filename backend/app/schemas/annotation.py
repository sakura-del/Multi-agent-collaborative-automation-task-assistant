"""批注相关 Pydantic 契约（Phase 2 · 团队工作区）。

## 为什么 `anchor_text` 与 `anchor_key` 都在

- `anchor_text`：正常写入路径，前端传**段落原文**，服务端算指纹
  （前后端各算一份迟早口径不一致：空白归一化、换行风格）。
- `anchor_key`：已有指纹的回放路径（导入、批量重建），给高级调用方用。

两者都给时以 `anchor_text` 为准（服务端算的才是权威）。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AnchorType = Literal["task", "paragraph", "citation"]


class AnnotationCreate(BaseModel):
    """新建批注请求。"""

    model_config = ConfigDict(extra="forbid")

    anchor_type: AnchorType = Field(default="task", description="锚点类型")
    content: str = Field(min_length=1, max_length=4000, description="批注正文")
    anchor_text: str | None = Field(
        default=None,
        max_length=4000,
        description="段落原文（anchor_type=paragraph 时必填，服务端算指纹）",
    )
    anchor_key: str | None = Field(
        default=None,
        max_length=256,
        description="已算好的指纹 / citation 的 source_id",
    )


class AnnotationUpdate(BaseModel):
    """修改批注正文。"""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=4000, description="批注正文")


class AnnotationOut(BaseModel):
    """批注响应。"""

    model_config = ConfigDict(from_attributes=True)

    annotation_id: uuid.UUID
    task_id: uuid.UUID
    author_id: uuid.UUID
    author_name: str | None = None
    anchor_type: str
    anchor_key: str | None = None
    content: str
    resolved_at: datetime | None = None
    created_at: datetime


class ParagraphMatchOut(BaseModel):
    """段落批注的锚点匹配状态。

    `matched=false` 表示原文已变更（任务重跑后段落被改写）——
    前端必须显示这个状态，不能把批注挂到别的段落上。
    """

    annotation_id: str
    fingerprint: str
    content: str
    author_id: str
    matched: bool
    paragraph_index: int | None = None
    resolved: bool = False
