"""reindex 必须同步更新 vector_index.chroma_id，且必须是**批量**更新。

## 历史缺陷 A（2026-10-23）

`reindex_from_chunks` 用 `DocChunk.id`（UUID）作为向量 id 写入，但**没有更新
`vector_index.chroma_id`** —— 表里仍是建库时生成的 16 位 hex。
实测：Chroma 实际 id 是 UUID、映射表是 hex，交集 **0**，而数量完全相等。

## 历史缺陷 B（2026-11-04，迁移到 Supabase 后暴露）

修复 A 时写成**每个 chunk 一条 UPDATE**：28341 条 × 0.48s 网络往返 ≈ **3.8 小时**，
「跑一次 reindex」事实上不可执行。本地库每条 2ms 时完全看不出来。

## 本文件锁定

1. 一批只发**一条** UPDATE（`WHERE chunk_id IN (...)` + `SET chroma_id = chunk_id`）；
2. 只作用于本批的 chunk（不误伤其它行）；
3. reindex 后 `chroma_id` 语义仍是「等于 chunk_id」；
4. dry-run 不写库。
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.knowledge.services.reindex import reindex_from_chunks


class _RecordingVectorStore:
    def __init__(self) -> None:
        self.ids: list[str] = []

    def add_chunks(self, chunks: list[Any]) -> int:
        for chunk in chunks:
            self.ids.append(chunk.chunk_id)
        return len(chunks)


class _Chunk:
    def __init__(self, chunk_id: uuid.UUID, doc_id: uuid.UUID, content: str) -> None:
        self.id = chunk_id
        self.doc_id = doc_id
        self.content = content
        self.chunk_index = 0


class _Row:
    """模拟 `select(DocChunk, KnowledgeDoc.user_id)` 返回的 Row。"""

    def __init__(self, chunk: _Chunk, user_id: uuid.UUID) -> None:
        self._chunk = chunk
        self._user_id = user_id

    def __iter__(self) -> Any:
        return iter((self._chunk, self._user_id))

    def __getitem__(self, index: int) -> Any:
        return (self._chunk, self._user_id)[index]


class _FakeSession:
    """记录 `update(VectorIndex)` 语句与参数（用于断言批量与作用域）。"""

    def __init__(self, chunks: list[_Chunk]) -> None:
        self._chunks = chunks
        self.updates: list[Any] = []
        self.committed = 0

    async def execute(self, stmt: Any) -> Any:
        compiled_sql = str(stmt)
        if "UPDATE" in compiled_sql.upper() and "vector_index" in compiled_sql:
            self.updates.append(stmt)

        user_id = uuid.uuid4()

        class _Result:
            def __init__(self, chunks: list[_Chunk]) -> None:
                self._chunks = chunks

            def all(self) -> list[_Row]:
                return [_Row(c, user_id) for c in self._chunks]

        return _Result(self._chunks)

    async def commit(self) -> None:
        self.committed += 1


def _chunks(count: int) -> list[_Chunk]:
    doc_id = uuid.uuid4()
    return [_Chunk(uuid.uuid4(), doc_id, f"内容 {i}") for i in range(count)]


def _update_params(stmt: Any) -> list[Any]:
    """取出 UPDATE 语句里 IN (...) 的绑定值（UUID 或字符串都归一成 str）。"""
    params = stmt.compile().params
    values = []
    for value in params.values():
        if isinstance(value, (list, tuple, set)):
            values.extend(str(v) for v in value)
        else:
            values.append(str(value))
    return values


class TestVectorIndexSync:
    @pytest.mark.asyncio
    async def test_one_update_per_batch_not_per_chunk(self) -> None:
        """**核心断言**：3 条 chunk 一批 → 只发 1 条 UPDATE（而不是 3 条）。"""
        chunks = _chunks(3)
        session = _FakeSession(chunks)

        await reindex_from_chunks(
            session=session, vector_store=_RecordingVectorStore(), batch_size=10
        )

        assert (
            len(session.updates) == 1
        ), "批量同步必须每批只发一条 UPDATE；逐条更新在远程库上是 3.8 小时的操作"

    @pytest.mark.asyncio
    async def test_update_is_scoped_to_whole_batch(self) -> None:
        chunks = _chunks(3)
        session = _FakeSession(chunks)

        await reindex_from_chunks(
            session=session, vector_store=_RecordingVectorStore(), batch_size=10
        )

        params = _update_params(session.updates[0])
        assert set(params) == {str(c.id) for c in chunks}

    @pytest.mark.asyncio
    async def test_sets_chroma_id_from_chunk_id_column(self) -> None:
        """值语义：`chroma_id = chunk_id`（同一列），无需逐行绑定。"""
        chunks = _chunks(2)
        session = _FakeSession(chunks)

        await reindex_from_chunks(
            session=session, vector_store=_RecordingVectorStore(), batch_size=10
        )

        sql = str(session.updates[0])
        assert "chroma_id" in sql
        # 值是列引用（不是参数）：SET chroma_id=vector_index.chunk_id
        assert "chunk_id" in sql.split("WHERE")[0]

    @pytest.mark.asyncio
    async def test_batch_size_respected(self) -> None:
        """5 条 chunk、batch=2 → 3 条 UPDATE（2+2+1）。"""
        chunks = _chunks(5)
        session = _FakeSession(chunks)

        await reindex_from_chunks(
            session=session, vector_store=_RecordingVectorStore(), batch_size=2
        )

        assert len(session.updates) == 3

    @pytest.mark.asyncio
    async def test_commits_after_sync(self) -> None:
        session = _FakeSession(_chunks(2))

        await reindex_from_chunks(
            session=session, vector_store=_RecordingVectorStore(), batch_size=10
        )

        assert session.committed >= 1, "同步后必须提交"

    @pytest.mark.asyncio
    async def test_dry_run_does_not_sync(self) -> None:
        session = _FakeSession(_chunks(5))

        await reindex_from_chunks(
            session=session,
            vector_store=_RecordingVectorStore(),
            batch_size=10,
            dry_run=True,
        )

        assert session.updates == []
        assert session.committed == 0
