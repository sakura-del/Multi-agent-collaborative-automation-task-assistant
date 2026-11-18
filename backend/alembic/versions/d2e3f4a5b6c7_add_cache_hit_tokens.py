"""add cache_hit_tokens to task

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-11-18 14:05:00.000000

## 为什么需要

Issue #22 的 prompt 前缀缓存优化需要一个**落库的命中率口径**：

- 日志里已有 `cache_hit_tokens`，但无法做跨任务聚合（成本报表要按模型/时间统计）；
- Prometheus 指标只有进程级计数，重启即丢，且无法与 `task.used_tokens` 对齐；
- 命中价通常只有未命中的 1/10~1/25，只有落库才能回答「优化到底省了多少钱」。

语义：厂商未返回缓存字段（如本地 vLLM）时保持 0，表示「未观测到」，
不是「未命中」。存量任务同样为 0。

## 为什么 BigInteger

单任务在 1M 上下文模型上可能命中数十万 token，多任务累计更大；
且 `used_tokens` 未来若统一改成 BigInteger，此列无需再迁移。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d2e3f4a5b6c7"
down_revision: str | None = "c1d2e3f4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return False
    return column in {item["name"] for item in inspector.get_columns(table)}


def upgrade() -> None:
    if _has_column("task", "cache_hit_tokens"):
        return
    op.add_column(
        "task",
        sa.Column(
            "cache_hit_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
            comment="输入侧前缀缓存命中 token（成本观测用）",
        ),
    )


def downgrade() -> None:
    if _has_column("task", "cache_hit_tokens"):
        op.drop_column("task", "cache_hit_tokens")
