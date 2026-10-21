"""TASK_ANNOTATION：导师批注（Phase 2 · 团队工作区）。

## 锚点设计：为什么用内容指纹，不用字符偏移

任务产出（Markdown）会因**重跑**而整体变化（模型输出不同、引用数量不同）。
字符偏移在重跑后立刻失效——更糟的是会**静默错挂**到别的段落，导师的
「这段引用缺页码」可能挂到完全无关的文字上。

所以用**段落内容指纹**做锚点：

- `anchor_type="paragraph"` 时，`anchor_key` = 该段落文本的 `sha256[:16]`；
- 渲染时对每个段落算指纹，匹配上的段落高亮并挂批注；
- 段落被改写 → 指纹失配 → 前端显示「原文已变更」，**不静默错挂**。

这是有意的取舍：宁可让导师知道「这条批注的原文已改」，也不要让批注出现在
错误的位置上。

## 锚点类型

| `anchor_type` | `anchor_key` | 用途 |
| --- | --- | --- |
| `task` | NULL | 任务级批注（整体评价，不定位段落） |
| `paragraph` | 指纹（16 位 hex） | 段落级批注 |
| `citation` | `source_id` | 引用级批注（本轮预留枚举，暂未在 UI 暴露） |

## 已解决状态

`resolved_at` 非空表示学生已处理。**不删批注**：导师需要看到历史意见，
删掉会让「这条意见我提过吗」无从查证。
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

#: 合法锚点类型。
ANNOTATION_ANCHOR_TYPES: tuple[str, ...] = ("task", "paragraph", "citation")

#: 段落指纹长度（hex 字符数）。16 位 = 64 bit，碰撞概率对本场景可忽略，
#: 且短到能放进 URL 查询参数与日志。
FINGERPRINT_LENGTH = 16


def paragraph_fingerprint(text: str) -> str:
    """计算段落指纹（`sha256` 前 16 位 hex）。

    **必须先 `strip()`**：Markdown 渲染前后可能有首尾空白差异（换行、缩进），
    不归一化会让同一段落算出两个指纹，批注随机失配。

    为什么不用行号/偏移：见模块 docstring——重跑会整体变化。
    """
    normalized = text.strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:FINGERPRINT_LENGTH]


class TaskAnnotation(Base):
    """任务批注（导师 → 学生）。"""

    __tablename__ = "task_annotation"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, comment="批注ID")
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("task.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="任务ID",
    )
    author_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="批注人ID",
    )
    anchor_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="task",
        comment="锚点类型：task/paragraph/citation",
    )
    anchor_key: Mapped[str | None] = mapped_column(
        String(256),
        nullable=True,
        comment="锚点键（paragraph=内容指纹，citation=source_id，task=NULL）",
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="批注正文")
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="已解决时间（非空表示学生已处理）"
    )
    # created_at / updated_at 由 `Base` 统一提供，不重复声明
