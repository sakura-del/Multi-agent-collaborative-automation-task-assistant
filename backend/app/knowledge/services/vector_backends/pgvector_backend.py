"""Supabase PostgreSQL + pgvector 后端。

设计要点与取舍：

1. **同步 psycopg3**：`VectorStore` 是同步接口，上层六处调用方（IndexManager /
   HybridRetriever / DocumentService / consistency / reindex / health）都按同步调用。
   改 async 要动四层，违反「迁移后接口不变」的硬约束。代价是查询期间阻塞事件循环
   ——与迁移前的同步 Chroma HTTP 调用一致；用 `connect_timeout=5s`、
   `statement_timeout=15s`、池 `timeout=5s` 封顶。异步化列为后续独立任务。

2. **原生 SQL，不装 `pgvector` Python 包**：向量以文本字面量
   （`'[0.1,0.2,…]'`）作为**参数**传入并 `::vector` 转换，既避免把 `vector`
   类型塞进 SQLAlchemy `Base.metadata`（会干扰 `create_all`/autogenerate），
   也避免逐连接注册类型适配器（pgbouncer 连接复用下更稳）。

3. **Supabase 三条硬约束**：
   - `vector` 扩展通常装在 `extensions` schema → 从 `pg_extension` 解析，
     查询里用限定类型名，不依赖 `search_path`；
   - transaction pooler 不支持 prepared statement → `prepare_threshold=None`；
   - 直连 `db.<ref>.supabase.co:5432` 可能 IPv6-only，池化器（6543）支持 IPv4；
     需要 SSL 时由配置或 DSN 里的 `sslmode` 决定。

4. **DDL 只由 alembic 执行**（`auto_migrate` 默认关闭，仅集成测试/临时库打开）。
   应用账号不需要 DDL 权限，运行时也不重复跑 `CREATE TABLE IF NOT EXISTS`。

5. **异常分层**：配置错（维度/表缺失/扩展缺失）→ `VectorStoreConfigError`
   （启动期就该暴露）；连不上/超时 → `VectorStoreUnavailableError`（API 映射 503）。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg import sql
from psycopg_pool import ConnectionPool, PoolTimeout

from app.core.logging import get_logger
from app.knowledge.services.vector_backends.base import (
    VectorDimensionMismatchError,
    VectorMatch,
    VectorStoreConfigError,
    VectorStoreError,
    VectorStoreUnavailableError,
)
from app.knowledge.services.vector_backends.dsn import (
    describe_dsn,
    redact_dsn,
    to_sync_dsn,
    with_options,
)

logger = get_logger(__name__)

# 允许出现在 where 过滤里的列（白名单，防注入；不认识的键直接报错）
WHERE_COLUMNS: dict[str, str] = {
    "doc_id": "doc_id",
    "user_id": "user_id",
    "doc_source": "doc_source",
    "chunk_index": "chunk_index",
}

# Catalog 探测：一次查询同时拿到「表在不在」「在哪个 schema」「向量列维度」
_TABLE_PROBE_SQL = """
SELECT n.nspname, c.relname, a.atttypmod
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid
     AND a.attname = 'embedding'
     AND NOT a.attisdropped
