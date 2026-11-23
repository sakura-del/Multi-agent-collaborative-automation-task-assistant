"""新增 oauth_account 表，并允许 user.password_hash 为空（支持 OAuth/免密用户）。

Revision ID: e8f1a2b3c4d5
Revises: b4e7f6a9c2d5
Create Date: 2026-11-23 13:02:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e8f1a2b3c4d5"
down_revision = "b4e7f6a9c2d5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("user") as batch_op:
        batch_op.alter_column(
            "password_hash",
            existing_type=sa.String(length=128),
            nullable=True,
        )

    op.create_table(
        "oauth_account",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_user_id", sa.String(length=128), nullable=False),
        sa.Column("provider_email", sa.String(length=128), nullable=True),
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
        sa.UniqueConstraint("provider", "provider_user_id", name="uq_oauth_account_provider_uid"),
    )
    op.create_index("ix_oauth_account_user_id", "oauth_account", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_oauth_account_user_id", table_name="oauth_account")
    op.drop_table("oauth_account")
    with op.batch_alter_table("user") as batch_op:
        batch_op.alter_column(
            "password_hash",
            existing_type=sa.String(length=128),
            nullable=False,
        )
