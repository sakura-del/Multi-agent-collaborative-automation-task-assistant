"""task_routine / task_refresh：定时重跑与增量刷新记录（Issue #27）

Revision ID: f3c9d2a7b1e8
Revises: e1f2a3b4c5d6
Create Date: 2026-11-20

两张表的职责与设计取舍见 `app/db/models/routine.py` 的模块注释：
- `task_routine`：每任务一条排期（unique task_id），决定频率与是否邮件汇报；
- `task_refresh`：每次刷新的运行记录（手动/定时），含窗口、计数、摘要、token 用量。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f3c9d2a7b1e8"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_routine",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False, comment="被排期的任务"),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="任务所有者（配额与邮件都发给他）",
        ),
        sa.Column(
            "enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False, comment="是否启用"
        ),
        sa.Column(
            "cadence",
            sa.String(length=16),
            nullable=False,
            comment="重跑频率：weekly / monthly",
        ),
        sa.Column(
            "notify", sa.String(length=16), nullable=False, comment="汇报方式：none / email"
        ),
        sa.Column(
            "next_run_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="下次运行时刻（UTC）",
        ),
        sa.Column(
            "last_run_at", sa.DateTime(timezone=True), nullable=True, comment="上次运行时刻（UTC）"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="创建时间",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="更新时间",
        ),
        sa.ForeignKeyConstraint(["task_id"], ["task.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", name="uq_task_routine_task"),
    )
    op.create_index("ix_task_routine_task_id", "task_routine", ["task_id"])
    op.create_index("ix_task_routine_user_id", "task_routine", ["user_id"])
    op.create_index("ix_task_routine_next_run_at", "task_routine", ["next_run_at"])

    op.create_table(
        "task_refresh",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False, comment="被刷新的任务"),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="发起者（手动=本人，定时=任务所有者）",
        ),
        sa.Column(
            "trigger",
            sa.String(length=16),
            nullable=False,
            comment="触发方式：manual / schedule",
        ),
        sa.Column("status", sa.String(length=16), nullable=False, comment="运行状态"),
        sa.Column("window_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "queries",
            sa.JSON(),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
        sa.Column(
            "hits_total", sa.Integer(), server_default="0", nullable=False, comment="上游命中总数（去重前）"
        ),
        sa.Column(
            "new_count", sa.Integer(), server_default="0", nullable=False, comment="本次新增文献数"
        ),
        sa.Column(
            "duplicate_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
            comment="与前次重复（含跨源重复）的条数",
        ),
        sa.Column(
            "sources",
            sa.JSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("summary_markdown", sa.Text(), nullable=True),
        sa.Column(
            "citation_ok",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
            comment="摘要是否通过引用一致性校验",
        ),
        sa.Column(
            "tokens_used",
            sa.Integer(),
            server_default="0",
            nullable=False,
            comment="本次摘要消耗 token",
        ),
        sa.Column("error", sa.Text(), nullable=True, comment="失败原因"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "notified",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="是否已发汇报邮件",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="创建时间",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="更新时间",
        ),
        sa.ForeignKeyConstraint(["task_id"], ["task.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_task_refresh_task_id", "task_refresh", ["task_id"])
    op.create_index("ix_task_refresh_user_id", "task_refresh", ["user_id"])
    op.create_index("ix_task_refresh_status", "task_refresh", ["status"])


def downgrade() -> None:
    op.drop_index("ix_task_refresh_status", table_name="task_refresh")
    op.drop_index("ix_task_refresh_user_id", table_name="task_refresh")
    op.drop_index("ix_task_refresh_task_id", table_name="task_refresh")
    op.drop_table("task_refresh")
    op.drop_index("ix_task_routine_next_run_at", table_name="task_routine")
    op.drop_index("ix_task_routine_user_id", table_name="task_routine")
    op.drop_index("ix_task_routine_task_id", table_name="task_routine")
    op.drop_table("task_routine")
