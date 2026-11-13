"""add llm_credential table

Revision ID: c3d7e9f1a4b2
Revises: b8e4f2a6c9d1
Create Date: 2026-11-13 18:00:00.000000

## 为什么需要这个迁移

Issue #15（BYOK）：用户要把自己的模型 API Key 接进来跑任务。没有这张表，
凭据只能塞进环境变量，全站共用一套 key，无法归属、无法按用户选择模型。

## 表设计要点

- **必须可逆加密**：运行时要把明文发给厂商，所以存 AES-256-GCM 密文，
  而不是 `api_key` 表那样的哈希。密文列一旦出现明文，测试会阻止合并；
- `api_key_hint` 只存脱敏串（`sk-…abcd`），列表里用于辨认；
- `key_fingerprint` 是 HMAC 摘要，用于去重与审计，**不可逆**；
- `key_version` 在换 key 时 +1，运行时按它做缓存失效；
- `is_default` 的部分唯一索引：应用层先清空再设置，索引兜底并发。

## 幂等

与 `f2b8d5c1a9e4`（api_key）同样的写法：建表/建索引前先查 inspector，
避免全新库或重跑迁移时炸。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d7e9f1a4b2"
down_revision: str | None = "b8e4f2a6c9d1"
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
    if not _has_table("llm_credential"):
        op.create_table(
            "llm_credential",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("user_id", sa.Uuid(), nullable=False),
            sa.Column("name", sa.String(length=64), nullable=False),
            sa.Column("provider", sa.String(length=32), nullable=False),
            sa.Column("base_url", sa.String(length=512), nullable=False),
            sa.Column("model", sa.String(length=160), nullable=False),
            sa.Column("api_key_ciphertext", sa.Text(), nullable=False),
            sa.Column("api_key_hint", sa.String(length=24), nullable=False),
            sa.Column("key_fingerprint", sa.String(length=64), nullable=False),
            sa.Column("key_version", sa.Integer(), nullable=False),
            sa.Column("capabilities", sa.JSON(), nullable=False),
            sa.Column("is_default", sa.Boolean(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_error", sa.String(length=512), nullable=True),
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
            sa.UniqueConstraint("user_id", "name", name="uq_llm_credential_user_name"),
        )
    if not _has_index("llm_credential", "ix_llm_credential_user_id"):
        op.create_index("ix_llm_credential_user_id", "llm_credential", ["user_id"])
    if not _has_index("llm_credential", "uq_llm_credential_user_default"):
        # 部分唯一索引：每用户至多一条 is_default
        op.create_index(
            "uq_llm_credential_user_default",
            "llm_credential",
            ["user_id"],
            unique=True,
            postgresql_where=sa.text("is_default"),
            sqlite_where=sa.text("is_default"),
        )


def downgrade() -> None:
    if _has_index("llm_credential", "uq_llm_credential_user_default"):
        op.drop_index("uq_llm_credential_user_default", table_name="llm_credential")
    if _has_index("llm_credential", "ix_llm_credential_user_id"):
        op.drop_index("ix_llm_credential_user_id", table_name="llm_credential")
    if _has_table("llm_credential"):
        op.drop_table("llm_credential")
