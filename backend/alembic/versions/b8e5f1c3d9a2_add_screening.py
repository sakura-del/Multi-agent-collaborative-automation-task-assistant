"""add screening workbench tables

Revision ID: b8e5f1c3d9a2
Revises: a7d4e2f9c1b8
Create Date: 2026-10-20 17:00:00.000000

Phase 2：PRISMA 筛选工作台（检索日志 / 候选记录 / 追加式决策）。

要点：
- `search_log.hits_by_source` 保存**去重前**的各源命中数（PRISMA「识别」依据）；
- `screening_decision` 有 CheckConstraint：`verdict='exclude'` 时必须有排除理由；
- 三张表都显式建 `created_at`/`updated_at`（`Base` 提供这两列，漏建会在
  真实 PostgreSQL 上炸——Phase 1 `email_token` 的生产 500 教训）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8e5f1c3d9a2"
down_revision: str | None = "a7d4e2f9c1b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "search_log",
        sa.Column("id", sa.Uuid(), nullable=False, comment="检索日志ID"),
        sa.Column("task_id", sa.Uuid(), nullable=False, comment="所属任务"),
        sa.Column("query", sa.Text(), nullable=False, comment="检索式（原样保存）"),
        sa.Column("sources", sa.JSON(), nullable=False, comment="本次检索的源列表"),
        sa.Column("hits_total", sa.Integer(), nullable=False, comment="各源命中数之和"),
        sa.Column(
            "hits_by_source", sa.JSON(), nullable=False, comment="各源原始命中数（去重前）"
        ),
        sa.Column("hits_deduped", sa.Integer(), nullable=False, comment="去重后记录数"),
        sa.Column(
            "executed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="检索执行时间（UTC）",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            comment="创建时间",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            comment="更新时间",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["task.id"], name="fk_search_log_task_id", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_search_log"),
        comment="检索可复现日志",
    )
    op.create_index("ix_search_log_task_id", "search_log", ["task_id"], unique=False)

    op.create_table(
        "screening_record",
        sa.Column("id", sa.Uuid(), nullable=False, comment="记录ID"),
        sa.Column("search_log_id", sa.Uuid(), nullable=False, comment="来源检索"),
        sa.Column("task_id", sa.Uuid(), nullable=False, comment="所属任务"),
        sa.Column("source", sa.String(length=32), nullable=False, comment="来源"),
        sa.Column("source_id", sa.String(length=64), nullable=False, comment="来源内 ID"),
        sa.Column("title", sa.Text(), nullable=False, comment="标题"),
        sa.Column("authors", sa.JSON(), nullable=False, comment="作者"),
        sa.Column("abstract", sa.Text(), nullable=False, comment="摘要"),
        sa.Column("doi", sa.String(length=256), nullable=True, comment="归一化 DOI"),
        sa.Column(
            "fulltext_status", sa.String(length=16), nullable=False, comment="全文可得性"
        ),
        sa.Column("current_stage", sa.String(length=16), nullable=False, comment="当前阶段"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            comment="创建时间",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            comment="更新时间",
        ),
        sa.ForeignKeyConstraint(
            ["search_log_id"],
            ["search_log.id"],
            name="fk_screening_record_search_log_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["task.id"],
            name="fk_screening_record_task_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_screening_record"),
        sa.UniqueConstraint(
            "search_log_id", "source", "source_id", name="uq_screening_record_log_source_id"
        ),
        comment="候选文献（检索结果快照）",
    )
    op.create_index(
        "ix_screening_record_search_log_id",
        "screening_record",
        ["search_log_id"],
        unique=False,
    )
    op.create_index("ix_screening_record_task_id", "screening_record", ["task_id"], unique=False)
    op.create_index(
        "ix_screening_record_task_stage",
        "screening_record",
        ["task_id", "current_stage"],
        unique=False,
    )

    op.create_table(
        "screening_decision",
        sa.Column("id", sa.Uuid(), nullable=False, comment="决策ID"),
        sa.Column("record_id", sa.Uuid(), nullable=False, comment="候选记录"),
        sa.Column("stage", sa.String(length=16), nullable=False, comment="筛选阶段"),
        sa.Column("verdict", sa.String(length=16), nullable=False, comment="结论"),
        sa.Column("reviewer_id", sa.Uuid(), nullable=False, comment="筛选者"),
        sa.Column(
            "exclusion_reason", sa.String(length=256), nullable=True, comment="排除理由"
        ),
        sa.Column("note", sa.Text(), nullable=False, comment="备注"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            comment="创建时间",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
            comment="更新时间",
        ),
        sa.CheckConstraint(
            "verdict != 'exclude' OR (exclusion_reason IS NOT NULL AND exclusion_reason != '')",
            name="ck_screening_decision_exclude_requires_reason",
        ),
        sa.ForeignKeyConstraint(
            ["record_id"],
            ["screening_record.id"],
            name="fk_screening_decision_record_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_id"],
            ["user.id"],
            name="fk_screening_decision_reviewer_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_screening_decision"),
        comment="筛选决策（追加式）",
    )
    op.create_index(
        "ix_screening_decision_record_id", "screening_decision", ["record_id"], unique=False
    )
    op.create_index(
        "ix_screening_decision_reviewer_id", "screening_decision", ["reviewer_id"], unique=False
    )
    op.create_index(
        "ix_screening_decision_record_stage",
        "screening_decision",
        ["record_id", "stage"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_screening_decision_record_stage", table_name="screening_decision")
    op.drop_index("ix_screening_decision_reviewer_id", table_name="screening_decision")
    op.drop_index("ix_screening_decision_record_id", table_name="screening_decision")
    op.drop_table("screening_decision")
    op.drop_index("ix_screening_record_task_stage", table_name="screening_record")
    op.drop_index("ix_screening_record_task_id", table_name="screening_record")
    op.drop_index("ix_screening_record_search_log_id", table_name="screening_record")
    op.drop_table("screening_record")
    op.drop_index("ix_search_log_task_id", table_name="search_log")
    op.drop_table("search_log")
