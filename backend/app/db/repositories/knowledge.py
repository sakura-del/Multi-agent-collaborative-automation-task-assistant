"""知识库 Repository：owner-scoped 的文档与分块查询。

核心约定（见 docs/DEVELOPMENT.md 第 6 节）：
所有查询必须携带 user_id 过滤，跨用户访问一律返回"不存在"，避免泄漏资源存在性。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select

from app.db.models.knowledge import DocChunk, KnowledgeDoc
from app.db.repositories.base import BaseRepository


class KnowledgeRepository(BaseRepository[KnowledgeDoc]):
    """KnowledgeDoc 的 owner-scoped 数据访问。"""

    model = KnowledgeDoc

    async def get_for_user(self, user_id: UUID, doc_id: UUID) -> KnowledgeDoc | None:
        """查询某用户的文档；不属于该用户时返回 None（与不存在同语义）。"""
        result = await self.db.execute(
            select(KnowledgeDoc).where(
                KnowledgeDoc.id == doc_id,
                KnowledgeDoc.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_user(self, user_id: UUID) -> list[KnowledgeDoc]:
        """列出某用户的全部文档，按创建时间倒序。"""
        result = await self.db.execute(
            select(KnowledgeDoc)
            .where(KnowledgeDoc.user_id == user_id)
            .order_by(KnowledgeDoc.created_at.desc())
        )
        return list(result.scalars().all())

    async def chunks_for_user(self, user_id: UUID) -> list[DocChunk]:
        """列出某用户全部文档下的分块（经 KnowledgeDoc 关联过滤）。"""
        result = await self.db.execute(
            select(DocChunk)
            .join(KnowledgeDoc, KnowledgeDoc.id == DocChunk.doc_id)
            .where(KnowledgeDoc.user_id == user_id)
        )
        return list(result.scalars().all())

    async def stats_for_user(self, user_id: UUID) -> tuple[int, int]:
        """统计某用户的文档数与分块数。"""
        docs = await self.db.scalar(
            select(func.count()).select_from(KnowledgeDoc).where(KnowledgeDoc.user_id == user_id)
        )
        chunks = await self.db.scalar(
            select(func.count())
            .select_from(DocChunk)
            .join(KnowledgeDoc, KnowledgeDoc.id == DocChunk.doc_id)
            .where(KnowledgeDoc.user_id == user_id)
        )
        return int(docs or 0), int(chunks or 0)
