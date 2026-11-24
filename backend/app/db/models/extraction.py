"""自定义论文抽取表模型（Issue #36）。

两张表：
- extraction_table：表头定义（用户、可选关联任务、列配置、数据源清单、运行状态）
- extraction_cell：单元格结果（行源 × 列键唯一），保存抽取结果、校验后原文引用与定位
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class ExtractionTable(Base):
    """自定义论文数据抽取表。"""

    __tablename__ = "extraction_table"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), index=True, nullable=False
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("task.id", ondelete="SET NULL"), index=True, nullable=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    # 列定义：[{"key": "sample_size", "label": "样本量", "prompt": "..."}]
    columns: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    # 行源清单：[{"source_type": "knowledge_doc", "source_id": "...", "title": "..."}]
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    # 状态：draft / running / completed / failed
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft", index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    cells: Mapped[list[ExtractionCell]] = relationship(
        back_populates="table", cascade="all, delete-orphan"
    )


class ExtractionCell(Base):
    """抽取表单个单元格（论文 × 列）。"""

    __tablename__ = "extraction_cell"
    __table_args__ = (
        UniqueConstraint(
            "table_id", "source_type", "source_id", "column_key", name="uq_extraction_cell_coord"
        ),
        Index("ix_extraction_cell_table_source", "table_id", "source_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    table_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("extraction_table.id", ondelete="CASCADE"), index=True, nullable=False
    )
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    column_key: Mapped[str] = mapped_column(String(64), nullable=False)

    # 状态：pending / extracted / not_reported / error
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 原文证据（经空白归一化子串校验，未通过则置 None）
    quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    passage: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 兼容 Issue #35 EvidenceLocator：{page_start, page_end, section_title, table_ref}
    locator: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # full_text / abstract_only
    evidence_scope: Mapped[str] = mapped_column(String(24), nullable=False, default="full_text")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    table: Mapped[ExtractionTable] = relationship(back_populates="cells")
