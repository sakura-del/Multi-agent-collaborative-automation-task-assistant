"""EMAIL_TOKEN 表：邮箱验证 / 密码重置的一次性令牌（Phase 1 P1-C）。

安全约定：
- **只存哈希**（``sha256(token)`` 的十六进制，64 字符），明文 token 只在发信链路出现一次；
- **单次使用**：``used_at`` 非空即视为已消费，二次使用一律拒绝；
- **有有效期**：``expires_at``（默认 24h，见 ``EmailConfig``），过期一律拒绝；
- ``user_id`` 级联删除：用户注销时令牌一并清除。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EmailTokenPurpose(StrEnum):
    """令牌用途：用途不符的令牌不得跨端点使用。"""

    VERIFY = "verify"
    """邮箱验证。"""

    RESET = "reset"
    """密码重置。"""


class EmailToken(Base):
    """邮箱/密码生命周期一次性令牌。"""

    __tablename__ = "email_token"

    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_email_token_token_hash"),
        # 失效旧令牌 / 查询用户未消费令牌（owner + 用途）
        Index("ix_email_token_user_purpose", "user_id", "purpose"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, comment="令牌ID")
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="用户ID",
    )
    purpose: Mapped[str] = mapped_column(String(16), nullable=False, comment="用途：verify / reset")
    token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="令牌哈希（sha256 hex，不存明文）"
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="过期时间"
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="使用时间（非空=已消费）"
    )

    # Base 已提供 created_at / updated_at，此处不重复声明
