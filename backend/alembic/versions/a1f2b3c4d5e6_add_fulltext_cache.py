"""新增 fulltext_cache 全局 OA 正文缓存表（Issue #73 Phase A）。

Revision ID: a1f2b3c4d5e6
Revises: d8b2f5c3a1e9
Create Date: 2026-11-27 16:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect

from alembic import op

revision = "a1f2b3c4d5e6"
down_revision = "d8b2f5c3a1e9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if inspector.has_table("fulltext_cache"):
        return

    op.create_table(
        "fulltext_cache",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("doi", sa.String(length=256), nullable=True),
        sa.Column("arxiv_id", sa.String(length=64), nullable=True),
        sa.Column("pmid", sa.String(length=32), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("source_url", sa.String(length=1024), nullable=False),
        sa.Column("license", sa.String(length=64), nullable=False),
        sa.Column("content_format", sa.String(length=16), nullable=False),
        sa.Column("file_path", sa.String(length=512), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("parsed_sections", sa.JSON(), nullable=False),
        sa.Column("paper_card", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_fulltext_cache_doi", "fulltext_cache", ["doi"], unique=True)
    op.create_index("ix_fulltext_cache_arxiv_id", "fulltext_cache", ["arxiv_id"], unique=True)
    op.create_index("ix_fulltext_cache_pmid", "fulltext_cache", ["pmid"], unique=True)
    op.create_index("ix_fulltext_cache_sha256", "fulltext_cache", ["sha256"], unique=True)
    op.create_index("ix_fulltext_cache_status", "fulltext_cache", ["status"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if not inspector.has_table("fulltext_cache"):
        return
    op.drop_table("fulltext_cache")
