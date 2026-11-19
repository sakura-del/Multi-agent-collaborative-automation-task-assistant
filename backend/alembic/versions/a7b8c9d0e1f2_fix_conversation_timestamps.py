"""fix conversation/message timestamp defaults

Revision ID: a7b8c9d0e1f2
Revises: f5b6c7d8e9a0
Create Date: 2026-11-19 12:30:00.000000

## 为什么需要这个迁移

`f5b6c7d8e9a0` 建表时把 `created_at/updated_at` 写成了「可空、无默认值」，
而 `Base` 的约定是 `server_default=now(), nullable=False`——结果是：
ORM 插入后 `created_at` 为 NULL，`ConversationOut` 校验直接 500。

生产实测（2026-11-19 12:10）：`POST /conversations` 500，
`ValidationError: created_at Input should be a valid datetime [input_value=None]`。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: str | None = "f5b6c7d8e9a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("conversation", "message")


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        # 已有行（若有）先补时间，再加 NOT NULL 默认
        bind.execute(sa.text(f"UPDATE {table} SET created_at = now() WHERE created_at IS NULL"))
        bind.execute(sa.text(f"UPDATE {table} SET updated_at = now() WHERE updated_at IS NULL"))
        op.alter_column(
            table,
            "created_at",
            server_default=sa.func.now(),
            nullable=False,
            existing_type=sa.DateTime(timezone=True),
        )
        op.alter_column(
            table,
            "updated_at",
            server_default=sa.func.now(),
            nullable=False,
            existing_type=sa.DateTime(timezone=True),
        )


def downgrade() -> None:
    for table in _TABLES:
        op.alter_column(
            table,
            "created_at",
            server_default=None,
            nullable=True,
            existing_type=sa.DateTime(timezone=True),
        )
        op.alter_column(
            table,
            "updated_at",
            server_default=None,
            nullable=True,
            existing_type=sa.DateTime(timezone=True),
        )
