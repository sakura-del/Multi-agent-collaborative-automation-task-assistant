"""reindex 必须同步更新 vector_index.chroma_id（2026-10-23 修复）。

## 缺陷（我自己上一轮引入的）

`reindex_from_chunks` 用 `DocChunk.id`（UUID）作为 Chroma id 写入，
但**没有更新 `vector_index.chroma_id`** —— 表里仍是建库时生成的 16 位 hex。

实测后果（服务器）：

| 项 | 值 |
| --- | --- |
| Chroma 实际 id | UUID（28344 条） |
| `vector_index.chroma_id` | 16 位 hex（28344 条，**全部指向不存在的 id**） |
| 交集 | **0** |

**为什么检索还能工作**：`_attach_storage_ids` 里「结果 id 已是 UUID → 原样保留」，
所以恰好绕过了映射表。但这意味着：

1. `vector_index` 表变成**死数据**，任何按 chroma_id 反查的逻辑都会失败；
2. 我写的 `check_vector_consistency` 只比**数量** → 报「一致」，**漏检**。

## 本文件锁定

1. reindex 后 `vector_index.chroma_id` 必须等于对应的 Chroma id（即 chunk_id）；
2. 只更新仍在库的 chunk（不新增、不删除行）；
3. dry-run 不写库。
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
    """记录 `update(VectorIndex)` 语句的参数（用于断言同步）。"""

    def __init__(self, chunks: list[_Chunk]) -> None:
        self._chunks = chunks
        self.updated_chroma_ids: list[tuple[str, str]] = []
        self.committed = 0

    async def execute(self, stmt: Any) -> Any:
        # 区分「查询 DocChunk」与「update VectorIndex」
        compiled = str(stmt)
        if "UPDATE" in compiled.upper() and "vector_index" in compiled:
            params = stmt.compile().params
            # values(chroma_id=X) 与 where(chunk_id=Y)
            chroma = next((v for k, v in params.items() if k.startswith("chroma_id")), None)
            chunk = next((v for k, v in params.items() if k.startswith("chunk_id")), None)
            if chroma is not None and chunk is not None:
                self.updated_chroma_ids.append((str(chroma), str(chunk)))

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


class TestVectorIndexSync:
    @pytest.mark.asyncio
    async def test_updates_chroma_id_to_chunk_id(self) -> None:
        """**核心断言**：reindex 后 vector_index.chroma_id 必须等于 chunk_id。"""
        chunks = _chunks(3)
        store = _RecordingVectorStore()
        session = _FakeSession(chunks)

        await reindex_from_chunks(session=session, vector_store=store, batch_size=10)

        expected = {(str(c.id), str(c.id)) for c in chunks}
        assert set(session.updated_chroma_ids) == expected, (
            "vector_index.chroma_id 必须同步成新的向量 id（chunk_id），"
            f"实际 {session.updated_chroma_ids}"
        )

    @pytest.mark.asyncio
    async def test_commits_after_sync(self) -> None:
        chunks = _chunks(2)
        session = _FakeSession(chunks)

        await reindex_from_chunks(
            session=session, vector_store=_RecordingVectorStore(), batch_size=10
        )

        assert session.committed >= 1, "同步后必须提交"

    @pytest.mark.asyncio
    async def test_dry_run_does_not_sync(self) -> None:
        chunks = _chunks(5)
        session = _FakeSession(chunks)

        await reindex_from_chunks(
            session=session,
            vector_store=_RecordingVectorStore(),
            batch_size=10,
            dry_run=True,
        )

        assert session.updated_chroma_ids == []
        assert session.committed == 0
