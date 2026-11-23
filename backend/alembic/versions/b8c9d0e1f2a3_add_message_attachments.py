"""message.attachments：会话消息附件引用（P2）

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-11-19

为什么是 JSON 列而不是关联表：附件是「消息的一个属性」，读写永远跟着消息走，
没有独立查询需求；关联表会让每次取消息多一次 join。只存引用（document_id +
标题快照），文件真源仍在 knowledge_doc。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "message",
        sa.Column(
            "attachments",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
            comment="附件引用列表（JSON）",
        ),
    )


def downgrade() -> None:
    op.drop_column("message", "attachments")
