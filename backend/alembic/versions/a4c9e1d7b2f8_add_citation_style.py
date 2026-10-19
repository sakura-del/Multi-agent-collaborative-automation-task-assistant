"""add citation style

Revision ID: a4c9e1d7b2f8
Revises: 8f1c2b3d4e5f
Create Date: 2026-10-19 13:10:00.000000

Issue #4 M2-1：新增 `citation_style` 表（用户自定义 CSL 引用样式）。

- `user_id` 可空：NULL = 系统预置（全局可见），非 NULL = 该用户私有（FK → user.id，级联删除）；
- 同用户内 `style_id` 唯一（`uq_citation_style_user_style_id`）；
- 预置样式（`user_id IS NULL`）的 `style_id` 由**部分唯一索引**
  `uq_citation_style_preset_style_id` 兜底：普通唯一约束不约束 NULL，
  故 `postgresql_where` 与 `sqlite_where` 必须双写，保证两方言行为一致。

`server_default` 用 `sa.func.now()` 而非 `sa.text("now()")`：前者在 PostgreSQL 渲染
`now()`、在 SQLite 渲染 `CURRENT_TIMESTAMP`（与 `app/db/base.py` 的 ORM 声明一致），
后者在 SQLite 上插入时会报「no such function: now」。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4c9e1d7b2f8"
down_revision: str | None = "8f1c2b3d4e5f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_USER_ID_IS_NULL = sa.text("user_id IS NULL")


def upgrade() -> None:
    op.create_table(
        "citation_style",
        sa.Column("id", sa.Uuid(), nullable=False, comment="样式ID"),
        sa.Column("user_id", sa.Uuid(), nullable=True, comment="所有者ID（NULL=系统预置）"),
        sa.Column(
            "style_id",
            sa.String(length=64),
            nullable=False,
            comment="样式标识（与 CSL 文件名一致）",
        ),
        sa.Column("title", sa.String(length=256), nullable=False, comment="人类可读样式名"),
        sa.Column(
            "numbering",
            sa.String(length=32),
            nullable=False,
            comment="编号模式：citation-number/none",
        ),
        sa.Column(
            "ordering",
            sa.String(length=32),
            nullable=False,
            comment="排序模式：citation-order/author-date",
        ),
        sa.Column(
            "default_locale",
            sa.String(length=16),
            nullable=False,
            server_default="en-US",
            comment="默认语言",
        ),
        sa.Column(
            "filename",
            sa.String(length=128),
            nullable=False,
            comment="磁盘文件名（服务端生成，不信任客户端）",
        ),
        sa.Column(
            "content_sha256",
            sa.String(length=64),
            nullable=False,
            comment="内容指纹（去重与完整性）",
        ),
        sa.Column("size_bytes", sa.Integer(), nullable=False, comment="文件大小（字节）"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            comment="创建时间",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            comment="更新时间",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name="fk_citation_style_user_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_citation_style"),
        sa.UniqueConstraint("user_id", "style_id", name="uq_citation_style_user_style_id"),
        comment="引用样式（用户上传的自定义 CSL）",
    )
    # 用户维度列表查询（owner-scoped）
    op.create_index("ix_citation_style_user_id", "citation_style", ["user_id"], unique=False)
    # 部分唯一索引：预置样式（user_id IS NULL）的 style_id 唯一，SQLite / PostgreSQL 行为一致
    op.create_index(
        "uq_citation_style_preset_style_id",
        "citation_style",
        ["style_id"],
        unique=True,
        postgresql_where=_USER_ID_IS_NULL,
        sqlite_where=_USER_ID_IS_NULL,
    )


def downgrade() -> None:
    # 先删索引再删表；顺序与 upgrade 相反，保证可完整回滚
    op.drop_index("uq_citation_style_preset_style_id", table_name="citation_style")
    op.drop_index("ix_citation_style_user_id", table_name="citation_style")
    op.drop_table("citation_style")
