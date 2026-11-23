"""audit_log：关键操作审计（Issue #25 沉淀项 3）

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-11-20

只增不改的旁路审计表：actor 外键 SET NULL（删用户不丢审计），
actor_name 存用户名快照（删号后仍可读）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d0e1f2a3b4c5"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_name", sa.String(length=64), nullable=False, comment="操作者用户名快照"),
        sa.Column("action", sa.String(length=64), nullable=False, comment="动作键"),
        sa.Column("target_type", sa.String(length=32), nullable=False, comment="对象类型"),
        sa.Column("target_id", sa.String(length=64), nullable=True, comment="对象 ID"),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="操作摘要（不含敏感值）",
        ),
        sa.Column("ip", sa.String(length=64), nullable=True, comment="客户端 IP"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_log_action_created", "audit_log", ["action", "created_at"], unique=False
    )
    op.create_index(
        "ix_audit_log_actor_created", "audit_log", ["actor_id", "created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_audit_log_actor_created", table_name="audit_log")
    op.drop_index("ix_audit_log_action_created", table_name="audit_log")
    op.drop_table("audit_log")
