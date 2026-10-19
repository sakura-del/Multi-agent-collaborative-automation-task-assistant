"""CITATION_STYLE 表：用户自定义 CSL 引用样式（Issue #4 M2）。

语义：
- `user_id IS NULL` = 系统预置（全局可见）；非 NULL = 该用户私有的上传样式。
- 预置样式默认不落库（由 `StyleRegistry` 静态提供），DB 只存用户上传的；
  表结构仍保留「NULL 所有者」语义，便于将来把预置样式固化进库、统一走一条读取路径。

约束：
- `UniqueConstraint("user_id", "style_id")`：同一用户内 `style_id` 唯一；
- **部分唯一索引**（`user_id IS NULL` 上 `style_id` 唯一）：普通唯一约束不约束 NULL，
  故预置样式需要单独的 `postgresql_where` / `sqlite_where` 双写的部分唯一索引。
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CitationStyle(Base):
    """引用样式表：样式标识、来源元数据与内容指纹。"""

    __tablename__ = "citation_style"

    __table_args__ = (
        UniqueConstraint("user_id", "style_id", name="uq_citation_style_user_style_id"),
        Index(
            "uq_citation_style_preset_style_id",
            "style_id",
            unique=True,
            postgresql_where=text("user_id IS NULL"),
            sqlite_where=text("user_id IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, comment="样式ID")
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
        comment="所有者ID（NULL=系统预置）",
    )

    style_id: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="样式标识（与 CSL 文件名一致）"
    )
    title: Mapped[str] = mapped_column(String(256), nullable=False, comment="人类可读样式名")
    numbering: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="编号模式：citation-number/none"
    )
    ordering: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="排序模式：citation-order/author-date"
    )
    default_locale: Mapped[str] = mapped_column(
        String(16), default="en-US", nullable=False, comment="默认语言"
    )
    filename: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="磁盘文件名（服务端生成，不信任客户端）"
    )
    content_sha256: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="内容指纹（去重与完整性）"
    )
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, comment="文件大小（字节）")

    # Base 已提供 created_at / updated_at，此处不重复声明
