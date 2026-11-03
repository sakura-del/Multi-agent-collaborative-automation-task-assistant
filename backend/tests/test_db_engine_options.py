"""数据库连接参数（Supabase 迁移）回归。

锁住三条容易在部署时才发现的行为：

1. 默认（本地 postgres）不加任何额外参数，避免影响既有部署；
2. `disable_prepared_statements=true` 时**两层**缓存都要关——
   只关一层仍会在 pgbouncer 复用连接后报 `prepared statement does not exist`；
3. 给 alembic 的 URL 必须带真实密码（`str(URL)` 会打码成 `***`，
   打码后的串连不上库，而报错信息只说「认证失败」，极难排查）。
"""

from __future__ import annotations

from app.core.config import settings
from app.db.engine_options import (
    database_connect_args,
    database_url_for_alembic,
    prepare_database_url,
)


class TestPrepareDatabaseUrl:
    def test_default_adds_nothing(self, monkeypatch) -> None:
        monkeypatch.setattr(settings.database, "disable_prepared_statements", False)
        url = prepare_database_url("postgresql+asyncpg://u:p@h:5432/db")
        assert url.query == {}

    def test_disables_both_statement_caches(self, monkeypatch) -> None:
        monkeypatch.setattr(settings.database, "disable_prepared_statements", True)
        url = prepare_database_url("postgresql+asyncpg://u:p@h:6543/db")

        # asyncpg 侧与 SQLAlchemy 方言侧都必须为 0
        assert url.query["statement_cache_size"] == "0"
        assert url.query["prepared_statement_cache_size"] == "0"

    def test_keeps_driver_prefix(self, monkeypatch) -> None:
        monkeypatch.setattr(settings.database, "disable_prepared_statements", True)
        url = prepare_database_url("postgresql+asyncpg://u:p@h:6543/db")
        assert url.drivername == "postgresql+asyncpg"

    def test_alembic_url_keeps_password(self, monkeypatch) -> None:
        monkeypatch.setattr(settings.database, "disable_prepared_statements", True)
        rendered = database_url_for_alembic("postgresql+asyncpg://u:secret@h:6543/db")
        # 打码后的 URL 无法连库；这里必须能读到真实密码
        assert "secret" in rendered
        assert "***" not in rendered


class TestConnectArgs:
    def test_default_no_ssl(self, monkeypatch) -> None:
        monkeypatch.setattr(settings.database, "ssl_require", False)
        assert database_connect_args() == {}

    def test_ssl_require_maps_to_asyncpg_sslmode(self, monkeypatch) -> None:
        monkeypatch.setattr(settings.database, "ssl_require", True)
        assert database_connect_args() == {"ssl": "require"}
