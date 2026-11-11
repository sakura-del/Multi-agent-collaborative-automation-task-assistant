"""add knowledge_vectors_bge shadow table

为真实 BGE 嵌入后台重建提供独立向量表，原 Mock 表持续在线。

- 表结构与 knowledge_vectors 一致，向量维度仍为 1024；
- 索引名使用 ix_kvbge_*，避免 PostgreSQL schema 内索引重名导致影子表没有索引；
- 无 pgvector 的本地/CI PostgreSQL 与既有迁移一样 no-op；
- downgrade 只删除影子表，不删除扩展或原表。

Revision ID: c4f2a8d7e9b1
Revises: d5a8c1e3f7b2
Create Date: 2026-11-11 12:30:00.000000
"""

from __future__ import annotations

import logging
from typing import Sequence

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger("alembic.knowledge_vectors_bge")

revision: str = "c4f2a8d7e9b1"
down_revision: str | None = "d5a8c1e3f7b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

VECTOR_DIMENSION = 1024
_TABLE = "knowledge_vectors_bge"

_EXTENSION_SCHEMA_SQL = """
SELECT n.nspname
FROM pg_extension e
JOIN pg_namespace n ON n.oid = e.extnamespace
WHERE e.extname = 'vector'
"""


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _extension_schema() -> str | None:
    bind = op.get_bind()
    return bind.execute(sa.text(_EXTENSION_SCHEMA_SQL)).scalar()


def _extension_installable() -> bool:
    bind = op.get_bind()
    return (
        bind.execute(
            sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")
        ).scalar()
        is not None
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    schema = _extension_schema()
    if schema is None and _extension_installable():
        bind.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
        schema = _extension_schema()
    if schema is None:
        logger.warning("pgvector 扩展不可用，跳过 %s 影子表创建", _TABLE)
        return

    vector = f"{_quote(schema)}.{_quote('vector')}"
    opclass = f"{_quote(schema)}.{_quote('vector_cosine_ops')}"

    bind.execute(
        sa.text(
            f"""
            CREATE TABLE IF NOT EXISTS {_TABLE} (
                collection   text        NOT NULL,
                id           text        NOT NULL,
                embedding    {vector}({VECTOR_DIMENSION}) NOT NULL,
                document     text        NOT NULL DEFAULT '',
                doc_id       text        NOT NULL DEFAULT '',
                user_id      text        NOT NULL DEFAULT '',
                doc_source   text        NOT NULL DEFAULT '',
                chunk_index  integer     NOT NULL DEFAULT 0,
                start_offset integer     NOT NULL DEFAULT 0,
                end_offset   integer     NOT NULL DEFAULT 0,
                metadata     jsonb       NOT NULL DEFAULT '{{}}'::jsonb,
                created_at   timestamptz NOT NULL DEFAULT now(),
                updated_at   timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY (collection, id)
            )
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            CREATE INDEX IF NOT EXISTS ix_kvbge_embedding_hnsw
                ON {_TABLE} USING hnsw (embedding {opclass})
                WITH (m = 16, ef_construction = 64)
            """
        )
    )
    bind.execute(
        sa.text(
            f"CREATE INDEX IF NOT EXISTS ix_kvbge_collection_doc ON {_TABLE} (collection, doc_id)"
        )
    )
    bind.execute(
        sa.text(
            f"CREATE INDEX IF NOT EXISTS ix_kvbge_collection_user ON {_TABLE} (collection, user_id)"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    bind.execute(sa.text(f"DROP TABLE IF EXISTS {_TABLE}"))
