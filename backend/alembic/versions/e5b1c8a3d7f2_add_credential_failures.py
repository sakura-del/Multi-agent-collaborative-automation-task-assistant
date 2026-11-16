"""add consecutive_failures to llm_credential

Revision ID: e5b1c8a3d7f2
Revises: d9e2f4a7c1b3
Create Date: 2026-11-13 23:20:00.000000

## 为什么需要

Issue #15 P1：连续失败要能自动停用凭据，避免用户反复撞同一个 401。
计数落在凭据上（而不是日志里），才能做到「第 3 次失败就置 invalid」。

默认 0 + `server_default`：存量行无需回填，语义也正确（从未失败过）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5b1c8a3d7f2"
down_revision: str | None = "d9e2f4a7c1b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return False
    return column in {item["name"] for item in inspector.get_columns(table)}


def upgrade() -> None:
    if not _has_column("llm_credential", "consecutive_failures"):
        op.add_column(
            "llm_credential",
            sa.Column(
                "consecutive_failures",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        )


def downgrade() -> None:
    if _has_column("llm_credential", "consecutive_failures"):
        op.drop_column("llm_credential", "consecutive_failures")
