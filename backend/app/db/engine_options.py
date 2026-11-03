"""数据库引擎参数：本地 postgres 与 Supabase 共用同一套构造逻辑。

为什么抽出来：`app/db/session.py`（应用）与 `alembic/env.py`（迁移）
必须用**完全相同**的连接参数，否则会出现「应用能连、迁移连不上」
或者「迁移建的表搜不到」这类只在部署时暴露的问题。

Supabase 的三条硬约束：

1. **TLS**：`ssl_require=true` 时给 asyncpg 传 `ssl="require"`；
2. **transaction pooler（6543）不支持 prepared statement**：
   `disable_prepared_statements=true` 时关闭**两层**缓存——
   asyncpg 的 `statement_cache_size=0`（connect_args）与 SQLAlchemy 方言的
   `prepared_statement_cache_size=0`（engine kwarg）。
   只关一层仍会在复用连接后报 `prepared statement "__asyncpg_stmt_x__" does not exist`；
3. **这两个参数不能写进 URL 的查询串**：SQLAlchemy 会把查询参数原样当字符串传给
   asyncpg，而 asyncpg 需要整数 →
   `TypeError: '<' not supported between instances of 'str' and 'int'`
   （2026-11-03 切换时真机踩到，后端直接起不来）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.engine import URL, make_url

from app.core.config import settings


def prepare_database_url(url: str | None = None) -> URL:
    """构造连接 URL。

    刻意**不**往里塞 `statement_cache_size` 之类的参数：它们必须走
    `connect_args` / engine kwarg（见模块 docstring 第 3 条）。
    """
    return make_url(url or settings.database.url)


def database_url_for_alembic(url: str | None = None) -> str:
    """alembic 需要字符串 URL；`str(URL)` 会把密码打码，必须显式还原。"""
    return prepare_database_url(url).render_as_string(hide_password=False)


def database_connect_args() -> dict[str, Any]:
    """asyncpg 侧的 connect_args。"""
    args: dict[str, Any] = {}
    if settings.database.ssl_require:
        # asyncpg 的 `ssl` 支持 sslmode 字符串（require / verify-ca / verify-full）
        args["ssl"] = "require"
    if settings.database.disable_prepared_statements:
        # asyncpg 语句缓存（必须在 connect_args 里，且是 int）
        args["statement_cache_size"] = 0
    return args


def database_engine_kwargs() -> dict[str, Any]:
    """SQLAlchemy 方言层的引擎参数（不是 connect_args）。"""
    kwargs: dict[str, Any] = {}
    if settings.database.disable_prepared_statements:
        # SQLAlchemy asyncpg 方言的预编译语句缓存
        kwargs["prepared_statement_cache_size"] = 0
    return kwargs
