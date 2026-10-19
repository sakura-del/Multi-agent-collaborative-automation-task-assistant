"""引用样式 Repository：owner-scoped 的样式查询（Issue #4 M2）。

核心约定（同 `db/repositories/knowledge.py`）：
- 所有查询必须携带 `user_id` 过滤，跨用户访问与不存在同语义（返回 `None`），
  避免泄漏「资源是否存在」；
- 系统预置样式（`user_id IS NULL`）只能经 `get_preset` 访问，不混入用户列表。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.db.models.citation_style import CitationStyle
from app.db.repositories.base import BaseRepository


class CitationStyleRepository(BaseRepository[CitationStyle]):
    """CitationStyle 的 owner-scoped 数据访问。"""

    model = CitationStyle

    async def list_for_user(self, user_id: UUID) -> list[CitationStyle]:
        """列出某用户的自定义样式（**不含**系统预置），按创建时间倒序。"""
        result = await self.db.execute(
            select(CitationStyle)
            .where(CitationStyle.user_id == user_id)
            .order_by(CitationStyle.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_for_user(self, user_id: UUID, style_id: str) -> CitationStyle | None:
        """查询某用户自己的样式；不属于该用户时返回 `None`（与不存在同语义）。"""
        result = await self.db.execute(
            select(CitationStyle).where(
                CitationStyle.user_id == user_id,
                CitationStyle.style_id == style_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_preset(self, style_id: str) -> CitationStyle | None:
        """查询系统预置样式（`user_id IS NULL`）；不存在返回 `None`。"""
        result = await self.db.execute(
            select(CitationStyle).where(
                CitationStyle.user_id.is_(None),
                CitationStyle.style_id == style_id,
            )
        )
        return result.scalar_one_or_none()

    async def add(self, style: CitationStyle) -> CitationStyle:
        """新增样式。

        仅 `add + flush`（不 commit，事务边界由 service 层统一控制），
        复用 `BaseRepository.create`，不重复实现 CRUD。
        """
        return await self.create(style)
