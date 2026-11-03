"""数据库连接参数（Supabase 迁移）回归。

锁住三条容易在部署时才发现的行为：

1. 默认（本地 postgres）不加任何额外参数，避免影响既有部署；
2. `disable_prepared_statements=true` 时**两层**缓存都要关——
   只关一层仍会在 pgbouncer 复用连接后报 `prepared statement does not exist`；
   且**不能**通过 URL 查询串传：SQLAlchemy 会把查询参数当字符串传给 asyncpg，
   直接 `TypeError: '<' not supported between instances of 'str' and 'int'`
   （2026-11-03 切换时真机踩到，后端起不来）；
3. 给 alembic 的 URL 必须带真实密码（`str(URL)` 会打码成 `***`，
   打码后的串连不上库，而报错信息只说「认证失败」，极难排查）。
"""

from __future__ import annotations

from app.core.config import settings
from app.db.engine_options import (
    database_connect_args,
    database_engine_kwargs,
    database_url_for_alembic,
    prepare_database_url,
)


class TestPrepareDatabaseUrl:
    def test_default_adds_nothing(self, monkeypatch) -> None:
        monkeypatch.setattr(settings.database, "disable_prepared_statements", False)
        url = prepare_database_url("postgresql+asyncpg://u:p@h:5432/db")
        assert url.query == {}

    def test_disabling_does_not_touch_url_query(self, monkeypatch) -> None:
        """缓存参数必须走 connect_args / engine kwarg，不能塞进 URL。"""
        monkeypatch.setattr(settings.database, "disable_prepared_statements", True)
        url = prepare_database_url("postgresql+asyncpg://u:p@h:6543/db")
        assert url.query == {}

    def test_keeps_driver_prefix(self) -> None:
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
        monkeypatch.setattr(settings.database, "disable_prepared_statements", False)
        assert database_connect_args() == {}

    def test_ssl_require_maps_to_asyncpg_sslmode(self, monkeypatch) -> None:
        monkeypatch.setattr(settings.database, "ssl_require", True)
        monkeypatch.setattr(settings.database, "disable_prepared_statements", False)
        assert database_connect_args() == {"ssl": "require"}

    def test_statement_cache_size_is_int_zero(self, monkeypatch) -> None:
        """asyncpg 要求 int：以字符串传入会 TypeError（部署实测）。"""
        monkeypatch.setattr(settings.database, "disable_prepared_statements", True)
        args = database_connect_args()
        assert args["statement_cache_size"] == 0
        assert isinstance(args["statement_cache_size"], int)


class TestEngineKwargs:
    def test_dialect_cache_disabled_when_requested(self, monkeypatch) -> None:
        monkeypatch.setattr(settings.database, "disable_prepared_statements", True)
        assert database_engine_kwargs() == {"prepared_statement_cache_size": 0}

    def test_default_is_empty(self, monkeypatch) -> None:
        monkeypatch.setattr(settings.database, "disable_prepared_statements", False)
        assert database_engine_kwargs() == {}

    def test_both_layers_disabled_together(self, monkeypatch) -> None:
        """只关一层仍会报 prepared statement 不存在/已存在（两种都踩过）。"""
        monkeypatch.setattr(settings.database, "disable_prepared_statements", True)
        assert "statement_cache_size" in database_connect_args()
        assert "prepared_statement_cache_size" in database_engine_kwargs()
