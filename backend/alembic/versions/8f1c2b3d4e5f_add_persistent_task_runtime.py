"""add persistent task runtime

Revision ID: 8f1c2b3d4e5f
Revises: 2f31f6cdd7b2
Create Date: 2026-10-17 20:05:00.000000

Plan 2 Task 1：在既有 13 张表上补充持久化任务运行时字段，不新增表。

- TASK：task_type / config / plan / plan_version / paused_from_status /
  review_report / artifacts
- SUBTASK：唯一约束 (task_id, node_id)
- EXECUTION_LOG：event_type / event_seq / attempt，唯一约束 (task_id, event_seq)

event_seq 对历史数据按 task_id 分组、logged_at 顺序回填后再收紧为非空。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8f1c2b3d4e5f"
down_revision: str | None = "2f31f6cdd7b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TASK_RUNTIME_COLUMNS = (
    "task_type",
    "config",
    "plan",
    "plan_version",
    "paused_from_status",
    "review_report",
    "artifacts",
)

LOG_RUNTIME_COLUMNS = ("event_type", "event_seq", "attempt")


def upgrade() -> None:
    # ===== TASK：运行时字段（历史行通过 server_default 回填） =====
    op.add_column(
        "task",
        sa.Column(
            "task_type",
            sa.String(length=32),
            nullable=False,
            server_default="literature_review",
            comment="任务类型",
        ),
    )
    op.add_column(
        "task",
        sa.Column(
            "config",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
            comment="任务配置",
        ),
    )
    op.add_column(
        "task",
        sa.Column("plan", sa.JSON(), nullable=True, comment="结构化规划"),
    )
    op.add_column(
        "task",
        sa.Column(
            "plan_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="规划版本",
        ),
    )
    op.add_column(
        "task",
        sa.Column("paused_from_status", sa.String(length=16), nullable=True, comment="暂停前状态"),
    )
    op.add_column(
        "task",
        sa.Column("review_report", sa.JSON(), nullable=True, comment="复盘报告"),
    )
    op.add_column(
        "task",
        sa.Column(
            "artifacts",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
            comment="产出物清单",
        ),
    )

    # ===== EXECUTION_LOG：事件流字段 =====
    op.add_column(
        "execution_log",
        sa.Column(
            "event_type",
            sa.String(length=32),
            nullable=False,
            server_default="log",
            comment="事件类型",
        ),
    )
    op.add_column(
        "execution_log",
        sa.Column(
            "attempt",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="第几次尝试",
        ),
    )
    # 先以可空列加入，回填历史行后再收紧
    op.add_column(
        "execution_log",
        sa.Column("event_seq", sa.Integer(), nullable=True, comment="任务内递增事件序号"),
    )
    op.execute(
        """
        UPDATE execution_log AS log
        SET event_seq = ranked.event_seq
        FROM (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY task_id ORDER BY logged_at, id
                   ) AS event_seq
            FROM execution_log
        ) AS ranked
        WHERE log.id = ranked.id
        """
    )

    with op.batch_alter_table("execution_log") as batch_op:
        batch_op.alter_column("event_seq", existing_type=sa.Integer(), nullable=False)
        batch_op.create_unique_constraint(
            "uq_execution_log_task_event_seq", ["task_id", "event_seq"]
        )

    with op.batch_alter_table("subtask") as batch_op:
        batch_op.create_unique_constraint("uq_subtask_task_node", ["task_id", "node_id"])


def downgrade() -> None:
    with op.batch_alter_table("subtask") as batch_op:
        batch_op.drop_constraint("uq_subtask_task_node", type_="unique")

    with op.batch_alter_table("execution_log") as batch_op:
        batch_op.drop_constraint("uq_execution_log_task_event_seq", type_="unique")

    for column in reversed(LOG_RUNTIME_COLUMNS):
        op.drop_column("execution_log", column)

    for column in reversed(TASK_RUNTIME_COLUMNS):
        op.drop_column("task", column)
