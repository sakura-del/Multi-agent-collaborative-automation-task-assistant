"""pgvector 后端集成测试（需要真实 PostgreSQL + pgvector）。

启用方式：
```bash
set TEST_PGVECTOR_DSN=postgresql://postgres:postgres@localhost:5432/postgres
pytest tests/test_pgvector_integration.py -q
```
无 DSN 时整组 skip——「本机没装 docker」不能让 2800 个离线单测变红。

测试用**随机表名**并自建自删，绝不动 `knowledge_vectors` 正式表；
所以可以安全地指向 Supabase 的临时库/临时 schema。
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest

from app.knowledge.services.vector_backends.base import (
    VectorDimensionMismatchError,
    VectorStoreConfigError,
)
from app.knowledge.services.vector_backends.pgvector_backend import PgVectorBackend

DSN = os.getenv("TEST_PGVECTOR_DSN", "").strip()

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not DSN, reason="未设置 TEST_PGVECTOR_DSN，跳过真实 pgvector 集成测试"),
]

DIMENSION = 8


def _vector(*values: float) -> list[float]:
    """补齐到固定维度：测试只关心前几维的方向。"""
    padded = list(values) + [0.0] * (DIMENSION - len(values))
    return padded[:DIMENSION]


def _drop_table(table: str) -> None:
    import psycopg

    with psycopg.connect(DSN, connect_timeout=5) as conn:
        conn.execute(f'DROP TABLE IF EXISTS "{table}"')
        conn.commit()


@pytest.fixture
def table_name() -> Iterator[str]:
    name = f"it_vectors_{uuid.uuid4().hex[:10]}"
    yield name
    try:
        _drop_table(name)
    except Exception:  # pragma: no cover - 清理失败不应掩盖测试结论
        pass


@pytest.fixture
def backend(table_name: str) -> Iterator[PgVectorBackend]:
    instance = PgVectorBackend(
        embedding_dimension=DIMENSION,
        collection_name="it",
        dsn=DSN,
        table=table_name,
        auto_migrate=True,
    )
    yield instance
    instance.close()


def _upsert(
    backend: PgVectorBackend,
    chunk_id: str,
    vector: list[float],
    *,
    doc_id: str = "d1",
    text: str = "正文",
    user_id: str = "u1",
) -> None:
    backend.upsert(
        ids=[chunk_id],
        vectors=[vector],
        documents=[text],
        metadatas=[{"doc_id": doc_id, "user_id": user_id, "chunk_index": 0}],
    )


class TestWriteAndRead:
    def test_upsert_then_count_and_ids(self, backend: PgVectorBackend) -> None:
        _upsert(backend, "c1", _vector(1.0))
        _upsert(backend, "c2", _vector(0.0, 1.0))

        assert backend.count() == 2
        assert backend.list_ids() == ["c1", "c2"]  # 稳定排序

    def test_upsert_same_id_updates_instead_of_duplicating(self, backend: PgVectorBackend) -> None:
        _upsert(backend, "c1", _vector(1.0), text="旧正文")
        _upsert(backend, "c1", _vector(0.0, 1.0), text="新正文")

        assert backend.count() == 1
        hits = backend.query(_vector(0.0, 1.0), top_k=1)
        assert hits[0].text == "新正文"

    def test_batch_upsert(self, backend: PgVectorBackend) -> None:
        backend.upsert(
            ids=["a", "b", "c"],
            vectors=[_vector(1.0), _vector(0.0, 1.0), _vector(0.0, 0.0, 1.0)],
            documents=["A", "B", "C"],
            metadatas=[{"doc_id": "d1"}, {"doc_id": "d1"}, {"doc_id": "d2"}],
        )
        assert backend.count() == 3


class TestSearch:
    def test_nearest_first_with_cosine_distance(self, backend: PgVectorBackend) -> None:
        _upsert(backend, "same", _vector(1.0), text="同向")
        _upsert(backend, "orthogonal", _vector(0.0, 1.0), text="正交")

        hits = backend.query(_vector(1.0), top_k=2)

        assert hits[0].chunk_id == "same"
        assert hits[0].distance == pytest.approx(0.0, abs=1e-6)
        assert hits[1].distance == pytest.approx(1.0, abs=1e-6)

    def test_metadata_round_trips(self, backend: PgVectorBackend) -> None:
        _upsert(backend, "c1", _vector(1.0), doc_id="doc-9", user_id="user-7")

        hit = backend.query(_vector(1.0), top_k=1)[0]

        assert hit.metadata["doc_id"] == "doc-9"
        assert hit.metadata["user_id"] == "user-7"
        assert hit.metadata["chunk_index"] == 0

    def test_where_filters_before_top_k(self, backend: PgVectorBackend) -> None:
        """过滤必须在截断前生效：否则会先取到别人的向量再丢弃，结果为空。"""
        _upsert(backend, "mine", _vector(1.0), user_id="me")
        _upsert(backend, "others", _vector(1.0), user_id="someone-else")

        hits = backend.query(_vector(1.0), top_k=5, where={"user_id": "me"})

        assert [h.chunk_id for h in hits] == ["mine"]

    def test_in_operator(self, backend: PgVectorBackend) -> None:
        _upsert(backend, "a", _vector(1.0), doc_id="d1")
        _upsert(backend, "b", _vector(1.0), doc_id="d2")
        _upsert(backend, "c", _vector(1.0), doc_id="d3")

        hits = backend.query(_vector(1.0), top_k=5, where={"doc_id": {"$in": ["d1", "d3"]}})

        assert sorted(h.chunk_id for h in hits) == ["a", "c"]

    def test_empty_table_returns_empty(self, backend: PgVectorBackend) -> None:
        assert backend.query(_vector(1.0), top_k=5) == []


class TestDeleteAndReset:
    def test_delete_by_doc_returns_exact_rowcount(self, backend: PgVectorBackend) -> None:
        _upsert(backend, "c1", _vector(1.0), doc_id="d1")
        _upsert(backend, "c2", _vector(1.0), doc_id="d1")
        _upsert(backend, "c3", _vector(1.0), doc_id="d2")

        assert backend.delete_by_doc("d1") == 2
        assert backend.list_ids() == ["c3"]

    def test_reset_only_affects_current_collection(self, table_name: str) -> None:
        first = PgVectorBackend(
            embedding_dimension=DIMENSION,
            collection_name="kb-a",
            dsn=DSN,
            table=table_name,
            auto_migrate=True,
        )
        second = PgVectorBackend(
            embedding_dimension=DIMENSION,
            collection_name="kb-b",
            dsn=DSN,
            table=table_name,
        )
        try:
            _upsert(first, "a1", _vector(1.0))
            _upsert(second, "b1", _vector(1.0))

            first.reset()

            assert first.count() == 0
            assert second.count() == 1
        finally:
            first.close()
            second.close()


class TestSchemaValidation:
    def test_dimension_mismatch_is_rejected(self, table_name: str) -> None:
        created = PgVectorBackend(
            embedding_dimension=DIMENSION,
            collection_name="it",
            dsn=DSN,
            table=table_name,
            auto_migrate=True,
        )
        try:
            _upsert(created, "c1", _vector(1.0))
        finally:
            created.close()

        wrong = PgVectorBackend(
            embedding_dimension=DIMENSION + 4,
            collection_name="it",
            dsn=DSN,
            table=table_name,
        )
        try:
            with pytest.raises(VectorDimensionMismatchError):
                wrong.count()
        finally:
            wrong.close()

    def test_missing_table_gives_actionable_error(self, table_name: str) -> None:
        missing = PgVectorBackend(
            embedding_dimension=DIMENSION,
            collection_name="it",
            dsn=DSN,
            table=f"{table_name}_absent",
        )
        try:
            with pytest.raises(VectorStoreConfigError):
                missing.count()
        finally:
            missing.close()

    def test_ping_is_true(self, backend: PgVectorBackend) -> None:
        assert backend.ping() is True
