"""User Repository：用户表专属查询。

阶段1任务5：在 BaseRepository 基础上扩展 User 专属查询。
"""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user import User
from app.db.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    """用户数据访问层。"""

    model = User

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(db)

    async def get_by_username(self, username: str) -> User | None:
        """按用户名查询，用于登录校验与注册查重。"""
        stmt = select(User).where(User.username == username)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> User | None:
        """按邮箱查询，用于注册查重与邮箱登录。"""
        stmt = select(User).where(User.email == email)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_account(self, account: str) -> User | None:
        """按用户名或邮箱查询，支持两种登录方式。

        利用 username / email 上的索引，单次查询即可命中。
        """
        stmt = select(User).where(or_(User.username == account, User.email == account))
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def exists(self, username: str, email: str) -> User | None:
        """注册查重：用户名或邮箱已存在时返回已存在的用户，否则 None。"""
        stmt = select(User).where(or_(User.username == username, User.email == email))
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()
