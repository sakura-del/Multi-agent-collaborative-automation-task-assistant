"""add conversation / message (session kernel P0)

Revision ID: f5b6c7d8e9a0
Revises: e3f4a5b6c7d8
Create Date: 2026-11-19 12:00:00.000000

## 循环外键的处理

`conversation.leaf_id → message.id` 与 `message.conversation_id → conversation.id`
互相引用：先建两张表（leaf_id 暂不带外键），再 `ALTER TABLE` 补约束。
不这样做，建表顺序没有解。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f5b6c7d8e9a0"
down_revision: str | None = "e3f4a5b6c7d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return name in inspector.get_table_names()


def upgrade() -> None:
    if not _has_table("conversation"):
        op.create_table(
            "conversation",
            sa.Column("id", sa.Uuid(), primary_key=True, comment="会话ID"),
            sa.Column(
                "user_id",
                sa.Uuid(),
                sa.ForeignKey("user.id", ondelete="CASCADE"),
                nullable=False,
                comment="归属用户ID",
            ),
            sa.Column(
                "title", sa.String(120), nullable=False, server_default="新会话", comment="标题"
            ),
            sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("leaf_id", sa.Uuid(), nullable=True, comment="当前分支末端消息ID"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            comment="多轮会话（会话式 Agent 界面内核）",
        )
        op.create_index("ix_conversation_user_id", "conversation", ["user_id"])
        op.create_index("ix_conversation_user_updated", "conversation", ["user_id", "updated_at"])

    if not _has_table("message"):
        op.create_table(
            "message",
            sa.Column("id", sa.Uuid(), primary_key=True, comment="消息ID"),
            sa.Column(
                "conversation_id",
                sa.Uuid(),
                sa.ForeignKey("conversation.id", ondelete="CASCADE"),
                nullable=False,
                comment="所属会话ID",
            ),
            sa.Column(
                "parent_id",
                sa.Uuid(),
                sa.ForeignKey("message.id", ondelete="CASCADE"),
                nullable=True,
                comment="父消息ID（树结构）",
            ),
            sa.Column("role", sa.String(16), nullable=False, comment="user / assistant / system"),
            sa.Column("content", sa.Text(), nullable=False, server_default=""),
            sa.Column(
                "status",
                sa.String(16),
                nullable=False,
                server_default="completed",
                comment="completed / pending / streaming / interrupted / failed",
            ),
            sa.Column("model", sa.String(160), nullable=True),
            sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error", sa.String(512), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            comment="会话消息（树节点）",
        )
        op.create_index(
            "ix_message_conversation_created", "message", ["conversation_id", "created_at"]
        )
        op.create_index("ix_message_parent", "message", ["parent_id"])

    # 循环外键：会话 → 当前分支末端
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_fks = {fk["name"] for fk in inspector.get_foreign_keys("conversation")}
    if "fk_conversation_leaf" not in existing_fks:
        op.create_foreign_key(
            "fk_conversation_leaf",
            "conversation",
            "message",
            ["leaf_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    if _has_table("conversation"):
        bind = op.get_bind()
        inspector = sa.inspect(bind)
        existing_fks = {fk["name"] for fk in inspector.get_foreign_keys("conversation")}
        if "fk_conversation_leaf" in existing_fks:
            op.drop_constraint("fk_conversation_leaf", "conversation", type_="foreignkey")
    if _has_table("message"):
        op.drop_table("message")
    if _has_table("conversation"):
        op.drop_table("conversation")
