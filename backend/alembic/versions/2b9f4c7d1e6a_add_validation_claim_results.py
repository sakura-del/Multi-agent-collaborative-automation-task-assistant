"""validation_result 增加 claim_results 列（Issue #31 Phase 2 论断级核验）。

Revision ID: 2b9f4c7d1e6a
Revises: e8f1a2b3c4d5
Create Date: 2026-11-23 17:55:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2b9f4c7d1e6a"
down_revision = "e8f1a2b3c4d5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "validation_result",
        sa.Column(
            "claim_results",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
            comment="论断级核验明细",
        ),
    )


def downgrade() -> None:
    op.drop_column("validation_result", "claim_results")
