"""message.tool_calls / pending_tool：会话工具调用（P3）

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-11-19

为什么把工具轨迹存在消息上：它是这一轮回答的「过程」，与内容同生命周期；
独立表会让每次打开会话多一次 join，且没有跨消息查询需求。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "message",
        sa.Column(
            "tool_calls",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
            comment="工具调用轨迹（JSON）",
        ),
    )
    op.add_column(
        "message",
        sa.Column(
            "pending_tool",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="待确认的写工具（JSON）",
        ),
    )


def downgrade() -> None:
    op.drop_column("message", "pending_tool")
    op.drop_column("message", "tool_calls")
