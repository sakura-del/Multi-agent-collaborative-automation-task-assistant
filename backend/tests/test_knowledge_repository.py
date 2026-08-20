"""KnowledgeRepository 测试：owner-scoped 查询与双用户隔离。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.knowledge import DocChunk, KnowledgeDoc
from app.db.models.user import User
from app.db.repositories.knowledge import KnowledgeRepository

Docs = tuple[Any, Any]
Users = tuple[Any, Any]


@pytest_asyncio.fixture
async def users(db_session: AsyncSession) -> AsyncIterator[Users]:
    """创建两个用户，按 (A, B) 顺序返回。"""
    user_a = User(username="owner-a", email="a@test.com", password_hash="x", role="user")
    user_b = User(username="owner-b", email="b@test.com", password_hash="x", role="user")
    db_session.add_all([user_a, user_b])
    await db_session.flush()
    yield user_a, user_b


@pytest_asyncio.fixture
async def docs(db_session: AsyncSession, users: Users) -> AsyncIterator[Docs]:
    """每个用户各有一份文档，A 的文档带一个分块。"""
    user_a, user_b = users
    doc_a = KnowledgeDoc(
        user_id=user_a.id,
        title="A 的文档",
        doc_type="txt",
        index_status="ready",
        chunk_count=1,
    )
    doc_b = KnowledgeDoc(
        user_id=user_b.id,
        title="B 的文档",
        doc_type="txt",
        index_status="ready",
        chunk_count=0,
    )
    db_session.add_all([doc_a, doc_b])
    await db_session.flush()
    db_session.add(DocChunk(doc_id=doc_a.id, chunk_index=0, content="A 的块", char_count=3))
    await db_session.flush()
    yield doc_a, doc_b


class TestGetForUser:
    async def test_returns_own_doc(
        self, db_session: AsyncSession, users: Users, docs: Docs
    ) -> None:
        user_a, _ = users
        doc_a, _ = docs
        repo = KnowledgeRepository(db_session)
        assert (await repo.get_for_user(user_a.id, doc_a.id)).id == doc_a.id

    async def test_other_users_doc_is_none(
        self, db_session: AsyncSession, users: Users, docs: Docs
    ) -> None:
        _, user_b = users
        doc_a, _ = docs
        repo = KnowledgeRepository(db_session)
        assert await repo.get_for_user(user_b.id, doc_a.id) is None


class TestListForUser:
    async def test_lists_only_own_docs(
        self,
        db_session: AsyncSession,
        users: Users,
        docs: Docs,
    ) -> None:
        user_a, _ = users
        doc_a, _ = docs
        repo = KnowledgeRepository(db_session)
        listed = await repo.list_for_user(user_a.id)
        assert [doc.id for doc in listed] == [doc_a.id]


class TestChunksForUser:
    async def test_returns_only_own_chunks(
        self, db_session: AsyncSession, users: Users, docs: Docs
    ) -> None:
        user_a, user_b = users
        repo = KnowledgeRepository(db_session)
        assert len(await repo.chunks_for_user(user_a.id)) == 1
        assert await repo.chunks_for_user(user_b.id) == []


class TestStatsForUser:
    async def test_counts_are_owner_scoped(
        self, db_session: AsyncSession, users: Users, docs: Docs
    ) -> None:
        user_a, user_b = users
        repo = KnowledgeRepository(db_session)
        assert await repo.stats_for_user(user_a.id) == (1, 1)
        assert await repo.stats_for_user(user_b.id) == (1, 0)
