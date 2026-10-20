"""add missing updated_at to email_token

Revision ID: f5c2a8d1b3e7
Revises: e3b8c4d9a2f1
Create Date: 2026-10-20 10:10:00.000000

修复 `e3b8c4d9a2f1` 漏建 `email_token.updated_at` 的缺陷。

`EmailToken` 继承 `Base`，ORM 元数据含 `created_at` **与** `updated_at` 两列，
但上一版迁移只建了 `created_at`。SQLite 单测因走 `Base.metadata.create_all`
（不经迁移）发现不了，**只有真实 PostgreSQL 走 Alembic 时才炸**：

    sqlalchemy.exc.ProgrammingError: column "updated_at" of relation "email_token" does not exist
    [SQL: UPDATE email_token SET used_at=..., updated_at=now() WHERE ...]

后果：生产上注册 / 重发验证邮件 / 密码重置申请全部 500。

回归测试见 `tests/test_migration_schema_consistency.py`（跑迁移后与 ORM 列比对）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f5c2a8d1b3e7"
down_revision: str | None = "e3b8c4d9a2f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 历史行需要非空默认值：用 server_default 回填，与 Base 的自动时间戳语义一致
    op.add_column(
        "email_token",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            comment="更新时间",
        ),
    )


def downgrade() -> None:
    with op.batch_alter_table("email_token") as batch:
        batch.drop_column("updated_at")
