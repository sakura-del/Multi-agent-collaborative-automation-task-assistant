"""add billing tables and seed plans

Revision ID: a3c9e6d2b8f1
Revises: f2b8d5c1a9e4
Create Date: 2026-10-22 09:20:00.000000

## 为什么需要这个迁移

Phase 2 最后一项：计费套餐。核心是解决「按日 token 上限」与「按月篇数」
**双轨打架**的问题 —— 明确篇数为主（准入）、token 为辅（成本护栏）。

三张表：

- `plan`：套餐定义（**表而非代码常量**：改价不发版、历史订阅可回溯）；
- `subscription`：用户订阅（同一用户只能一条 `active`）；
- `usage_counter`：月度篇数（**计费依据**，必须持久可审计）。

## 部分唯一索引（PostgreSQL 专属）

```sql
CREATE UNIQUE INDEX uq_subscription_active_per_user
    ON subscription (user_id) WHERE status = 'active';
```

保证「一个用户同时只有一条 active 订阅」。SQLite 不支持部分索引，
迁移里用 `dialect` 判断跳过（服务层 + 测试保证）。

## 种子数据

四个套餐（free / student / pro / institution）对齐路线图 §7 建议档位。
**幂等**：已存在则更新价格与描述（允许调价），不重复插入。

## 时间戳

`created_at` / `updated_at` 由 `Base` 统一提供，迁移必须一并建出来
（`email_token` / `workspace` / `api_key` 都踩过这个坑）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3c9e6d2b8f1"
down_revision: str | None = "f2b8d5c1a9e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: 种子套餐（与 `app.db.models.billing.SEED_PLANS` 保持一致）。
#: 迁移里内联一份而不是 import 应用代码：迁移必须能在**任意历史版本**的代码上跑，
#: import 应用常量会让「未来改了 SEED_PLANS」导致老迁移行为漂移。
_SEED_PLANS: list[dict[str, object]] = [
    {
        "id": "free",
        "name": "免费版",
        "monthly_reviews": 1,
        "max_workspaces": 0,
        "max_seats_per_workspace": 0,
        "allow_api_access": False,
        "allow_team": False,
        "price_cents": 0,
        "description": "每月 1 篇综述，含 GB/T 7714—2025 引用与 Markdown/DOCX 导出",
    },
    {
        "id": "student",
        "name": "学生版",
        "monthly_reviews": 10,
        "max_workspaces": 1,
        "max_seats_per_workspace": 3,
        "allow_api_access": False,
        "allow_team": True,
        "price_cents": 1900,
        "description": "每月 10 篇，支持团队协作与导师批注（3 席位）",
    },
    {
        "id": "pro",
        "name": "专业版",
        "monthly_reviews": 50,
        "max_workspaces": 5,
        "max_seats_per_workspace": 10,
        "allow_api_access": True,
        "allow_team": True,
        "price_cents": 6900,
        "description": "每月 50 篇，开放 API 与 MCP 接入（10 席位）",
    },
    {
        "id": "institution",
        "name": "机构版",
        "monthly_reviews": -1,
        "max_workspaces": 100,
        "max_seats_per_workspace": 200,
        "allow_api_access": True,
        "allow_team": True,
        "price_cents": 199900,
        "description": "篇数不限，按席位计价，支持统一身份与审计",
    },
]


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _has_table(name: str) -> bool:
    return name in _inspector().get_table_names()


def _has_index(table: str, index_name: str) -> bool:
    if not _has_table(table):
        return False
    return index_name in {idx["name"] for idx in _inspector().get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # ---- plan ----
    if not _has_table("plan"):
        op.create_table(
            "plan",
            sa.Column("id", sa.String(length=32), nullable=False),
            sa.Column("name", sa.String(length=64), nullable=False),
            sa.Column("monthly_reviews", sa.Integer(), nullable=False),
            sa.Column("max_workspaces", sa.Integer(), nullable=False, server_default="0"),
            sa.Column(
                "max_seats_per_workspace", sa.Integer(), nullable=False, server_default="0"
            ),
            sa.Column(
                "allow_api_access", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
            sa.Column("allow_team", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("price_cents", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
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
            sa.PrimaryKeyConstraint("id"),
        )

    # ---- subscription ----
    if not _has_table("subscription"):
        op.create_table(
            "subscription",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("user_id", sa.Uuid(), nullable=False),
            sa.Column("plan_id", sa.String(length=32), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
            sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
            sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
            sa.Column("seats_purchased", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("source", sa.String(length=16), nullable=False, server_default="manual"),
            sa.Column("note", sa.Text(), nullable=True),
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
            sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["plan_id"], ["plan.id"], ondelete="RESTRICT"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("id", name="uq_subscription_id"),
        )
    if not _has_index("subscription", "ix_subscription_user_id"):
        op.create_index("ix_subscription_user_id", "subscription", ["user_id"])
    if not _has_index("subscription", "ix_subscription_plan_id"):
        op.create_index("ix_subscription_plan_id", "subscription", ["plan_id"])
    # 部分唯一索引：一个用户同时只能有一条 active 订阅。
    # SQLite 不支持部分索引，跳过（服务层 + 测试保证）。
    if dialect == "postgresql" and not _has_index(
        "subscription", "uq_subscription_active_per_user"
    ):
        op.execute(
            "CREATE UNIQUE INDEX uq_subscription_active_per_user "
            "ON subscription (user_id) WHERE status = 'active'"
        )

    # ---- usage_counter ----
    if not _has_table("usage_counter"):
        op.create_table(
            "usage_counter",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("user_id", sa.Uuid(), nullable=False),
            sa.Column("period_key", sa.String(length=8), nullable=False),
            sa.Column("reviews_used", sa.Integer(), nullable=False, server_default="0"),
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
            sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "period_key", name="uq_usage_counter_user_period"),
        )
    if not _has_index("usage_counter", "ix_usage_counter_user_id"):
        op.create_index("ix_usage_counter_user_id", "usage_counter", ["user_id"])
    if not _has_index("usage_counter", "ix_usage_counter_period_key"):
        op.create_index("ix_usage_counter_period_key", "usage_counter", ["period_key"])

    # ---- 种子套餐（幂等：已存在则更新，允许调价）----
    plan_table = sa.table(
        "plan",
        sa.column("id", sa.String),
        sa.column("name", sa.String),
        sa.column("monthly_reviews", sa.Integer),
        sa.column("max_workspaces", sa.Integer),
        sa.column("max_seats_per_workspace", sa.Integer),
        sa.column("allow_api_access", sa.Boolean),
        sa.column("allow_team", sa.Boolean),
        sa.column("price_cents", sa.Integer),
        sa.column("description", sa.Text),
        sa.column("is_active", sa.Boolean),
    )
    for plan in _SEED_PLANS:
        exists = bind.execute(
            sa.text("SELECT 1 FROM plan WHERE id = :pid"), {"pid": plan["id"]}
        ).first()
        if exists:
            # UPDATE 语句引用 `:is_active`，参数里必须显式给出（种子数据不含该键）
            params = dict(plan, is_active=True)
            bind.execute(
                sa.text(
                    "UPDATE plan SET name = :name, monthly_reviews = :monthly_reviews, "
                    "max_workspaces = :max_workspaces, "
                    "max_seats_per_workspace = :max_seats_per_workspace, "
                    "allow_api_access = :allow_api_access, allow_team = :allow_team, "
                    "price_cents = :price_cents, description = :description, "
                    "is_active = :is_active WHERE id = :id"
                ),
                params,
            )
        else:
            op.bulk_insert(plan_table, [dict(plan, is_active=True)])


def downgrade() -> None:
    if _has_index("usage_counter", "ix_usage_counter_period_key"):
        op.drop_index("ix_usage_counter_period_key", table_name="usage_counter")
    if _has_index("usage_counter", "ix_usage_counter_user_id"):
        op.drop_index("ix_usage_counter_user_id", table_name="usage_counter")
    if _has_table("usage_counter"):
        op.drop_table("usage_counter")

    if _has_index("subscription", "uq_subscription_active_per_user"):
        op.drop_index("uq_subscription_active_per_user", table_name="subscription")
    if _has_index("subscription", "ix_subscription_plan_id"):
        op.drop_index("ix_subscription_plan_id", table_name="subscription")
    if _has_index("subscription", "ix_subscription_user_id"):
        op.drop_index("ix_subscription_user_id", table_name="subscription")
    if _has_table("subscription"):
        op.drop_table("subscription")

    if _has_table("plan"):
        op.drop_table("plan")
