"""知识库检索提供者：为长驻运行时提供按需开 session 的证据检索（Plan 3 Exit Gate 接线）。

`KnowledgeDocumentService` 与 session 同生命周期；工作流需要长驻依赖，因此这里
提供 `search_for_context()` 适配器——每次调用打开一个独立 session，复用同一
owner-scoped 检索语义（`where={"user_id": ...}` + 独立 namespace）。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.session import AsyncSessionLocal
from app.knowledge.services.document_service import KnowledgeDocumentService
from app.knowledge.services.factory import get_index_manager, get_kb, get_storage


class KnowledgeSearchProvider:
    """按调用打开 session 的证据检索提供者（供 RuntimeContainer.knowledge_query）。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] = AsyncSessionLocal,
    ) -> None:
        self._session_factory = session_factory

    async def search_for_context(
        self,
        user_id: UUID,
        query: str,
        *,
        top_k: int = 8,
    ) -> list[Any]:
        async with self._session_factory() as session:
            service = KnowledgeDocumentService(
                session,
                storage=get_storage(),
                index_manager=get_index_manager(),
                kb=get_kb(),
            )
            return await service.search_for_context(user_id, query, top_k=top_k)
