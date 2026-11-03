"""数据库引擎参数：本地 postgres 与 Supabase 共用同一套构造逻辑。

为什么抽出来：`app/db/session.py`（应用）与 `alembic/env.py`（迁移）
必须用**完全相同**的连接参数，否则会出现「应用能连、迁移连不上」
或者「迁移建的表搜不到」这类只在部署时暴露的问题。

Supabase（transaction pooler）下两个参数的正确传法（SQLAlchemy 2.0.51 实测）：

| 参数 | 传法 | 为什么 |
| --- | --- | --- |
| `prepared_statement_cache_size=0`（SQLAlchemy 方言层） | **URL 查询串** | 只有 URL 查询串会被方言识别；作为 engine kwarg 会报 `Invalid argument(s) ... sent to create_engine()` |
| `statement_cache_size=0`（asyncpg 层） | `connect_args`，**必须是 int** | 走 URL 查询串会以字符串传给 asyncpg → `TypeError: '<' not supported between instances of 'str' and 'int'` |

只关任何一层都不够：pooler 复用服务端连接后，
`prepared statement "__asyncpg_stmt_N__" does not exist` / `already exists` 都会出现。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.engine import URL, make_url

from app.core.config import settings


def prepare_database_url(url: str | None = None) -> URL:
    """构造连接 URL；方言层缓存开关只能放这里（见模块 docstring）。"""
    parsed = make_url(url or settings.database.url)
    if settings.database.disable_prepared_statements:
        parsed = parsed.update_query_dict({"prepared_statement_cache_size": "0"})
    return parsed


def database_url_for_alembic(url: str | None = None) -> str:
    """alembic 需要字符串 URL；`str(URL)` 会把密码打码，必须显式还原。"""
    return prepare_database_url(url).render_as_string(hide_password=False)


def database_connect_args() -> dict[str, Any]:
    """asyncpg 侧的 connect_args（`statement_cache_size` 必须是 int）。"""
    args: dict[str, Any] = {}
    if settings.database.ssl_require:
        # asyncpg 的 `ssl` 支持 sslmode 字符串（require / verify-ca / verify-full）
        args["ssl"] = "require"
    if settings.database.disable_prepared_statements:
        args["statement_cache_size"] = 0
    return args