WHERE c.oid = to_regclass(%s)
"""

_EXTENSION_SCHEMA_SQL = """
SELECT n.nspname
FROM pg_extension e
JOIN pg_namespace n ON n.oid = e.extnamespace
WHERE e.extname = 'vector'
"""

# ⚠️ pgvector 的 `atttypmod` **直接就是维度**，不像 varchar 那样加 `VARHDRSZ(4)`。
# 2026-11-03 在 Supabase 实测：`embedding vector(1024)` → atttypmod = 1024。
# 曾按 varchar 习惯写 `atttypmod - 4`，会把 1024 维读成 1020 维，
# 于是真实 1024 维嵌入被判为「维度不一致」，写入与检索全部被自己的校验拦住。
# 无维度的 `vector` 列 atttypmod = -1。
_UNDIMENSIONED_TYPMOD = -1

_DEFAULT_TABLE = "knowledge_vectors"


def serialise_vector(vector: Iterable[float]) -> str:
    """把向量序列化成 pgvector 文本格式 `[1.0,0.0]`。

    用 `repr(float)` 而不是 `round`/`%.6f`：Python 的 float repr 是
    最短可往返表示，四舍五入会改变相似度结果。
    """
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


class PgVectorBackend:
    """pgvector 后端：单表 + `collection` 列区分知识库命名空间。"""

    name = "pgvector"

    def __init__(
        self,
        *,
        embedding_dimension: int,
        collection_name: str = "knowledge_base",
        dsn: str | None = None,
        table: str | None = None,
        pool: Any | None = None,
        auto_migrate: bool = False,
        pool_min_size: int | None = None,
        pool_max_size: int | None = None,
        pool_timeout: float | None = None,
        connect_timeout: int | None = None,
        statement_timeout_ms: int | None = None,
        sslmode: str | None = None,
        disable_prepared_statements: bool | None = None,
        index_type: str | None = None,
        hnsw_m: int | None = None,
        hnsw_ef_construction: int | None = None,
    ) -> None:
        self._embedding_dimension = int(embedding_dimension)
        self._collection = collection_name
        self._dsn = (dsn or "").strip()
        self._table = (table or "").strip() or None
        self._pool = pool
        self._owns_pool = pool is None
        # None = 未显式指定，运行时读配置（`RAG__PGVECTOR_AUTO_MIGRATE`）
        self._auto_migrate = auto_migrate
        self._pool_min_size = pool_min_size
        self._pool_max_size = pool_max_size
        self._pool_timeout = pool_timeout
        self._connect_timeout = connect_timeout
        self._statement_timeout_ms = statement_timeout_ms
        self._sslmode = sslmode
        self._disable_prepared = disable_prepared_statements
        # 空串 = 未显式指定，运行时读 `RAG__PGVECTOR_INDEX`
        self._index_type = (index_type or "").strip().lower()
        self._hnsw_m = hnsw_m
        self._hnsw_ef_construction = hnsw_ef_construction

        # 以下状态在首次真正访问数据库时填充（构造期绝不连接）
        self._prepared = False
        self._ext_schema = "public"
        self._table_ident: sql.Composable | None = None
        # 从 pg_attribute.atttypmod 读到的列维度（= 维度本身，不是 typmod-4）
        self._column_dimension: int | None = None

    # ------------------------------------------------------------------
    # 连接与 schema 探测
    # ------------------------------------------------------------------
    def _resolve_dsn(self) -> str:
        """DSN 优先级：显式参数 > `RAG__PGVECTOR_DSN` > 业务库 `DATABASE__URL`。

        复用业务库 DSN 的好处：迁移到 Supabase 后整库同源，少一套配置；
        仍允许单独指向另一个库（便于先迁向量、后迁业务表）。
        """
        dsn = self._dsn
        if not dsn:
            try:
                from app.core.config import get_settings

                settings = get_settings()
                dsn = (getattr(settings.rag, "pgvector_dsn", "") or "").strip()
                if not dsn:
                    dsn = settings.database.url
            except Exception as exc:  # pragma: no cover - 配置模块损坏时才会走到
                raise VectorStoreConfigError(f"读取 pgvector 配置失败：{exc}") from exc
        if not dsn:
            raise VectorStoreConfigError(
                "pgvector DSN 为空：请设置 RAG__PGVECTOR_DSN 或 DATABASE__URL"
            )
        if dsn.startswith("sqlite"):
            raise VectorStoreConfigError(
                "pgvector 后端不能使用 sqlite DSN；测试请走 Chroma 内存模式（persist_directory=''）"
            )
        return dsn

    def _ensure_pool(self) -> None:
        if self._pool is not None:
            return
        connect_timeout = self._connect_timeout or int(_setting("pgvector_connect_timeout", 5) or 5)
        statement_timeout_ms = self._statement_timeout_ms or int(
            _setting("pgvector_statement_timeout_ms", 15000) or 15000
        )
        sslmode = self._sslmode or str(_setting("pgvector_sslmode", "") or "")
        disable_prepared = (
            self._disable_prepared
            if self._disable_prepared is not None
            else bool(_setting("pgvector_disable_prepared_statements", True))
        )
        min_size = int(self._pool_min_size or _setting("pgvector_pool_min_size", 1) or 1)
        max_size = int(self._pool_max_size or _setting("pgvector_pool_max_size", 5) or 5)
        timeout = float(self._pool_timeout or _setting("pgvector_pool_timeout", 5.0) or 5.0)

        conninfo = to_sync_dsn(self._resolve_dsn())
        conninfo = with_options(
            conninfo,
            connect_timeout=connect_timeout,
            sslmode=sslmode or None,
            options=(
                f"-c statement_timeout={statement_timeout_ms}" if statement_timeout_ms else None
            ),
        )
        pool_kwargs: dict[str, Any] = {}
        if disable_prepared:
            # pgbouncer transaction 模式不支持 prepared statement
            pool_kwargs["prepare_threshold"] = None
        self._pool = ConnectionPool(
            conninfo=conninfo,
            min_size=min_size,
            max_size=max_size,
            timeout=timeout,
            kwargs=pool_kwargs,
            name="pgvector-vector-store",
            open=True,
        )
        logger.info(
            "pgvector_pool_created",
            **describe_dsn(conninfo),
            min_size=min_size,
            max_size=max_size,
            prepared_statements=not disable_prepared,
        )

    @contextmanager
    def _cursor(self) -> Iterator[Any]:
        """借一条连接并开游标，把驱动异常翻译成本项目异常。

        `_ensure_pool()` 也放在 try 里：连接串非法（`ProgrammingError`）与
        建池超时都属于「依赖不可用」，不能以裸驱动异常冒到 API 层。
        """
        try:
            self._ensure_pool()
            pool = self._pool
            if pool is None:  # pragma: no cover - _ensure_pool 之后不会为空
                raise VectorStoreConfigError("pgvector 连接池未初始化（内部错误）")
            with pool.connection() as conn, conn.cursor() as cur:
                yield cur
        except (psycopg.OperationalError, PoolTimeout) as exc:
            # 不把原始 DSN 带进日志/响应：redact 后只留可辨识的部分
            logger.error(
                "pgvector_unavailable",
                **describe_dsn(to_sync_dsn(self._dsn)),
                error=str(exc),
            )
            raise VectorStoreUnavailableError(f"pgvector 不可达：{redact_dsn(str(exc))}") from exc
        except psycopg.Error as exc:
            raise VectorStoreError(f"pgvector 操作失败：{exc}") from exc

    def _ensure_collection(self) -> None:
        """探测扩展、表与列维度（幂等，首次成功后不再查）。"""
        if self._prepared:
            return

        auto_migrate = (
            self._auto_migrate
            if self._auto_migrate is not None
            else bool(_setting("pgvector_auto_migrate", False))
        )

        with self._cursor() as cur:
            cur.execute(_EXTENSION_SCHEMA_SQL)
            row = cur.fetchone()
            if row is None and auto_migrate:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                cur.execute(_EXTENSION_SCHEMA_SQL)
                row = cur.fetchone()
            if row is None:
                raise VectorStoreConfigError(
                    "pgvector 扩展未安装：请在目标库执行 `CREATE EXTENSION IF NOT EXISTS vector`"
                )
            self._ext_schema = str(row[0])

            table = self._table or str(_setting("pgvector_table", _DEFAULT_TABLE))
            cur.execute(_TABLE_PROBE_SQL, (table,))
            probed = cur.fetchone()
            if probed is None and auto_migrate:
                self._create_table(cur, table)
                cur.execute(_TABLE_PROBE_SQL, (table,))
                probed = cur.fetchone()
            if probed is None:
                raise VectorStoreConfigError(
                    f"向量表 {table!r} 不存在或缺少 embedding 列：请先执行 `alembic upgrade head`"
                )

            schema, relname, typmod = str(probed[0]), str(probed[1]), int(probed[2])
            if typmod == _UNDIMENSIONED_TYPMOD:
                raise VectorStoreConfigError(
                    f"向量列 {schema}.{relname}.embedding 未声明维度："
                    f"请改用 `vector({self._embedding_dimension})`（HNSW 索引要求定长）"
                )
            if typmod != self._embedding_dimension:
                raise VectorDimensionMismatchError(
                    f"向量维度不一致：表 {schema}.{relname} 是 {typmod} 维，"
                    f"当前嵌入模型是 {self._embedding_dimension} 维"
                )

            self._table_ident = sql.Identifier(schema, relname)
            self._column_dimension = typmod
            self._prepared = True
            logger.info(
                "pgvector_ready",
                schema=schema,
                table=relname,
                dimension=typmod,
                collection=self._collection,
            )

    def _create_table(self, cur: Any, table: str) -> None:
        """仅集成测试/临时库使用：建表 + 索引（生产由 alembic 负责）。

        DDL 里的参数不能用 `%s`（utility 语句不支持占位符），维度与索引参数
        都先从 int 转换再以 `sql.Literal` 内联；表名/类型名走 Identifier。
        """
        ident = sql.Identifier(table)
        vector_type = sql.SQL("{}").format(sql.Identifier(self._ext_schema, "vector")) + sql.SQL(
            "({})"
        ).format(sql.Literal(self._embedding_dimension))
        cur.execute(
            sql.SQL(
                "CREATE TABLE IF NOT EXISTS {} ("
                " collection text NOT NULL,"
                " id text NOT NULL,"
                " embedding {} NOT NULL,"
                " document text NOT NULL DEFAULT '',"
                " doc_id text NOT NULL DEFAULT '',"
                " user_id text NOT NULL DEFAULT '',"
                " doc_source text NOT NULL DEFAULT '',"
                " chunk_index integer NOT NULL DEFAULT 0,"
                " start_offset integer NOT NULL DEFAULT 0,"
                " end_offset integer NOT NULL DEFAULT 0,"
                " metadata jsonb NOT NULL DEFAULT '{{}}'::jsonb,"
                " created_at timestamptz NOT NULL DEFAULT now(),"
                " updated_at timestamptz NOT NULL DEFAULT now(),"
                " PRIMARY KEY (collection, id))"
            ).format(ident, vector_type)
        )
        self._create_indexes(cur, ident)

    def _create_indexes(self, cur: Any, table: sql.Identifier) -> None:
        index_type = self._index_type or str(_setting("pgvector_index", "hnsw")).lower()
        # opclass 是扩展 schema 里的独立对象 `vector_cosine_ops`，
        # 不能写成 `"schema"."vector"_cosine_ops`（会被解析成两个标识符）
        opclass = sql.SQL("{}").format(sql.Identifier(self._ext_schema, "vector_cosine_ops"))
        if index_type == "hnsw":
            cur.execute(
                sql.SQL(
                    "CREATE INDEX IF NOT EXISTS ix_kv_embedding_hnsw ON {} "
                    "USING hnsw (embedding {}) WITH (m = {}, ef_construction = {})"
                ).format(
                    table,
                    opclass,
                    sql.Literal(int(self._hnsw_m or _setting("pgvector_hnsw_m", 16))),
                    sql.Literal(
                        int(
                            self._hnsw_ef_construction
                            or _setting("pgvector_hnsw_ef_construction", 64)
                        )
                    ),
                )
            )
        elif index_type == "ivfflat":
            cur.execute(
                sql.SQL(
                    "CREATE INDEX IF NOT EXISTS ix_kv_embedding_ivfflat ON {} "
                    "USING ivfflat (embedding {}) WITH (lists = {})"
                ).format(
                    table,
                    opclass,
                    sql.Literal(int(_setting("pgvector_ivfflat_lists", 100))),
                )
            )
        cur.execute(
            sql.SQL(
                "CREATE INDEX IF NOT EXISTS ix_kv_collection_doc ON {} (collection, doc_id)"
            ).format(table)
        )
        cur.execute(
            sql.SQL(
                "CREATE INDEX IF NOT EXISTS ix_kv_collection_user ON {} (collection, user_id)"
            ).format(table)
        )

    # ------------------------------------------------------------------
    # 写
    # ------------------------------------------------------------------
    def upsert(
        self,
        ids: list[str],
        vectors: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        if not ids:
            return
        total = len(ids)
        if not (len(vectors) == len(documents) == len(metadatas) == total):
            raise ValueError(
                "upsert 参数长度不一致："
                f"ids={len(ids)} vectors={len(vectors)} "
                f"documents={len(documents)} metadatas={len(metadatas)}"
            )
        # 维度先校验：错误信息要指出「表维度 vs 模型维度」，
        # 而不是让 pgvector 在 SQL 层抛一句 `expected 1024 dimensions`
        for vector in vectors:
            if len(vector) != self._embedding_dimension:
                raise VectorDimensionMismatchError(
                    f"向量维度不一致：收到 {len(vector)} 维，"
                    f"嵌入模型是 {self._embedding_dimension} 维"
                )

        self._ensure_collection()
        assert self._table_ident is not None
        statement = sql.SQL(
            "INSERT INTO {table}"
            " (collection, id, embedding, document, doc_id, user_id, doc_source,"
            "  chunk_index, start_offset, end_offset, metadata)"
            " VALUES (%s, %s, {cast}, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)"
            " ON CONFLICT (collection, id) DO UPDATE SET"
            "  embedding = EXCLUDED.embedding,"
            "  document = EXCLUDED.document,"
            "  doc_id = EXCLUDED.doc_id,"
            "  user_id = EXCLUDED.user_id,"
            "  doc_source = EXCLUDED.doc_source,"
            "  chunk_index = EXCLUDED.chunk_index,"
            "  start_offset = EXCLUDED.start_offset,"
            "  end_offset = EXCLUDED.end_offset,"
            "  metadata = EXCLUDED.metadata,"
            "  updated_at = now()"
        ).format(
            table=self._table_ident,
            cast=sql.SQL("::{}").format(sql.Identifier(self._ext_schema, "vector")),
        )

        rows = [
            (
                self._collection,
                chunk_id,
                serialise_vector(vector),
                document,
                metadata.get("doc_id", ""),
                metadata.get("user_id", ""),
                metadata.get("doc_source", ""),
                int(metadata.get("chunk_index", 0) or 0),
                int(metadata.get("start_offset", 0) or 0),
                int(metadata.get("end_offset", 0) or 0),
                json.dumps(metadata, ensure_ascii=False),
            )
            for chunk_id, vector, document, metadata in zip(
                ids, vectors, documents, metadatas, strict=True
            )
        ]
        with self._cursor() as cur:
            cur.executemany(statement, rows)

    # ------------------------------------------------------------------
    # 删 / 清空
    # ------------------------------------------------------------------
    def delete_by_doc(self, doc_id: str) -> int:
        self._ensure_collection()
        assert self._table_ident is not None
        statement = sql.SQL("DELETE FROM {table} WHERE collection = %s AND doc_id = %s").format(
            table=self._table_ident
        )
        with self._cursor() as cur:
            cur.execute(statement, (self._collection, doc_id))
            return int(cur.rowcount or 0)

    def reset(self) -> None:
        """清空当前 collection 的行。

        与 Chroma「删 collection 再建」可观察语义相同（条数归零、`is_ready=False`），
        但不做 DDL：应用账号不需要建表权限，也不会把索引一起丢掉重建。
        """
        self._ensure_collection()
        assert self._table_ident is not None
        statement = sql.SQL("DELETE FROM {table} WHERE collection = %s").format(
            table=self._table_ident
        )
        with self._cursor() as cur:
            cur.execute(statement, (self._collection,))

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------
    @property
    def column_dimension(self) -> int | None:
        """表里 `embedding` 列声明的维度（诊断用；无维度列为 None）。"""
        self._ensure_collection()
        return self._column_dimension

    def count(self) -> int:
        self._ensure_collection()
        assert self._table_ident is not None
        statement = sql.SQL("SELECT count(*) FROM {table} WHERE collection = %s").format(
            table=self._table_ident
        )
        with self._cursor() as cur:
            cur.execute(statement, (self._collection,))
            row = cur.fetchone()
        return int(row[0]) if row else 0

    def list_ids(self) -> list[str]:
        self._ensure_collection()
        assert self._table_ident is not None
        statement = sql.SQL("SELECT id FROM {table} WHERE collection = %s ORDER BY id").format(
            table=self._table_ident
        )
        with self._cursor() as cur:
            cur.execute(statement, (self._collection,))
            rows = cur.fetchall() or []
        return [str(row[0]) for row in rows]

    def query(
        self,
        vector: list[float],
        top_k: int,
        where: dict[str, Any] | None = None,
    ) -> list[VectorMatch]:
        if top_k <= 0:
            return []
        if len(vector) != self._embedding_dimension:
            raise VectorDimensionMismatchError(
                f"查询向量是 {len(vector)} 维，嵌入模型是 {self._embedding_dimension} 维"
            )

        self._ensure_collection()
        assert self._table_ident is not None

        where_sql, where_params = _compile_where(where)
        cast = sql.SQL("::{}").format(sql.Identifier(self._ext_schema, "vector"))
        statement = sql.SQL(
            "SELECT id, document, metadata, (embedding <=> {cast}) AS distance"
            " FROM {table}"
            " WHERE collection = %s{where}"
            " ORDER BY embedding <=> {cast}"
            " LIMIT %s"
        ).format(cast=cast, table=self._table_ident, where=where_sql)

        literal = serialise_vector(vector)
        params: list[Any] = [literal, self._collection, *where_params, literal, int(top_k)]
        with self._cursor() as cur:
            cur.execute(statement, params)
            rows = cur.fetchall() or []

        return [
            VectorMatch(
                chunk_id=str(row[0]),
                text=str(row[1] or ""),
                metadata=dict(row[2] or {}),
                distance=float(row[3]),
            )
            for row in rows
        ]

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def ping(self) -> bool:
        """轻量探活：在已建连接池上执行 `SELECT 1`（不碰向量表、不做 schema 校验）。

        为什么单独一个方法：`count()` 会走 `_ensure_collection()`，
        表/维度没准备好时抛的是配置错误；健康探针只需要回答「数据库通不通」。
        """
        try:
            with self._cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        except VectorStoreError:
            return False
        return True

    def close(self) -> None:
        """关闭自建连接池（注入的池由调用方负责）。"""
        if self._owns_pool and self._pool is not None:
            self._pool.close()
            self._pool = None
            self._prepared = False


def _compile_where(where: dict[str, Any] | None) -> tuple[sql.Composed | sql.SQL, list[Any]]:
    """把 Chroma 风格的 where 字典编译成参数化 SQL 片段。

    支持：标量等值、`$eq`、`$ne`、`$in`、`$nin`。
    未知键或未知算子**直接报错**：静默忽略会让过滤失效，
    在 `user_id` 场景下等于跨用户串数据。

    返回 `(" AND col = %s ...", [值...])`，无过滤时返回空片段。
    """
    if not where:
        return sql.SQL(""), []

    clauses: list[sql.Composable] = []
    params: list[Any] = []
    for key, value in where.items():
        column = WHERE_COLUMNS.get(key)
        if column is None:
            raise ValueError(f"不支持的过滤键 {key!r}（可选：{', '.join(sorted(WHERE_COLUMNS))}）")
        ident = sql.Identifier(column)

        if isinstance(value, dict):
            if len(value) != 1:
                raise ValueError(f"过滤条件 {key!r} 只能包含一个算子，收到：{sorted(value)}")
            operator, operand = next(iter(value.items()))
            if operator == "$eq":
                clauses.append(sql.SQL("{} = %s").format(ident))
                params.append(operand)
            elif operator == "$ne":
                clauses.append(sql.SQL("{} <> %s").format(ident))
                params.append(operand)
            elif operator == "$in":
                clauses.append(sql.SQL("{} = ANY(%s)").format(ident))
                params.append(list(operand))
            elif operator == "$nin":
                clauses.append(sql.SQL("{} <> ALL(%s)").format(ident))
                params.append(list(operand))
            else:
                raise ValueError(f"不支持的过滤算子 {operator!r}（可选：$eq / $ne / $in / $nin）")
        else:
            clauses.append(sql.SQL("{} = %s").format(ident))
            params.append(value)

    combined = sql.SQL(" AND ").join(clauses)
    return sql.SQL(" AND ") + combined, params


def _setting(name: str, default: Any) -> Any:
    """读配置项，读不到就用默认值（配置模块不可用时不炸）。"""
    try:
        from app.core.config import get_settings

        return getattr(get_settings().rag, name, default)
    except Exception:  # pragma: no cover
        return default
