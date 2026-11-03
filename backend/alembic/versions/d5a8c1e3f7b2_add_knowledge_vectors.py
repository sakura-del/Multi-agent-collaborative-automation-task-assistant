"""add knowledge_vectors table (pgvector)

向量库从 Chroma 迁移到 Supabase PostgreSQL + pgvector 的表结构。

设计要点：
- **不进 ORM / Base.metadata**：`vector` 类型没有通用 SQLAlchemy 映射，
  放进 metadata 会干扰 `create_all` 与 autogenerate；表用原生 DDL 维护。
- `vector` 扩展可能装在 `public`，也可能装在 Supabase 默认的 `extensions` schema，
  所以类型与 opclass 一律**从 `pg_extension` 解析后限定引用**，不依赖 `search_path`。
- 主键 `(collection, id)`：`clear()` 只清当前 collection，
  多知识库命名空间共存不能互相污染。
- 索引：HNSW + `vector_cosine_ops`（与检索用的 `<=>` 余弦距离一致）；
  2.8 万行规模下无需训练、召回优于 IVFFlat。
- downgrade **只删表，不删扩展**：扩展可能是同库其他表在用的。

Revision ID: d5a8c1e3f7b2
Revises: b7d4e9f1c6a2
Create Date: 2026-11-03 10:00:00.000000
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5a8c1e3f7b2"
down_revision: str | None = "b7d4e9f1c6a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 与默认嵌入模型 `BAAI/bge-large-zh` 对齐；换模型需要新迁移 + 重建向量
VECTOR_DIMENSION = 1024

_TABLE = "knowledge_vectors"

_EXTENSION_SCHEMA_SQL = """
SELECT n.nspname
FROM pg_extension e
JOIN pg_namespace n ON n.oid = e.extnamespace
WHERE e.extname = 'vector'
"""


def _quote(identifier: str) -> str:
    """标识符加双引号（schema 来自 catalog，仍按最小惊讶原则转义）。"""
    return '"' + identifier.replace('"', '""') + '"'


def _extension_schema() -> str | None:
    bind = op.get_bind()
    return bind.execute(sa.text(_EXTENSION_SCHEMA_SQL)).scalar()


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # 测试环境是 SQLite：向量表只存在于 PostgreSQL（pgvector）
        return

    schema = _extension_schema()
    if schema is None:
        bind.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
        schema = _extension_schema()
    if schema is None:
        raise RuntimeError(
            "pgvector 扩展不可用：请在目标库执行 `CREATE EXTENSION vector`（Supabase 需在 extensions schema）"
        )

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
            CREATE INDEX IF NOT EXISTS ix_kv_embedding_hnsw
                ON {_TABLE} USING hnsw (embedding {opclass})
                WITH (m = 16, ef_construction = 64)
            """
        )
    )
    bind.execute(
        sa.text(
            f"CREATE INDEX IF NOT EXISTS ix_kv_collection_doc ON {_TABLE} (collection, doc_id)"
        )
    )
    bind.execute(
        sa.text(
            f"CREATE INDEX IF NOT EXISTS ix_kv_collection_user ON {_TABLE} (collection, user_id)"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    bind.execute(sa.text(f"DROP TABLE IF EXISTS {_TABLE}"))
