"""add email lifecycle

Revision ID: e3b8c4d9a2f1
Revises: a4c9e1d7b2f8
Create Date: 2026-10-20 09:30:00.000000

Phase 1 P1-C：账号体系（邮箱验证 / 密码重置）。

- `user.email_verified_at`（nullable）：邮箱验证时间，NULL = 未验证；
- `email_token` 表：一次性令牌，`purpose` 区分 verify / reset，**只存 sha256 哈希**，
  `used_at` 保证单次使用，`expires_at` 控制有效期；`user_id` 级联删除。

双方言说明：
- `add_column` 在 SQLite / PostgreSQL 都是原生 `ALTER TABLE ADD COLUMN`；
- `downgrade()` 删列用 `batch_alter_table`：SQLite 走「重建表」路径（旧版 SQLite 不支持
  `DROP COLUMN`），PostgreSQL 下 batch 是透传的普通 `ALTER TABLE DROP COLUMN`；
- `server_default` 用 `sa.func.now()`（PG 渲染 `now()`、SQLite 渲染 `CURRENT_TIMESTAMP`）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e3b8c4d9a2f1"
down_revision: str | None = "a4c9e1d7b2f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1) 用户表：邮箱验证时间（NULL = 未验证）
    op.add_column(
        "user",
        sa.Column(
            "email_verified_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="邮箱验证时间",
        ),
    )

    # 2) 一次性令牌表（邮箱验证 / 密码重置）
    op.create_table(
        "email_token",
        sa.Column("id", sa.Uuid(), nullable=False, comment="令牌ID"),
        sa.Column("user_id", sa.Uuid(), nullable=False, comment="用户ID"),
        sa.Column(
            "purpose",
            sa.String(length=16),
            nullable=False,
            comment="用途：verify / reset",
        ),
        sa.Column(
            "token_hash",
            sa.String(length=64),
            nullable=False,
            comment="令牌哈希（sha256 hex，不存明文）",
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="过期时间",
        ),
        sa.Column(
            "used_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="使用时间（非空=已消费）",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            comment="创建时间",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name="fk_email_token_user_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_email_token"),
        sa.UniqueConstraint("token_hash", name="uq_email_token_token_hash"),
        comment="邮箱验证 / 密码重置一次性令牌",
    )
    # 用户维度查询（FK + 「该用户某用途」失效旧令牌）
    op.create_index("ix_email_token_user_id", "email_token", ["user_id"], unique=False)
    op.create_index(
        "ix_email_token_user_purpose",
        "email_token",
        ["user_id", "purpose"],
        unique=False,
    )


def downgrade() -> None:
    # 顺序与 upgrade 相反：先删索引与表，再删列，保证可完整回滚
    op.drop_index("ix_email_token_user_purpose", table_name="email_token")
    op.drop_index("ix_email_token_user_id", table_name="email_token")
    op.drop_table("email_token")
    with op.batch_alter_table("user") as batch_op:
        batch_op.drop_column("email_verified_at")
