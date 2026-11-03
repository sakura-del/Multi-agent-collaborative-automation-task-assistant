"""数据库引擎参数：本地 postgres 与 Supabase 共用同一套构造逻辑。

为什么抽出来：`app/db/session.py`（应用）与 `alembic/env.py`（迁移）
必须用**完全相同**的连接参数，否则会出现「应用能连、迁移连不上」
或者「迁移建的表搜不到」这类只在部署时暴露的问题。

Supabase 的两条硬约束：
- **TLS**：默认要求 SSL，`ssl_require=true` 时给 asyncpg 传 `ssl="require"`；
- **transaction pooler（6543）不支持 prepared statement**：
  `disable_prepared_statements=true` 时同时关闭两层缓存——
  asyncpg 的 `statement_cache_size=0` 与 SQLAlchemy 方言的
  `prepared_statement_cache_size=0`。只关一层仍会在复用连接后报
  `prepared statement "__asyncpg_stmt_x__" does not exist`。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.engine import URL, make_url

from app.core.config import settings


def prepare_database_url(url: str | None = None) -> URL:
    """构造最终连接 URL（含关闭 prepared statement 的查询参数）。"""
    parsed = make_url(url or settings.database.url)
    if settings.database.disable_prepared_statements:
        # 两个参数都由 SQLAlchemy 的 asyncpg 方言解析并透传给驱动
        parsed = parsed.update_query_dict(
            {
                "prepared_statement_cache_size": "0",
                "statement_cache_size": "0",
            }
        )
    return parsed


def database_url_for_alembic(url: str | None = None) -> str:
    """alembic 需要字符串 URL；`str(URL)` 会把密码打码，必须显式还原。"""
    return prepare_database_url(url).render_as_string(hide_password=False)


def database_connect_args() -> dict[str, Any]:
    """按配置生成 asyncpg 的 connect_args。"""
    args: dict[str, Any] = {}
    if settings.database.ssl_require:
        # asyncpg 的 `ssl` 支持 sslmode 字符串（require / verify-ca / verify-full）
        args["ssl"] = "require"
    return args
