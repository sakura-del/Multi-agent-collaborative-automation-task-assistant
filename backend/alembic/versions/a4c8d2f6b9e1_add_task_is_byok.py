"""add is_byok to task

Revision ID: a4c8d2f6b9e1
Revises: e5b1c8a3d7f2
Create Date: 2026-11-16 12:00:00.000000

## 为什么加一个冗余布尔列

用量分账要按「平台模型 / 用户自带模型」分组统计。历史任务里，
凭据可能已被删除（外键 `SET NULL`），现场用 `llm_credential_id IS NOT NULL`
推导会把这些任务算成平台模型。

`is_byok` 在**任务创建时写死**，之后凭据增删都不影响历史口径。

## 回填规则（两步）

1. `llm_credential_id IS NOT NULL` → 一定是 BYOK；
2. 凭据已删但 `model_snapshot.is_byok` 为真的 → 也是 BYOK。

第 2 步用 Python 解析 JSON，避免不同数据库的 JSON 运算符差异
（SQLite 的 `json_extract` 与 PostgreSQL 的 `->>` 写法不通用）。
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4c8d2f6b9e1"
down_revision: str | None = "e5b1c8a3d7f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return False
    return column in {item["name"] for item in inspector.get_columns(table)}


def upgrade() -> None:
    if not _has_column("task", "is_byok"):
        with op.batch_alter_table("task") as batch:
            batch.add_column(
                sa.Column(
                    "is_byok",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )

    connection = op.get_bind()
    # ① 仍在用凭据的任务
    connection.execute(sa.text("UPDATE task SET is_byok = TRUE WHERE llm_credential_id IS NOT NULL"))

    # ② 凭据已删、但快照标记了 BYOK
    rows = connection.execute(
        sa.text(
            "SELECT id, model_snapshot FROM task "
            "WHERE llm_credential_id IS NULL AND model_snapshot IS NOT NULL"
        )
    ).fetchall()
    for row in rows:
        snapshot = row[1]
        if isinstance(snapshot, str):
            try:
                snapshot = json.loads(snapshot)
            except ValueError:
                continue
        if isinstance(snapshot, dict) and snapshot.get("is_byok"):
            connection.execute(
                sa.text("UPDATE task SET is_byok = TRUE WHERE id = :task_id"),
                {"task_id": row[0]},
            )


def downgrade() -> None:
    if _has_column("task", "is_byok"):
        with op.batch_alter_table("task") as batch:
            batch.drop_column("is_byok")
