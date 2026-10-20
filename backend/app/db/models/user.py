"""USER 表：用户与鉴权。

阶段0任务3：JWT 鉴权基础。
- 密码哈希存储（passlib bcrypt），不存明文
- 角色字段区分普通用户/管理员
- 关联 TOKEN 失效时间戳支持强制下线
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    """用户表：承载注册/登录/角色/密码哈希。"""

    __tablename__ = "user"

    # UUID 主键，分散自增压力，便于未来分库
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, comment="用户ID")

    # 登录凭据
    username: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False, comment="登录用户名"
    )
    email: Mapped[str] = mapped_column(
        String(128), unique=True, index=True, nullable=False, comment="邮箱"
    )
    # passlib bcrypt 哈希后约 60 字符，留余量
    password_hash: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="密码哈希（bcrypt）"
    )

    # 角色：admin / user，默认 user
    role: Mapped[str] = mapped_column(
        String(16), default="user", nullable=False, comment="角色：admin/user"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, comment="账号是否启用"
    )

    # 强制下线：所有早于此时间签发的 token 视为失效
    token_invalid_before: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="token 失效时间戳"
    )

    # 邮箱验证（Phase 1 P1-C）：NULL = 未验证。历史用户保持 NULL，不阻塞登录。
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="邮箱验证时间"
    )

    # Base 已提供 created_at / updated_at，此处不重复声明
