"""REVOKED_REFRESH_TOKEN 表：被轮换掉的 refresh 令牌黑名单（Issue #11 M-1）。

## 为什么需要这张表

refresh 令牌本来是"换发新令牌"的长期凭据。2026-11-11 安全排查实测：换发之后
**旧令牌仍然可用**（同一个 refresh 连换两次都返回 200）。这意味着令牌一旦泄露，
合法用户做什么都踢不掉它——`token_invalid_before` 只在改密码/强制下线时才变。

## 设计取舍

- **按 `jti` 精确吊销**，不是按用户：按用户吊销（每次轮换都改 `token_invalid_before`）
  会把同一个人其它设备一起踢下线，多端登录直接不可用；
- **落库而不是放 Redis**：Redis 重启/清空会把这批黑名单一起丢掉，而令牌还有 7 天寿命，
  等于吊销失效。这张表跟着数据库一起备份，行为可预期；
- **`expires_at` 取令牌自身的 `exp`**：令牌自然过期后这一行就没有意义了，
  `purge_expired()` 在每次轮换时顺手清掉，表不会无限增长；
- `user_id` 级联删除：用户注销时黑名单一并清除。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RevokedRefreshToken(Base):
    """已吊销的 refresh 令牌（只存 jti，不存令牌本身）。"""

    __tablename__ = "revoked_refresh_token"

    __table_args__ = (
        # 清理扫描按过期时间走，避免全表扫
        Index("ix_revoked_refresh_token_expires_at", "expires_at"),
    )

    jti: Mapped[str] = mapped_column(String(64), primary_key=True, comment="令牌唯一标识")
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="令牌所属用户",
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="令牌自身过期时间（过期后本行可清理）",
    )

    # Base 已提供 created_at / updated_at，此处不重复声明
