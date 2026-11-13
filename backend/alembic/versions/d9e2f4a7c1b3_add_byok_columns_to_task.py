"""add byok columns to task

Revision ID: d9e2f4a7c1b3
Revises: c3d7e9f1a4b2
Create Date: 2026-11-13 19:30:00.000000

## 为什么需要这个迁移

Issue #15（BYOK）P0-3：任务要记住「用哪一把用户凭据」以及创建时的模型快照。

- `llm_credential_id`：运行时按它解析客户端；`ON DELETE SET NULL` 让用户删凭据
  不会连带删任务（历史任务仍要能打开）；
- `model_snapshot`：快照名称/厂商/模型。**不能只靠外键**——凭据被删后
  外键变 NULL，历史任务就答不出「当时用的什么模型」。

## 为什么用 batch_alter_table

SQLite 不支持 `ALTER TABLE ... ADD CONSTRAINT`；batch 模式在 SQLite 上走
「建新表 → 拷数据 → 改名」，在 PostgreSQL 上仍是普通 ALTER。CI 的迁移作业
跑 PostgreSQL，本地迁移冒烟跑 SQLite，两边都要过。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d9e2f4a7c1b3"
down_revision: str | None = "c3d7e9f1a4b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _has_column(table: str, column: str) -> bool:
    inspector = _inspector()
    if table not in inspector.get_table_names():
        return False
    return column in {item["name"] for item in inspector.get_columns(table)}


def _has_index(table: str, index_name: str) -> bool:
    if table not in _inspector().get_table_names():
        return False
    return index_name in {item["name"] for item in _inspector().get_indexes(table)}


def upgrade() -> None:
    if not _has_column("task", "llm_credential_id"):
        with op.batch_alter_table("task") as batch:
            batch.add_column(sa.Column("llm_credential_id", sa.Uuid(), nullable=True))
            batch.create_foreign_key(
                "fk_task_llm_credential_id",
                "llm_credential",
                ["llm_credential_id"],
                ["id"],
                ondelete="SET NULL",
            )
    if not _has_column("task", "model_snapshot"):
        with op.batch_alter_table("task") as batch:
            batch.add_column(sa.Column("model_snapshot", sa.JSON(), nullable=True))
    if not _has_index("task", "ix_task_llm_credential_id"):
        op.create_index("ix_task_llm_credential_id", "task", ["llm_credential_id"])


def downgrade() -> None:
    if _has_index("task", "ix_task_llm_credential_id"):
        op.drop_index("ix_task_llm_credential_id", table_name="task")
    # 先取值再进 batch：SQLite 的 batch 会临时改名表，块内再 inspect 不可靠
    has_snapshot = _has_column("task", "model_snapshot")
    has_credential = _has_column("task", "llm_credential_id")
    if has_snapshot or has_credential:
        with op.batch_alter_table("task") as batch:
            if has_snapshot:
                batch.drop_column("model_snapshot")
            if has_credential:
                batch.drop_column("llm_credential_id")
