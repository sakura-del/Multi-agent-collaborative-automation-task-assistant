"""add seq column to screening_decision

Revision ID: c9f6a2d4e1b7
Revises: b8e5f1c3d9a2
Create Date: 2026-10-20 18:00:00.000000

## 为什么需要这个迁移

`b8e5f1c3d9a2`（Task 1）最初建表时没有 `seq` 列。Task 2 的 TDD 发现一个真实缺陷：

**不能用 `created_at` 排序决策**——同一事务内多行的 `server_default` 时间精度不足会
并列，`ORDER BY created_at DESC` 取到的是任意一条。这对「最新决策派生」与
「双人分歧判定」是致命的（会误报或漏报冲突）。

修法：加 `seq`（Integer，决策先后顺序的稳定排序依据），由 Repository 在追加时按
「该记录当前最大 seq + 1」赋值。

`b8e5f1c3d9a2` 已直接修正（全新部署一次到位）；本迁移用于**已执行过旧版
`b8e5f1c3d9a2` 的库**（如服务器）。**必须幂等**——列已存在时跳过，
否则全新库会重复加列（Phase 1 的教训）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9f6a2d4e1b7"
down_revision: str | None = "b8e5f1c3d9a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """补 `seq`；**幂等**——列已存在则跳过（全新库已由修正后的上一版建好）。"""
    inspector = sa.inspect(op.get_bind())
    existing = {column["name"] for column in inspector.get_columns("screening_decision")}
    if "seq" in existing:
        return

    # 先加可空列，按既有行的决策时间顺序回填，再收紧为 NOT NULL。
    # （当前生产表为 0 行，但迁移必须对「有历史数据」的库同样正确。）
    op.add_column(
        "screening_decision",
        sa.Column(
            "seq",
            sa.Integer(),
            nullable=True,
            comment="决策先后顺序（稳定排序依据；由应用层按最大值+1 赋值）",
        ),
    )
    op.execute(
        sa.text(
            """
            UPDATE screening_decision AS d
            SET seq = sub.row_number
            FROM (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY record_id ORDER BY created_at, id
                       ) AS row_number
                FROM screening_decision
            ) AS sub
            WHERE d.id = sub.id
            """
        )
    )
    # ⚠️ 必须用 batch_alter_table：SQLite 不支持 `ALTER COLUMN ... SET NOT NULL`，
    # batch 模式会走「建新表 + 拷数据」的等价路径（PostgreSQL 上则透传 ALTER）。
    with op.batch_alter_table("screening_decision") as batch:
        batch.alter_column("seq", existing_type=sa.Integer(), nullable=False)


def downgrade() -> None:
    """移除 `seq`（幂等：列不存在则跳过）。"""
    inspector = sa.inspect(op.get_bind())
    existing = {column["name"] for column in inspector.get_columns("screening_decision")}
    if "seq" not in existing:
        return
    with op.batch_alter_table("screening_decision") as batch:
        batch.drop_column("seq")
