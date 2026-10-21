"""add workspace, workspace member, workspace invite and task annotation

Revision ID: e7a1c4b9f2d6
Revises: d4b1e7f2a8c3
Create Date: 2026-10-21 19:20:00.000000

## 为什么需要这个迁移

路线图把「无协作」列为高校采购的硬门槛：当前只有 `task.user_id` 单用户隔离，
无法回答「一个导师带 5 个学生怎么共用」。本迁移引入：

- `workspace`：团队容器（含 `seat_limit` 席位上限，计费单位）；
- `workspace_member`：成员 = **一个席位**（owner 也占席位）；
- `workspace_invite`：**一次性**邀请码（SMTP 未配齐，不走邮件邀请）；
- `task_annotation`：导师批注（段落指纹锚点）；
- `task.workspace_id`：任务归属双轨（NULL = 个人任务）。

## 存量数据

`task.workspace_id` 可空，**存量任务全部保持 NULL** —— 语义就是「个人任务」，
无需回填，也不会改变任何现有可见性（`user_id` 过滤继续生效）。

## 幂等

每个建表/加列/加索引都先查 `inspector`（PostgreSQL 与 SQLite 都支持），
已存在则跳过。Phase 1 `email_token` 的教训：非幂等迁移会让全新库炸。

## downgrade

按依赖逆序删：先 `task_annotation` 与 `task.workspace_id`，再邀请/成员，最后
`workspace`（否则外键约束会挡住）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7a1c4b9f2d6"
down_revision: str | None = "d4b1e7f2a8c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFAULT_SEAT_LIMIT = 5


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _has_table(name: str) -> bool:
    return name in _inspector().get_table_names()


def _has_column(table: str, column: str) -> bool:
    if not _has_table(table):
        return False
    return column in {col["name"] for col in _inspector().get_columns(table)}


def _has_index(table: str, index_name: str) -> bool:
    if not _has_table(table):
        return False
    return index_name in {idx["name"] for idx in _inspector().get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()

    # ---- workspace ----
    if not _has_table("workspace"):
        op.create_table(
            "workspace",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("slug", sa.String(length=64), nullable=False),
            sa.Column("owner_id", sa.Uuid(), nullable=False),
            sa.Column(
                "seat_limit",
                sa.Integer(),
                nullable=False,
                server_default=str(_DEFAULT_SEAT_LIMIT),
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.ForeignKeyConstraint(["owner_id"], ["user.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("slug", name="uq_workspace_slug"),
        )
    if not _has_index("workspace", "ix_workspace_slug"):
        op.create_index("ix_workspace_slug", "workspace", ["slug"])
    if not _has_index("workspace", "ix_workspace_owner_id"):
        op.create_index("ix_workspace_owner_id", "workspace", ["owner_id"])

    # ---- workspace_member ----
    # ⚠️ `created_at` / `updated_at` 由 `Base` 统一提供（`app/db/base.py`），
    # 迁移必须一并建出来。这正是 `email_token` 迁移（`e3b8c4d9a2f1`）踩过的坑：
    # ORM 有列、迁移没建 → 只有真实 PostgreSQL 走 Alembic 时才炸
    # （SQLite 测试用 `create_all` 不经迁移，所以本地全绿）。
    if not _has_table("workspace_member"):
        op.create_table(
            "workspace_member",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("workspace_id", sa.Uuid(), nullable=False),
            sa.Column("user_id", sa.Uuid(), nullable=False),
            sa.Column("role", sa.String(length=16), nullable=False, server_default="member"),
            sa.Column(
                "joined_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "workspace_id",
                "user_id",
                name="uq_workspace_member_workspace_user",
            ),
        )
    if not _has_index("workspace_member", "ix_workspace_member_workspace_id"):
        op.create_index("ix_workspace_member_workspace_id", "workspace_member", ["workspace_id"])
    if not _has_index("workspace_member", "ix_workspace_member_user_id"):
        op.create_index("ix_workspace_member_user_id", "workspace_member", ["user_id"])

    # ---- workspace_invite ----
    if not _has_table("workspace_invite"):
        op.create_table(
            "workspace_invite",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("workspace_id", sa.Uuid(), nullable=False),
            sa.Column("code", sa.String(length=32), nullable=False),
            sa.Column("role", sa.String(length=16), nullable=False, server_default="member"),
            sa.Column("created_by", sa.Uuid(), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("used_by", sa.Uuid(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["created_by"], ["user.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["used_by"], ["user.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("code", name="uq_workspace_invite_code"),
        )
    if not _has_index("workspace_invite", "ix_workspace_invite_code"):
        op.create_index("ix_workspace_invite_code", "workspace_invite", ["code"])
    if not _has_index("workspace_invite", "ix_workspace_invite_workspace_id"):
        op.create_index("ix_workspace_invite_workspace_id", "workspace_invite", ["workspace_id"])

    # ---- task.workspace_id（双轨归属）----
    if not _has_column("task", "workspace_id"):
        # SQLite 不支持 ADD CONSTRAINT，用 batch 模式兼容（PostgreSQL 下等价普通 ALTER）
        with op.batch_alter_table("task") as batch:
            batch.add_column(sa.Column("workspace_id", sa.Uuid(), nullable=True))
            batch.create_foreign_key(
                "fk_task_workspace_id_workspace",
                "workspace",
                ["workspace_id"],
                ["id"],
                ondelete="SET NULL",
            )
    if not _has_index("task", "ix_task_workspace_id"):
        op.create_index("ix_task_workspace_id", "task", ["workspace_id"])

    # ---- task_annotation ----
    if not _has_table("task_annotation"):
        op.create_table(
            "task_annotation",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("task_id", sa.Uuid(), nullable=False),
            sa.Column("author_id", sa.Uuid(), nullable=False),
            sa.Column("anchor_type", sa.String(length=16), nullable=False, server_default="task"),
            sa.Column("anchor_key", sa.String(length=256), nullable=True),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.ForeignKeyConstraint(["task_id"], ["task.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["author_id"], ["user.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
    if not _has_index("task_annotation", "ix_task_annotation_task_id"):
        op.create_index("ix_task_annotation_task_id", "task_annotation", ["task_id"])
    if not _has_index("task_annotation", "ix_task_annotation_author_id"):
        op.create_index("ix_task_annotation_author_id", "task_annotation", ["author_id"])

    # 静默未使用变量告警（bind 仅用于触发连接，便于调试时断点）
    del bind


def downgrade() -> None:
    # 逆序：先批注与 task 列，再邀请/成员，最后 workspace（外键依赖）
    if _has_index("task_annotation", "ix_task_annotation_author_id"):
        op.drop_index("ix_task_annotation_author_id", table_name="task_annotation")
    if _has_index("task_annotation", "ix_task_annotation_task_id"):
        op.drop_index("ix_task_annotation_task_id", table_name="task_annotation")
    if _has_table("task_annotation"):
        op.drop_table("task_annotation")

    if _has_index("task", "ix_task_workspace_id"):
        op.drop_index("ix_task_workspace_id", table_name="task")
    if _has_column("task", "workspace_id"):
        with op.batch_alter_table("task") as batch:
            batch.drop_constraint("fk_task_workspace_id_workspace", type_="foreignkey")
            batch.drop_column("workspace_id")

    if _has_index("workspace_invite", "ix_workspace_invite_workspace_id"):
        op.drop_index("ix_workspace_invite_workspace_id", table_name="workspace_invite")
    if _has_index("workspace_invite", "ix_workspace_invite_code"):
        op.drop_index("ix_workspace_invite_code", table_name="workspace_invite")
    if _has_table("workspace_invite"):
        op.drop_table("workspace_invite")

    if _has_index("workspace_member", "ix_workspace_member_user_id"):
        op.drop_index("ix_workspace_member_user_id", table_name="workspace_member")
    if _has_index("workspace_member", "ix_workspace_member_workspace_id"):
        op.drop_index("ix_workspace_member_workspace_id", table_name="workspace_member")
    if _has_table("workspace_member"):
        op.drop_table("workspace_member")

    if _has_index("workspace", "ix_workspace_owner_id"):
        op.drop_index("ix_workspace_owner_id", table_name="workspace")
    if _has_index("workspace", "ix_workspace_slug"):
        op.drop_index("ix_workspace_slug", table_name="workspace")
    if _has_table("workspace"):
        op.drop_table("workspace")
