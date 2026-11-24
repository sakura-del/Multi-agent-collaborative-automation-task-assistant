"""task_routine 增加可空 memory_brief 列（Issue #42 跨期增量记忆）。

Revision ID: 9e2f5b8c3a1d
Revises: 7c4e9a1f2b3d
Create Date: 2026-11-24 17:30:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "9e2f5b8c3a1d"
down_revision = "7c4e9a1f2b3d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "task_routine",
        sa.Column(
            "memory_brief",
            sa.JSON(),
            nullable=True,
            comment="跨期增量状态记忆（结构化 JSON）",
        ),
    )


def downgrade() -> None:
    op.drop_column("task_routine", "memory_brief")
