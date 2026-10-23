"""知识库 Repository：owner-scoped 的文档与分块查询。

核心约定（见 docs/DEVELOPMENT.md 第 6 节）：
所有查询必须携带 user_id 过滤，跨用户访问一律返回"不存在"，避免泄漏资源存在性。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select

from app.db.models.knowledge import DocChunk, KnowledgeDoc, VectorIndex
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

    async def index_status_counts_for_user(self, user_id: UUID) -> dict[str, int]:
        """按 `index_status` 统计某用户的文档数（2026-10-23 知识库页）。

        页面需要区分「真正需要用户处理」的文档：`failed` 要重新上传，
        `indexing` 只需等待。只给总数无法做这个提示。
        """
        rows = await self.db.execute(
            select(KnowledgeDoc.index_status, func.count())
            .where(KnowledgeDoc.user_id == user_id)
            .group_by(KnowledgeDoc.index_status)
        )
        return {str(status): int(count) for status, count in rows.all()}

    async def vector_rows_for_user(self, user_id: UUID) -> int:
        """统计某用户文档实际写入 `vector_index` 的条数。

        ## 为什么要单独查

        `doc_chunk` 有行 ≠ 向量已入库。实测踩过：`doc_chunk` 28344 行、
        `vector_index` 28344 行，而 Chroma 实际只有 4 条向量 —— 但更近的一层
        漂移是 `vector_index` 自己缺行（手工清库只删了 SQLite 行时）。

        比对「分块数」与「向量索引行数」能发现这一层漂移，比直接相信
        `index_status` 诚实得多。
        """
        return int(
            await self.db.scalar(
                select(func.count())
                .select_from(VectorIndex)
                .join(DocChunk, DocChunk.id == VectorIndex.chunk_id)
                .join(KnowledgeDoc, KnowledgeDoc.id == DocChunk.doc_id)
                .where(KnowledgeDoc.user_id == user_id)
            )
            or 0
        )
