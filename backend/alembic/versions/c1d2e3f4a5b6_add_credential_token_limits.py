"""add context_window / max_output_tokens to llm_credential

Revision ID: c1d2e3f4a5b6
Revises: b7d1e3f5a2c8
Create Date: 2026-11-17 16:45:00.000000

## 为什么需要

BYOK 凭据此前只记录了「厂商 + 模型名」，容量信息完全缺失：

- 界面无法回答「这个模型能吃多长上下文、能吐多长」；
- 运行时也从不传 `max_tokens`，实际输出上限由厂商默认值决定，用户不可知
  也不可控（实测接入 1M 上下文 / 128K 输出的模型后，两边都是一笔糊涂账）。

两列都允许 NULL，语义区分清楚：

- `context_window`：仅用于展示与校验，不参与请求；
- `max_output_tokens`：非空时作为 `default_max_tokens` 真正下发给厂商。

存量凭据不回填：我们并不知道用户模型真实容量，编一个默认值等于给假信息。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1d2e3f4a5b6"
down_revision: str | None = "b7d1e3f5a2c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return False
    return column in {item["name"] for item in inspector.get_columns(table)}


def upgrade() -> None:
    for column, comment in (
        ("context_window", "模型上下文窗口（token）"),
        ("max_output_tokens", "单次输出上限（token）"),
    ):
        if _has_column("llm_credential", column):
            continue
        op.add_column(
            "llm_credential",
            sa.Column(column, sa.Integer(), nullable=True, comment=comment),
        )


def downgrade() -> None:
    for column in ("max_output_tokens", "context_window"):
        if _has_column("llm_credential", column):
            op.drop_column("llm_credential", column)
