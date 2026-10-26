"""add reference_library_item table

Revision ID: b7d4e9f1c6a2
Revises: a3c9e6d2b8f1
Create Date: 2026-10-26 14:10:00.000000

## 为什么需要这个迁移

取证发现 `/references/import` 能正确解析 BibTeX / RIS，但解析完**什么也做不了**：
没有引用库表、没有保存路径，任务文献只来自检索源（arXiv / OpenAlex / PubMed）。
用户把 Zotero 导出的 `.bib` 粘进来，看到结果，然后无法用于任何任务。

本迁移建 `reference_library_item`，让导入结果能存下来并作为任务文献源
（计划：`docs/superpowers/plans/2026-10-26-00-reference-library-plan.md`）。

## 表设计要点

- `payload` 存**整份 `CslItem` JSON**：`CslItem` 是 `extra="forbid"` 的严格模型，
  字段随 CSL 支持演进会增加（`medium` 就是后加的）。拆列意味着每加字段都要迁移；
  存 JSON 则「渲染/导出直接 `from_csl_json(payload)` 还原」，与现有 CSL 管线零阻抗。
- `title` / `issued_year` / `doi` 是**冗余列**，只为列表查询与排序，**不是真源**。
- `UniqueConstraint("user_id", "ref_id")`：同一用户内 `ref_id` 唯一 →
  **重复导入幂等**（用户会重复粘同一份文件）。
- `(user_id, created_at, id)` 索引：列表按此排序。**必须带 `id`** ——
  同事务批量插入的多行 `created_at` 完全相同，PostgreSQL 对并列行返回任意顺序
  （AGENTS §5 #14，本项目已因此踩过两次）。

## JSON 类型

PostgreSQL 用 `JSONB`（可索引、去重键），其他方言（测试用 SQLite）退化为 `JSON`。
与 ORM 模型的 `JSON().with_variant(JSONB(), "postgresql")` 保持一致。

## 幂等

建表/建索引前先查 `inspector`（PostgreSQL 与 SQLite 都支持）。
Phase 1 `email_token` 的教训：非幂等迁移会让全新库炸。

## 时间戳

`created_at` / `updated_at` 由 `Base` 统一提供（`app/db/base.py`），
迁移必须一并建出来 —— 这正是 `email_token` 缺陷的成因。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b7d4e9f1c6a2"
down_revision: str | None = "a3c9e6d2b8f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _has_table(name: str) -> bool:
    return name in _inspector().get_table_names()


def _has_index(table: str, index_name: str) -> bool:
    if not _has_table(table):
        return False
    return index_name in {idx["name"] for idx in _inspector().get_indexes(table)}


def upgrade() -> None:
    if not _has_table("reference_library_item"):
        op.create_table(
            "reference_library_item",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("user_id", sa.Uuid(), nullable=False),
            sa.Column("ref_id", sa.String(length=200), nullable=False),
            sa.Column(
                "payload",
                sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
                nullable=False,
            ),
            sa.Column("title", sa.String(length=500), nullable=False, server_default=""),
            sa.Column("issued_year", sa.Integer(), nullable=True),
            sa.Column("doi", sa.String(length=255), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "user_id", "ref_id", name="uq_reference_library_item_user_ref"
            ),
        )
    if not _has_index("reference_library_item", "ix_reference_library_item_user_id"):
        op.create_index(
            "ix_reference_library_item_user_id",
            "reference_library_item",
            ["user_id"],
        )
    if not _has_index(
        "reference_library_item", "ix_reference_library_item_user_created"
    ):
        op.create_index(
            "ix_reference_library_item_user_created",
            "reference_library_item",
            ["user_id", "created_at", "id"],
        )


def downgrade() -> None:
    if _has_index(
        "reference_library_item", "ix_reference_library_item_user_created"
    ):
        op.drop_index(
            "ix_reference_library_item_user_created",
            table_name="reference_library_item",
        )
    if _has_index("reference_library_item", "ix_reference_library_item_user_id"):
        op.drop_index(
            "ix_reference_library_item_user_id", table_name="reference_library_item"
        )
    if _has_table("reference_library_item"):
        op.drop_table("reference_library_item")
