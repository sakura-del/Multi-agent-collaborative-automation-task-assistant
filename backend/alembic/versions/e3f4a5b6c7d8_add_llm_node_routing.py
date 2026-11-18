"""add llm_node_routing (user-level mixed routing)

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-11-18 16:40:00.000000

## 为什么需要

混合路由（Issue #22 延伸）最初只在平台 `.env`，用户看不到也改不了。
「哪个节点用哪把模型」本质是每个用户的取舍，必须做成用户级配置：

- 每个 `(user_id, node_type)` 一条：该节点固定用某把凭据；
- 未配置的节点跟随任务勾选的模型（默认行为不变）；
- 凭据删除级联清理，不留悬空路由。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e3f4a5b6c7d8"
down_revision: str | None = "d2e3f4a5b6c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return name in inspector.get_table_names()


def upgrade() -> None:
    if _has_table("llm_node_routing"):
        return
    op.create_table(
        "llm_node_routing",
        sa.Column("id", sa.Uuid(), primary_key=True, comment="路由ID"),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
            comment="归属用户ID",
        ),
        sa.Column(
            "node_type",
            sa.String(32),
            nullable=False,
            comment="节点类型：planner/search/extract/write/validate/review",
        ),
        sa.Column(
            "credential_id",
            sa.Uuid(),
            sa.ForeignKey("llm_credential.id", ondelete="CASCADE"),
            nullable=False,
            comment="该节点固定使用的凭据",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "node_type", name="uq_llm_node_routing_user_node"),
        comment="节点级模型路由（用户级混合路由）",
    )
    op.create_index("ix_llm_node_routing_user_id", "llm_node_routing", ["user_id"])


def downgrade() -> None:
    if _has_table("llm_node_routing"):
        op.drop_table("llm_node_routing")
