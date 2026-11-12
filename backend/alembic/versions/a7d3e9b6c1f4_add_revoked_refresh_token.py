"""add revoked_refresh_token table

Issue #11 M-1：refresh 令牌轮换后旧令牌必须立即失效。

- 按 `jti` 精确吊销（不按用户），避免一次刷新把同一个人其它设备踢下线；
- 落库而非放 Redis：Redis 重启/清空会丢掉黑名单，而令牌还有 7 天寿命；
- `expires_at` 取令牌自身 `exp`，`purge_expired()` 在每次轮换时顺手清理，表不会膨胀；
- `user_id` 级联删除：用户注销时黑名单一并清除。

Revision ID: a7d3e9b6c1f4
Revises: c4f2a8d7e9b1
Create Date: 2026-11-12 10:30:00.000000
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7d3e9b6c1f4"
down_revision: str | None = "c4f2a8d7e9b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "revoked_refresh_token"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("jti", sa.String(length=64), nullable=False, comment="令牌唯一标识"),
        sa.Column("user_id", sa.Uuid(), nullable=False, comment="令牌所属用户"),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="令牌自身过期时间（过期后本行可清理）",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            comment="创建时间",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            comment="更新时间",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], name="fk_revoked_refresh_token_user_id", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("jti"),
    )
    op.create_index("ix_revoked_refresh_token_user_id", _TABLE, ["user_id"])
    op.create_index("ix_revoked_refresh_token_expires_at", _TABLE, ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_revoked_refresh_token_expires_at", table_name=_TABLE)
    op.drop_index("ix_revoked_refresh_token_user_id", table_name=_TABLE)
    op.drop_table(_TABLE)
