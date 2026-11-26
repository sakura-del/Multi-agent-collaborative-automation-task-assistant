"""新增 task_draft 任务创建草稿表（Issue #68）。

Revision ID: d8b2f5c3a1e9
Revises: c7a1e4b9d2f3
Create Date: 2026-11-26 18:45:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision = "d8b2f5c3a1e9"
down_revision = "c7a1e4b9d2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if inspector.has_table("task_draft"):
        return

    op.create_table(
        "task_draft",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_task_draft_user"),
    )
    op.create_index("ix_task_draft_user_id", "task_draft", ["user_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("task_draft"):
        return
    op.drop_index("ix_task_draft_user_id", table_name="task_draft")
    op.drop_table("task_draft")
