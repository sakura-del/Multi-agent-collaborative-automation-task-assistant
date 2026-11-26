"""免费版额度上调至 3 篇 + 漏斗埋点与站内反馈表（Issue #61）。

Revision ID: 4f8a2d1c9e7b
Revises: 9e2f5b8c3a1d
Create Date: 2026-11-26 12:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "4f8a2d1c9e7b"
down_revision = "9e2f5b8c3a1d"
branch_labels = None
depends_on = None

_JSON_TYPE = sa.JSON().with_variant(JSONB(), "postgresql")

_NEW_FREE_DESCRIPTION = (
    "每月 3 篇综述（平台模型单次最多 15 篇文献），含 GB/T 7714—2025 引用与 Markdown/DOCX 导出"
)
_OLD_FREE_DESCRIPTION = "每月 1 篇综述，含 GB/T 7714—2025 引用与 Markdown/DOCX 导出"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if inspector.has_table("plan"):
        op.execute(
            sa.text(
                "UPDATE plan SET monthly_reviews = 3, description = :desc WHERE id = 'free'"
            ).bindparams(desc=_NEW_FREE_DESCRIPTION)
        )

    if not inspector.has_table("analytics_event"):
        op.create_table(
            "analytics_event",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("event_name", sa.String(length=64), nullable=False),
            sa.Column("visitor_id", sa.String(length=64), nullable=False),
            sa.Column("user_id", sa.Uuid(), nullable=True),
            sa.Column("path", sa.String(length=255), nullable=True),
            sa.Column("meta", _JSON_TYPE, nullable=True),
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
            sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_analytics_event_user_id", "analytics_event", ["user_id"])
        op.create_index(
            "ix_analytics_event_name_created",
            "analytics_event",
            ["event_name", "created_at"],
        )
        op.create_index(
            "ix_analytics_event_visitor_created",
            "analytics_event",
            ["visitor_id", "created_at"],
        )

    if not inspector.has_table("user_feedback"):
        op.create_table(
            "user_feedback",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("user_id", sa.Uuid(), nullable=False),
            sa.Column("category", sa.String(length=32), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("page_path", sa.String(length=255), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False),
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
        )
        op.create_index("ix_user_feedback_user_id", "user_feedback", ["user_id"])
        op.create_index("ix_user_feedback_created", "user_feedback", ["created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if inspector.has_table("user_feedback"):
        op.drop_index("ix_user_feedback_created", table_name="user_feedback")
        op.drop_index("ix_user_feedback_user_id", table_name="user_feedback")
        op.drop_table("user_feedback")

    if inspector.has_table("analytics_event"):
        op.drop_index("ix_analytics_event_visitor_created", table_name="analytics_event")
        op.drop_index("ix_analytics_event_name_created", table_name="analytics_event")
        op.drop_index("ix_analytics_event_user_id", table_name="analytics_event")
        op.drop_table("analytics_event")

    if inspector.has_table("plan"):
        op.execute(
            sa.text(
                "UPDATE plan SET monthly_reviews = 1, description = :desc WHERE id = 'free'"
            ).bindparams(desc=_OLD_FREE_DESCRIPTION)
        )
