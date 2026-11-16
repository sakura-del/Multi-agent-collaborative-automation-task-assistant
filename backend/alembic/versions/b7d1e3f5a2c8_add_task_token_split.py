"""add prompt/completion token split to task

Revision ID: b7d1e3f5a2c8
Revises: a4c8d2f6b9e1
Create Date: 2026-11-16 13:40:00.000000

## 为什么需要拆分

按模型单价估算成本时，输入与输出单价不同（输出通常贵 2~4 倍）。
只存 `used_tokens` 总量只能按一个混合价估算，模型一多误差就不可接受。

## 历史数据

不回填：事件表在生产并不存在（2026-11-16 实测），无法还原历史拆分。
两列默认 0，成本估算对这类任务按可配置的输出占比估算，并显式标记
`split_estimated=true`，不把估算值伪装成实测值。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7d1e3f5a2c8"
down_revision: str | None = "a4c8d2f6b9e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return False
    return column in {item["name"] for item in inspector.get_columns(table)}


def upgrade() -> None:
    for column, comment in (
        ("prompt_tokens", "输入 token（估算成本用）"),
        ("completion_tokens", "输出 token（估算成本用）"),
    ):
        if _has_column("task", column):
            continue
        with op.batch_alter_table("task") as batch:
            batch.add_column(
                sa.Column(
                    column,
                    sa.Integer(),
                    nullable=False,
                    server_default="0",
                    comment=comment,
                )
            )


def downgrade() -> None:
    for column in ("completion_tokens", "prompt_tokens"):
        if _has_column("task", column):
            with op.batch_alter_table("task") as batch:
                batch.drop_column(column)
