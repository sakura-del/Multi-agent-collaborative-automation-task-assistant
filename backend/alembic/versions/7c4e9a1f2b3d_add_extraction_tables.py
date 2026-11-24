"""新增自定义论文抽取表 extraction_table 与 extraction_cell（Issue #36）。

Revision ID: 7c4e9a1f2b3d
Revises: 2b9f4c7d1e6a
Create Date: 2026-11-24 14:25:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "7c4e9a1f2b3d"
down_revision = "2b9f4c7d1e6a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "extraction_table",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("columns", sa.JSON(), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["task.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_extraction_table_user_id", "extraction_table", ["user_id"], unique=False)
    op.create_index("ix_extraction_table_task_id", "extraction_table", ["task_id"], unique=False)
    op.create_index("ix_extraction_table_status", "extraction_table", ["status"], unique=False)

    op.create_table(
        "extraction_cell",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("table_id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("column_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("quote", sa.Text(), nullable=True),
        sa.Column("passage", sa.Text(), nullable=True),
        sa.Column("locator", sa.JSON(), nullable=True),
        sa.Column("evidence_scope", sa.String(length=24), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["table_id"], ["extraction_table.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "table_id",
            "source_type",
            "source_id",
            "column_key",
            name="uq_extraction_cell_coord",
        ),
    )
    op.create_index("ix_extraction_cell_table_id", "extraction_cell", ["table_id"], unique=False)
    op.create_index(
        "ix_extraction_cell_table_source",
        "extraction_cell",
        ["table_id", "source_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_extraction_cell_table_source", table_name="extraction_cell")
    op.drop_index("ix_extraction_cell_table_id", table_name="extraction_cell")
    op.drop_table("extraction_cell")

    op.drop_index("ix_extraction_table_status", table_name="extraction_table")
    op.drop_index("ix_extraction_table_task_id", table_name="extraction_table")
    op.drop_index("ix_extraction_table_user_id", table_name="extraction_table")
    op.drop_table("extraction_table")
