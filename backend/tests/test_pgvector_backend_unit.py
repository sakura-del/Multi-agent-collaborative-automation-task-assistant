"""PgVectorBackend 单元测试（不连真库）。

测试策略：在**驱动边界**（psycopg 连接池）注入记录型替身，
断言的是后端对外行为——写入的参数化、过滤条件编译、删除作用域、
异常映射与 DSN 脱敏——而不是内部实现细节。

为什么不用真库：全量测试必须离线可跑（2837 个用例不依赖 docker）。
真实 SQL 语义由 `test_pgvector_integration.py` 在提供 DSN 时覆盖。
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any

import pytest

from app.knowledge.services.vector_backends import pgvector_backend
from app.knowledge.services.vector_backends.base import (
    VectorDimensionMismatchError,
    VectorStoreConfigError,
    VectorStoreUnavailableError,
)
from app.knowledge.services.vector_backends.dsn import redact_dsn, to_sync_dsn
from app.knowledge.services.vector_backends.pgvector_backend import (
    PgVectorBackend,
    split_table_name,
)

_DEFAULT_DIMENSION = 4


class _FakeCursor:
    """记录执行过的 SQL 与参数，并按脚本/默认值返回结果。

    Catalog 探测（扩展、表、维度）用构造参数模拟真实库状态；
    `script` 只喂业务查询（SELECT hits / DELETE rowcount），不参与探针。
    """

    def __init__(
        self,
        script: list[dict[str, Any]],
        dimension: int = _DEFAULT_DIMENSION,
        *,
        extension_present: bool = True,
        table_present: bool = True,
        table_dimension: int | None = None,
    ) -> None:
        self._script = script
        self._extension_present = extension_present
        self._table_present = table_present
        # pgvector 的 atttypmod **直接就是维度**（不像 varchar 加 4）；
        # 曾在 Supabase 实测 vector(1024) → 1024，见 pgvector_backend 注释
        dim = table_dimension if table_dimension is not None else dimension
        self._table_row = ("public", "knowledge_vectors", dim)
        self.executed: list[tuple[str, Any]] = []
        self._current: dict[str, Any] = {}
        self.rowcount = 0

    @staticmethod
    def _text(sql: Any) -> str:
        return sql.as_string(None) if hasattr(sql, "as_string") else str(sql)

    def _advance(self, text: str) -> None:
        if "pg_extension" in text:
            self._current = {"one": ("public",) if self._extension_present else None}
        elif "pg_class" in text:
            self._current = {"one": self._table_row if self._table_present else None}
        elif self._script:
            self._current = self._script.pop(0)
        else:
            self._current = {}

    def execute(self, sql: Any, params: Any = None) -> None:
        text = self._text(sql)
        self.executed.append((text, params))
        self._advance(text)
        self.rowcount = int(self._current.get("rowcount", 0))

    def executemany(self, sql: Any, rows: Any) -> None:
        """批量写入：真实 psycopg 会流水线化，测试只需记录每一行。"""
        text = self._text(sql)
        self.executed.append((text, rows))
        self._advance(text)
        self.rowcount = len(list(rows))

    def fetchone(self) -> Any:
        return self._current.get("one")

    def fetchall(self) -> Any:
        return self._current.get("all", [])

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


class _FakePool:
    def __init__(
        self,
        script: list[dict[str, Any]] | None = None,
        dimension: int = _DEFAULT_DIMENSION,
        *,
        extension_present: bool = True,
        table_present: bool = True,
        table_dimension: int | None = None,
    ) -> None:
        self.cursor = _FakeCursor(
            script or [],
            dimension,
            extension_present=extension_present,
            table_present=table_present,
            table_dimension=table_dimension,
        )
        self.connection_obj = _FakeConnection(self.cursor)

    @contextmanager
    def connection(self) -> Any:
        yield self.connection_obj


def _make_backend(
    *,
    dimension: int = _DEFAULT_DIMENSION,
    script: list[dict[str, Any]] | None = None,
    extension_present: bool = True,
    table_present: bool = True,
    table_dimension: int | None = None,
) -> tuple[PgVectorBackend, _FakePool]:
    pool = _FakePool(
        script,
        dimension,
        extension_present=extension_present,
        table_present=table_present,
        table_dimension=table_dimension,
    )
    backend = PgVectorBackend(
        embedding_dimension=dimension,
        collection_name="kb",
        dsn="postgresql://u:p@localhost:5432/db",
        table="knowledge_vectors",
        pool=pool,  # type: ignore[arg-type]
        auto_migrate=False,
    )
    return backend, pool


class TestUpsert:
    def test_serializes_vector_and_metadata(self) -> None:
        """向量必须以 pgvector 文本格式传递，元数据进 jsonb 参数。"""
        backend, pool = _make_backend()
        backend.upsert(
            ids=["c1"],
            vectors=[[1.0, 0.0, 0.0, 0.5]],
            documents=["一段正文"],
            metadatas=[{"doc_id": "d1", "chunk_index": 2, "user_id": "u1"}],
        )

        sql, rows = pool.cursor.executed[-1]
        assert "INSERT INTO" in sql
        assert "ON CONFLICT" in sql
        # 向量是参数而非拼进 SQL
        assert "[1.0,0.0,0.0,0.5]" in rows[0]
        assert "一段正文" in rows[0]
        assert json.loads(rows[0][-1])["user_id"] == "u1"

    def test_rejects_dimension_mismatch_before_db(self) -> None:
        """维度不一致必须在写库前报错，不能等 SQL 层报错。"""
        backend, pool = _make_backend(dimension=4)

        with pytest.raises(VectorDimensionMismatchError):
            backend.upsert(
                ids=["c1"],
                vectors=[[1.0, 0.0]],
                documents=["x"],
                metadatas=[{"doc_id": "d1"}],
            )

        assert pool.cursor.executed == []

    def test_rejects_misaligned_batch(self) -> None:
        """ids/vectors/documents/metadatas 长度必须一致。"""
        backend, _ = _make_backend()
        with pytest.raises(ValueError):
            backend.upsert(
                ids=["c1", "c2"],
                vectors=[[0.0, 0.0, 0.0, 0.0]],
                documents=["x", "y"],
                metadatas=[{}, {}],
            )


class TestQuery:
    def _hits(self) -> list[dict[str, Any]]:
        return [
            {
                "all": [
                    ("c1", "正文1", {"doc_id": "d1", "user_id": "u1"}, 0.25),
                    ("c2", "正文2", {"doc_id": "d2", "user_id": "u1"}, 0.75),
                ]
            }
        ]

    def test_returns_distance_and_metadata(self) -> None:
        backend, _ = _make_backend(script=self._hits())
        hits = backend.query([1.0, 0.0, 0.0, 0.0], top_k=2, where={"user_id": "u1"})

        assert [h.chunk_id for h in hits] == ["c1", "c2"]
        assert hits[0].distance == pytest.approx(0.25)
        assert hits[0].metadata["doc_id"] == "d1"

    def test_where_filter_is_parameterized(self) -> None:
        """过滤值必须走参数，不能拼进 SQL（注入测试）。"""
        backend, pool = _make_backend(script=self._hits())
        evil = "u1'); DROP TABLE knowledge_vectors; --"
        backend.query([1.0, 0.0, 0.0, 0.0], top_k=5, where={"user_id": evil})

        sql, params = pool.cursor.executed[-1]
        assert "DROP TABLE" not in sql
        assert evil in params
        assert "user_id" in sql

    def test_supports_in_operator(self) -> None:
        backend, pool = _make_backend(script=self._hits())
        backend.query([1.0, 0.0, 0.0, 0.0], top_k=5, where={"doc_id": {"$in": ["d1", "d2"]}})

        sql, params = pool.cursor.executed[-1]
        assert "= ANY" in sql
        assert ["d1", "d2"] in params

    def test_unknown_key_raises(self) -> None:
        """未识别的过滤键必须报错：静默忽略会导致过滤失效（跨用户串数据）。"""
        backend, _ = _make_backend(script=self._hits())
        with pytest.raises(ValueError):
            backend.query([1.0, 0.0, 0.0, 0.0], top_k=5, where={"nope": "x"})

    def test_unknown_operator_raises(self) -> None:
        backend, _ = _make_backend(script=self._hits())
        with pytest.raises(ValueError):
            backend.query([1.0, 0.0, 0.0, 0.0], top_k=5, where={"doc_id": {"$regex": ".*"}})

    def test_no_hits_returns_empty(self) -> None:
        backend, _ = _make_backend(script=[{"all": []}])
        assert backend.query([1.0, 0.0, 0.0, 0.0], top_k=5) == []

    def test_zero_top_k_short_circuits(self) -> None:
        backend, pool = _make_backend(script=self._hits())
        assert backend.query([1.0, 0.0, 0.0, 0.0], top_k=0) == []
        assert pool.cursor.executed == []


class TestDeleteAndReset:
    def test_delete_by_doc_scopes_collection_and_returns_rowcount(self) -> None:
        backend, pool = _make_backend(script=[{"rowcount": 3}])
        removed = backend.delete_by_doc("d1")

        assert removed == 3
        sql, params = pool.cursor.executed[-1]
        assert "DELETE FROM" in sql
        assert params == ("kb", "d1")

    def test_reset_only_clears_current_collection(self) -> None:
        backend, pool = _make_backend(script=[{"rowcount": 7}])
        backend.reset()

        sql, params = pool.cursor.executed[-1]
        assert "DELETE FROM" in sql
        assert params == ("kb",)


class TestSchemaAndErrors:
    def test_probe_uses_schema_qualified_name(self) -> None:
        """探测必须带 schema：Supabase pooler 会话的 search_path 是空的，
        未限定的 to_regclass 会返回 NULL → 误判「表不存在」（2026-11-03 实测）。"""
        backend, pool = _make_backend()
        backend.count()

        probes = [params for sql, params in pool.cursor.executed if "pg_class" in sql]
        assert probes and probes[0] == ("public.knowledge_vectors",)

    def test_split_table_name(self) -> None:
        assert split_table_name("knowledge_vectors") == ("public", "knowledge_vectors")
        assert split_table_name("other.knowledge_vectors") == ("other", "knowledge_vectors")

    def test_dimension_mismatch_detected_from_catalog(self) -> None:
        """表列维度与嵌入维度不一致 → 配置错误（不是运行期 500）。

        表是 1024 维（bge-large-zh），后端按 64 维初始化，必须报错。
        """
        backend, _ = _make_backend(dimension=64, table_dimension=1024)
        with pytest.raises(VectorDimensionMismatchError):
            backend.count()

    def test_atttypmod_is_the_dimension_itself(self) -> None:
        """pgvector 的 atttypmod 就是维度，不能再减 VARHDRSZ。

        2026-11-03 在 Supabase 实测：`vector(1024)` → atttypmod = 1024。
        按 varchar 习惯写 `- 4` 会把 1024 读成 1020，真实写入/检索全被误判为维度不一致。
        """
        backend, _ = _make_backend(dimension=1024, table_dimension=1024)
        # 不该抛 VectorDimensionMismatchError
        assert backend.column_dimension == 1024

    def test_undimensioned_vector_column_rejected(self) -> None:
        """`vector`（不写维度）无法建 HNSW 索引，必须要求定长列。"""
        backend, _ = _make_backend(dimension=4, table_dimension=-1)
        with pytest.raises(VectorStoreConfigError):
            backend.count()

    def test_missing_extension_raises_config_error(self) -> None:
        """pgvector 扩展没装是「库没准备好」，必须给出可操作的错误。"""
        backend, _ = _make_backend(extension_present=False)
        with pytest.raises(VectorStoreConfigError):
            backend.count()

    def test_missing_table_raises_config_error(self) -> None:
        backend, _ = _make_backend(table_present=False)
        with pytest.raises(VectorStoreConfigError):
            backend.count()

    def test_driver_error_maps_to_unavailable(self) -> None:
        """连接失败 → Unavailable（API 层映射 503），不是 500。"""
        backend, pool = _make_backend()
        pool.connection = contextmanager(_raise_operational)  # type: ignore[method-assign]
        with pytest.raises(VectorStoreUnavailableError):
            backend.count()


class TestPoolConfiguration:
    """连接池参数：默认必须关掉服务端预编译语句。

    原因不只是 pgbouncer：`ORDER BY embedding <=> $1` 一旦走 generic plan，
    距离依赖参数值 → 用不上 HNSW 有序扫描，退化成全表 Sort。
    """

    def _capture(
        self, monkeypatch: pytest.MonkeyPatch, **kwargs: Any
    ) -> tuple[dict[str, Any], PgVectorBackend]:
        captured: dict[str, Any] = {}

        class _Pool:
            def __init__(self, **pool_kwargs: Any) -> None:
                captured.update(pool_kwargs)

        monkeypatch.setattr(pgvector_backend, "ConnectionPool", _Pool)
        backend = PgVectorBackend(
            embedding_dimension=4,
            collection_name="kb",
            dsn="postgresql://u:p@localhost:5432/db",
            **kwargs,
        )
        backend._ensure_pool()
        return captured, backend

    def test_prepared_statements_disabled_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured, _ = self._capture(monkeypatch)

        assert captured["kwargs"]["prepare_threshold"] is None
        assert "statement_timeout" in captured["conninfo"]

    def test_flag_off_keeps_server_side_prepared_statements(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured, _ = self._capture(monkeypatch, disable_prepared_statements=False)

        assert captured["kwargs"] == {}

    def test_pool_sizes_come_from_arguments(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured, _ = self._capture(monkeypatch, pool_min_size=2, pool_max_size=7)

        assert (captured["min_size"], captured["max_size"]) == (2, 7)


def _raise_operational() -> Any:
    import psycopg

    raise psycopg.OperationalError("connection refused")
    yield  # pragma: no cover - 生成器形态仅为满足 contextmanager


class TestDsnHelpers:
    def test_redacts_password(self) -> None:
        masked = redact_dsn("postgresql://user:super-secret@db.example.com:5432/postgres")
        assert "super-secret" not in masked
        assert "db.example.com" in masked

    def test_redacts_keyword_password(self) -> None:
        masked = redact_dsn("host=db port=5432 user=u password=super-secret dbname=x")
        assert "super-secret" not in masked
        assert "db" in masked

    def test_strips_asyncpg_driver(self) -> None:
        """业务库 DSN 是 asyncpg，同步驱动必须去掉 `+asyncpg`。"""
        assert to_sync_dsn("postgresql+asyncpg://u:p@h:5432/db") == "postgresql://u:p@h:5432/db"
        assert to_sync_dsn("postgresql://u:p@h:5432/db") == "postgresql://u:p@h:5432/db"
