"""按 DocChunk 重建向量测试（2026-10-22 向量一致性 Task 2）。

## 为什么不能用现有 `IndexManager.rebuild_all()`

它按**文件路径**重新解析文档：

```python
self.clear()                    # 清空 Chroma
for source in sources:
    self.add_document(source)   # 重新分块 → 生成**新的** chunk id
```

后果：Chroma 里的 id 与 SQLite `doc_chunk.id` **不一致** →
`_document_service._attach_storage_ids()` 无法把检索结果回溯到存储行 →
**引用追溯断裂**（这是本项目的核心能力）。

## 正确做法

从 SQLite 读 `DocChunk.content`（内容已在库，**不需重新解析文件**），
用**原 chunk_id** 作为向量库 id 写入。

## 本文件锁定

1. 写入向量库的 id **等于** `DocChunk.id`（回溯能力的前提）；
2. **分批**（28344 条不能一次全塞内存）；
3. 单批失败**不中断**整批（记录并继续，最后汇总）；
4. `--dry-run` 不写任何数据。
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.knowledge.services.reindex import ReindexResult, reindex_from_chunks


class _RecordingVectorStore:
    """记录写入的 id 与文本。"""

    def __init__(self, *, fail_on_batch: int | None = None) -> None:
        self.ids: list[str] = []
        self.texts: list[str] = []
        self.batches: list[int] = []
        self._fail_on_batch = fail_on_batch
        self._batch_index = 0

    def add_chunks(self, chunks: list[Any]) -> int:
        """与真实 `VectorStore.add_chunks(chunks)` 签名一致。"""
        self._batch_index += 1
        self.batches.append(len(chunks))
        if self._fail_on_batch == self._batch_index:
            raise RuntimeError("模拟单批写入失败")
        for chunk in chunks:
            self.ids.append(chunk.chunk_id)
            self.texts.append(chunk.text)
        return len(chunks)


class _Chunk:
    """`DocChunk` 的最小替身（实现只读这 4 个字段）。"""

    def __init__(
        self,
        chunk_id: uuid.UUID,
        doc_id: uuid.UUID,
        content: str,
        chunk_index: int = 0,
    ) -> None:
        self.id = chunk_id
        self.doc_id = doc_id
        self.content = content
        self.chunk_index = chunk_index


class _FakeSession:
    """按批返回分块（模拟 SQLAlchemy 的 offset/limit 分页）。"""

    def __init__(self, chunks: list[_Chunk]) -> None:
        self._chunks = chunks
        self.queries = 0

    async def execute(self, _stmt: Any) -> Any:
        self.queries += 1
        # 实现会带 limit/offset；这里用 queries 计数模拟分页
        return _Result(self._chunks)


class _Result:
    def __init__(self, chunks: list[_Chunk]) -> None:
        self._chunks = chunks

    def all(self) -> list[Any]:
        # 返回 (DocChunk, user_id) 元组，与实现一致
        return [(c, uuid.uuid4()) for c in self._chunks]

    def scalars(self) -> Any:
        return self

    def __iter__(self) -> Any:
        return iter(self.all())


def _chunks(count: int) -> list[_Chunk]:
    doc_id = uuid.uuid4()
    return [_Chunk(uuid.uuid4(), doc_id, f"分块内容 {i}") for i in range(count)]


class TestIdPreservation:
    @pytest.mark.asyncio
    async def test_uses_chunk_id_as_vector_id(self) -> None:
        """**核心断言**：向量 id 必须等于 `DocChunk.id`（否则回溯断裂）。"""
        chunks = _chunks(5)
        store = _RecordingVectorStore()

        await reindex_from_chunks(
            session=_FakeSession(chunks),
            vector_store=store,
            batch_size=10,
        )

        assert store.ids == [str(c.id) for c in chunks]

    @pytest.mark.asyncio
    async def test_passes_content_as_text(self) -> None:
        chunks = _chunks(3)
        store = _RecordingVectorStore()

        await reindex_from_chunks(session=_FakeSession(chunks), vector_store=store, batch_size=10)

        assert store.texts == [c.content for c in chunks]


class TestBatching:
    @pytest.mark.asyncio
    async def test_splits_into_batches(self) -> None:
        """分批写入（28344 条不能一次全塞）。"""
        chunks = _chunks(25)
        store = _RecordingVectorStore()

        result = await reindex_from_chunks(
            session=_FakeSession(chunks), vector_store=store, batch_size=10
        )

        assert len(store.batches) == 3, f"应分 3 批，实际 {store.batches}"
        assert sum(store.batches) == 25
        assert result.indexed == 25

    @pytest.mark.asyncio
    async def test_single_batch_when_small(self) -> None:
        chunks = _chunks(5)
        store = _RecordingVectorStore()

        await reindex_from_chunks(session=_FakeSession(chunks), vector_store=store, batch_size=10)

        assert store.batches == [5]


class TestFailureIsolation:
    @pytest.mark.asyncio
    async def test_single_batch_failure_does_not_abort(self) -> None:
        """单批失败不中断整批（否则 28344 条里一条坏数据就全废）。"""
        chunks = _chunks(25)
        store = _RecordingVectorStore(fail_on_batch=2)

        result = await reindex_from_chunks(
            session=_FakeSession(chunks), vector_store=store, batch_size=10
        )

        assert result.failed_batches == 1
        assert result.indexed == 15, f"第 1、3 批应成功（15 条），实际 {result.indexed}"

    @pytest.mark.asyncio
    async def test_result_records_errors(self) -> None:
        chunks = _chunks(15)
        store = _RecordingVectorStore(fail_on_batch=1)

        result = await reindex_from_chunks(
            session=_FakeSession(chunks), vector_store=store, batch_size=10
        )

        assert result.errors
        assert "模拟单批写入失败" in " ".join(result.errors)


class TestDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_writes_nothing(self) -> None:
        chunks = _chunks(20)
        store = _RecordingVectorStore()

        result = await reindex_from_chunks(
            session=_FakeSession(chunks), vector_store=store, batch_size=10, dry_run=True
        )

        assert store.ids == [], "dry-run 不应写入任何向量"
        assert result.indexed == 0
        assert result.planned == 20, "dry-run 应报出计划写入的数量"


class TestResultShape:
    @pytest.mark.asyncio
    async def test_result_has_counts(self) -> None:
        chunks = _chunks(8)
        store = _RecordingVectorStore()

        result = await reindex_from_chunks(
            session=_FakeSession(chunks), vector_store=store, batch_size=10
        )

        assert isinstance(result, ReindexResult)
        assert result.indexed == 8
        assert result.planned == 8
        assert result.failed_batches == 0

    @pytest.mark.asyncio
    async def test_result_serializable(self) -> None:
        chunks = _chunks(4)
        store = _RecordingVectorStore()

        result = await reindex_from_chunks(
            session=_FakeSession(chunks), vector_store=store, batch_size=10
        )

        payload = result.as_dict()

        assert payload["indexed"] == 4
        assert payload["planned"] == 4
        assert isinstance(payload["errors"], list)
