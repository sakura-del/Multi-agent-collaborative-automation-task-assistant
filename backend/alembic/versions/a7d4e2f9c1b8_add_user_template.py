"""add user_template table

Revision ID: a7d4e2f9c1b8
Revises: f5c2a8d1b3e7
Create Date: 2026-10-20 11:30:00.000000

Phase 1 P1-A 接线：用户上传的 Word 模板元数据表。

文件本体落盘（`{storage_root}/{user_id}/{template_id}.docx`），DB 只存元数据；
`placeholders` / `style_map` 用 JSON 列（结构见 `app.tools.templates.models`）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7d4e2f9c1b8"
down_revision: str | None = "f5c2a8d1b3e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_template",
        sa.Column("id", sa.Uuid(), nullable=False, comment="记录ID"),
        sa.Column("user_id", sa.Uuid(), nullable=False, comment="所有者ID"),
        sa.Column(
            "template_id",
            sa.String(length=64),
            nullable=False,
            comment="模板标识（由内容 sha256 派生）",
        ),
        sa.Column("name", sa.String(length=256), nullable=False, comment="用户可见名称"),
        sa.Column(
            "checksum",
            sa.String(length=64),
            nullable=False,
            comment="源 docx 字节的 sha256（完整性/去重）",
        ),
        sa.Column(
            "placeholders",
            sa.JSON(),
            nullable=False,
            comment="占位符契约（PlaceholderField 列表）",
        ),
        sa.Column(
            "style_map",
            sa.JSON(),
            nullable=False,
            comment="Word styleId → 语义角色",
        ),
        sa.Column("size", sa.Integer(), nullable=False, comment="源文件字节数"),
        # Base 提供的两列（漏建会让 UPDATE 在真实 PostgreSQL 上失败，见
        # tests/test_migration_schema_consistency.py）
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
            name="fk_user_template_user_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_user_template"),
        sa.UniqueConstraint(
            "user_id", "template_id", name="uq_user_template_user_template_id"
        ),
        comment="用户上传的 Word 模板元数据",
    )
    # 按用户查列表 / 按 template_id 定位
    op.create_index("ix_user_template_user_id", "user_template", ["user_id"], unique=False)
    op.create_index(
        "ix_user_template_template_id", "user_template", ["template_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_user_template_template_id", table_name="user_template")
    op.drop_index("ix_user_template_user_id", table_name="user_template")
    op.drop_table("user_template")
