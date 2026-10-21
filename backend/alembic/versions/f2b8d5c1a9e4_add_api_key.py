"""add api_key table

Revision ID: f2b8d5c1a9e4
Revises: e7a1c4b9f2d6
Create Date: 2026-10-21 20:35:00.000000

## 为什么需要这个迁移

开放 API 与 MCP 需要可归属调用方的凭证。没有 API Key 就没有「按调用方计量」
的基础，计费只能按用户粗算，也无法做「只读集成」这类最小权限授予。

## 表设计要点

- **只存 `key_hash`（sha256），绝不存明文**：数据库泄漏时明文 Key 直接可用；
- `prefix` 存明文前几位（如 `maa_7Kx9mQ2p`），让用户在列表里认出是哪个 Key，
  又不足以被暴力补齐；
- `scopes` 用 JSON 数组（4 个固定 scope 的子集）；
- `revoked_at` / `expires_at` **软删除**：审计需要回答「这个 Key 什么时候被
  谁吊销的」。

## 幂等

建表/建索引前先查 `inspector`（PostgreSQL 与 SQLite 都支持）。
Phase 1 `email_token` 的教训：非幂等迁移会让全新库炸。

## 时间戳

`created_at` / `updated_at` 由 `Base` 统一提供（`app/db/base.py`），
迁移必须一并建出来 —— 这正是 `email_token` 缺陷的成因。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2b8d5c1a9e4"
down_revision: str | None = "e7a1c4b9f2d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _has_table(name: str) -> bool:
    return name in _inspector().get_table_names()


def _has_index(table: str, index_name: str) -> bool:
    if not _has_table(table):
        return False
    return index_name in {idx["name"] for idx in _inspector().get_indexes(table)}


def upgrade() -> None:
    if not _has_table("api_key"):
        op.create_table(
            "api_key",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("user_id", sa.Uuid(), nullable=False),
            sa.Column("name", sa.String(length=64), nullable=False),
            sa.Column("prefix", sa.String(length=16), nullable=False),
            sa.Column("key_hash", sa.String(length=128), nullable=False),
            sa.Column("scopes", sa.JSON(), nullable=False),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
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
            sa.UniqueConstraint("key_hash", name="uq_api_key_key_hash"),
        )
    if not _has_index("api_key", "ix_api_key_user_id"):
        op.create_index("ix_api_key_user_id", "api_key", ["user_id"])
    if not _has_index("api_key", "ix_api_key_prefix"):
        op.create_index("ix_api_key_prefix", "api_key", ["prefix"])


def downgrade() -> None:
    if _has_index("api_key", "ix_api_key_prefix"):
        op.drop_index("ix_api_key_prefix", table_name="api_key")
    if _has_index("api_key", "ix_api_key_user_id"):
        op.drop_index("ix_api_key_user_id", table_name="api_key")
    if _has_table("api_key"):
        op.drop_table("api_key")
