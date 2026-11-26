"""用户自带数据源凭据表（Issue #67 · BYOK）。

Revision ID: c7a1e4b9d2f3
Revises: 4f8a2d1c9e7b
Create Date: 2026-11-26 15:20:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision = "c7a1e4b9d2f3"
down_revision = "4f8a2d1c9e7b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if inspector.has_table("data_source_credential"):
        return

    op.create_table(
        "data_source_credential",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("api_key_ciphertext", sa.Text(), nullable=False),
        sa.Column("key_mask", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default="active",
            nullable=False,
        ),
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
        sa.UniqueConstraint(
            "user_id",
            "provider",
            name="uq_data_source_credential_user_provider",
        ),
    )
    op.create_index(
        "ix_data_source_credential_user_id",
        "data_source_credential",
        ["user_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("data_source_credential"):
        return
    op.drop_index("ix_data_source_credential_user_id", table_name="data_source_credential")
    op.drop_table("data_source_credential")
