"""BYOK 收尾：可选回退平台模型 + 生成能力探测门禁（Issue #28）

Revision ID: b4e7f6a9c2d5
Revises: f3c9d2a7b1e8
Create Date: 2026-11-20

- `llm_credential.allow_platform_fallback`：凭据级开关（默认 false，#15 的显式契约
  「不静默回退」不变，只是给用户一个可选项）；
- `task.actual_provider` / `task.actual_model`：回退发生时如实标注「这次实际用的
  是平台什么模型」——BYOK 任务显示的是创建时快照，用户会误以为还是自己的 key 在花钱；
- `task.fallback_token_base`：回退时刻的 `used_tokens` 基线。结算退款必须只退
  **平台实际消耗的部分**（ESTIMATED − (总用量 − 基线)），否则 BYOK 阶段花的
  自己的钱会被算进平台退款，凭空多出额度。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b4e7f6a9c2d5"
down_revision = "f3c9d2a7b1e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "llm_credential",
        sa.Column(
            "allow_platform_fallback",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="凭据不可用（401/402/403）时是否允许回退平台模型（默认否）",
        ),
    )
    op.add_column(
        "task",
        sa.Column(
            "actual_provider",
            sa.String(length=64),
            nullable=True,
            comment="实际使用的模型厂商（回退平台后与快照不同）",
        ),
    )
    op.add_column(
        "task",
        sa.Column(
            "actual_model",
            sa.String(length=128),
            nullable=True,
            comment="实际使用的模型名（回退平台后与快照不同）",
        ),
    )
    op.add_column(
        "task",
        sa.Column(
            "fallback_token_base",
            sa.Integer(),
            nullable=True,
            comment="回退时刻的已用 token 基线（结算只退平台实际消耗部分）",
        ),
    )


def downgrade() -> None:
    op.drop_column("task", "fallback_token_base")
    op.drop_column("task", "actual_model")
    op.drop_column("task", "actual_provider")
    op.drop_column("llm_credential", "allow_platform_fallback")
