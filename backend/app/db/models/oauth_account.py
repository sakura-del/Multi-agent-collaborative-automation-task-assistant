"""OAUTH_ACCOUNT 表：第三方 OAuth 账号绑定（Google / GitHub）。"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OAuthAccount(Base):
    """第三方 OAuth 账号映射：支持一个本地账号绑定多个 OAuth 提供方。"""

    __tablename__ = "oauth_account"

    __table_args__ = (
        UniqueConstraint("provider", "provider_user_id", name="uq_oauth_account_provider_uid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, default=uuid.uuid4, comment="绑定记录ID"
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="关联本地用户ID",
    )
    provider: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="提供方：google / github"
    )
    provider_user_id: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="第三方用户唯一标识"
    )
    provider_email: Mapped[str | None] = mapped_column(
        String(128), nullable=True, comment="第三方返回的已验证邮箱"
    )
