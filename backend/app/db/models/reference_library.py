"""REFERENCE_LIBRARY_ITEM 表：用户的个人引用库（2026-10-26 引用库计划 §3.1）。

## 为什么需要这张表

取证发现 `/references/import` 页能正确解析 BibTeX / RIS，但**解析完什么也做不了**：
后端没有引用库表，`POST /references/import` 纯解析不落库，任务文献来自检索源
（arXiv / OpenAlex / PubMed）。用户把 Zotero 导出的 `.bib` 粘进来，看到结果，
然后无法用于任何任务。

产品定位是「生成即合规、引用可核验的学术综述工作台」。用户自带文献库是真实需求：
综述要引的往往就是自己读过的那批，而不是检索源随机返回的。

## 为什么 `payload` 存整份 CslItem JSON 而不是拆列

`CslItem` 是 `extra="forbid"` 的严格模型，字段随 CSL 支持演进会增加
（`medium` 就是后加的）。拆列意味着每加一个字段都要迁移；存 JSON 则
「导出/渲染直接 `from_csl_json(payload)` 还原」，与现有 CSL 管线零阻抗。

`title` / `issued_year` / `doi` 三个冗余列**只为列表展示与查询**，
**不是真源** —— 真源永远是 `payload`。二者不一致时以 `payload` 为准。

## 约束

- `UniqueConstraint("user_id", "ref_id")`：同一用户内 `ref_id` 唯一 →
  **重复导入幂等**（用户会重复粘同一份文件）；
- `(user_id, created_at, id)` 索引：列表按此排序。**必须带 `id`** ——
  同事务批量插入的多行 `created_at` 完全相同，PostgreSQL 对并列行返回任意顺序
  （AGENTS §5 #14，本项目已因此踩过两次）。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base

#: PostgreSQL 用 JSONB（可索引、去重键），其他方言（测试用 SQLite）退化为 JSON。
_JSON_TYPE = JSON().with_variant(JSONB(), "postgresql")


class ReferenceLibraryItem(Base):
    """引用库条目：一条文献的完整 CSL-JSON 载荷 + 展示用冗余列。"""

    __tablename__ = "reference_library_item"

    __table_args__ = (
        UniqueConstraint("user_id", "ref_id", name="uq_reference_library_item_user_ref"),
        Index(
            "ix_reference_library_item_user_created",
            "user_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, comment="条目ID")

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="所有者ID（owner-scoped，跨用户不可见）",
    )

    ref_id: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        comment="稳定标识（沿用 CslItem.id 规则：优先 doi:<归一化 DOI>）",
    )

    payload: Mapped[dict[str, Any]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
        comment="完整 CslItem.model_dump(mode='json')——真源",
    )

    # ===== 展示用冗余列（非真源，便于列表查询与排序）=====

    title: Mapped[str] = mapped_column(
        String(500), nullable=False, default="", comment="题名（冗余，便于列表展示）"
    )
    issued_year: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="出版年份（冗余，便于排序/筛选）"
    )
    doi: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="DOI（冗余，便于去重提示）"
    )

    # Base 已提供 created_at / updated_at，此处不重复声明

    def to_csl_item(self) -> Any:
        """还原成 `CslItem`（真源是 `payload`，冗余列不参与）。

        放在模型上而不是服务里：渲染/导出/文献源三处都要用，
        挂在 ORM 行上调用最自然（`row.to_csl_item()`）。
        延迟导入避免 `db.models` ↔ `tools.csl` 的循环依赖。
        """
        from app.tools.csl.models import CslItem

        return CslItem.model_validate(self.payload)
