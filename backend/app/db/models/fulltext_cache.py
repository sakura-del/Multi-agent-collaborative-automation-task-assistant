"""OA 文献全局正文缓存（Issue #73）：仅存合规 OA，按 DOI/arXiv/PMID/sha256 去重。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FulltextCache(Base):
    """全局 OA 正文与 Paper Card 缓存（严禁存付费闭源文献）。"""

    __tablename__ = "fulltext_cache"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    doi: Mapped[str | None] = mapped_column(String(256), unique=True, index=True, nullable=True)
    arxiv_id: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)
    pmid: Mapped[str | None] = mapped_column(String(32), unique=True, index=True, nullable=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    source_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    license: Mapped[str] = mapped_column(String(64), nullable=False)
    content_format: Mapped[str] = mapped_column(String(16), nullable=False)
    file_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parsed_sections: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    paper_card: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
