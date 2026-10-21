"""add quota settlement columns to task

Revision ID: d4b1e7f2a8c3
Revises: c9f6a2d4e1b7
Create Date: 2026-10-21 12:40:00.000000

## 为什么需要这个迁移

提交任务时按预估 100k **预扣**配额，任务**完成**时按实际用量退还差额。
但退款只在完成路径调用 → 取消 / 失败 / 转人工 / 放弃确认的任务，
预扣的 100k **永久占用**。

真实故障（2026-10-21 Task 7 验收）：demo 账号 6 个停在 `waiting_approval` 的探针任务
占满 100 万日配额，之后所有建任务请求返回 429 `QUOTA_EXCEEDED`。

## 两列

- `used_tokens`：实际已消耗 token（配额结算依据）。真实用量原本只存在于内存
  `ExecutionResult`，进程重启 / 任务中途取消后就丢了，无法统计——所以必须落库。
- `quota_settled`：结算幂等标记。结算会调 Redis `refund()`，重复调用等于
  「越退越多」——额度能被刷成正收益，必须防重。

## 存量数据

`status='completed'` 的任务在完成时已经按实际用量校正过（`refund` 已调用），
迁移时把它们置 `quota_settled=true`，避免上线后被任何重放路径再结算一次。

## 幂等

列已存在则跳过（Phase 1 `email_token` 的教训：非幂等迁移会让全新库炸）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4b1e7f2a8c3"
down_revision: str | None = "c9f6a2d4e1b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """补 `used_tokens` / `quota_settled`；幂等——列已存在则按需补齐另一列。"""
    inspector = sa.inspect(op.get_bind())
    existing = {column["name"] for column in inspector.get_columns("task")}

    if "used_tokens" not in existing:
        op.add_column(
            "task",
            sa.Column(
                "used_tokens",
                sa.Integer(),
                nullable=False,
                server_default="0",
                comment="实际已消耗 token（配额结算依据）",
            ),
        )

    if "quota_settled" not in existing:
        op.add_column(
            "task",
            sa.Column(
                "quota_settled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
                comment="配额是否已结算（幂等保护）",
            ),
        )
        # 已完成任务的预扣已在完成时校正过（refund 已调用）→ 标记为已结算，
        # 避免上线后被重放路径再退一次。
        op.execute(
            sa.text(
                """
                UPDATE task
                   SET quota_settled = TRUE
                 WHERE status = 'completed'
                """
            )
        )


def downgrade() -> None:
    """移除两列（幂等：列不存在则跳过）。"""
    inspector = sa.inspect(op.get_bind())
    existing = {column["name"] for column in inspector.get_columns("task")}
    with op.batch_alter_table("task") as batch:
        if "quota_settled" in existing:
            batch.drop_column("quota_settled")
        if "used_tokens" in existing:
            batch.drop_column("used_tokens")
