"""config 模块测试：配置加载与环境变量覆盖。

阶段1验收：核心模块覆盖率 ≥70%。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch


class TestConfigLoading:
    """配置系统：yaml 默认值 + 环境变量覆盖。"""

    def test_settings_has_app_config(self, app_settings) -> None:
        """Settings 应包含 app 子配置。"""
        assert hasattr(app_settings, "app")
        assert app_settings.app.env in ("development", "test", "staging", "production")

    def test_settings_has_database_config(self, app_settings) -> None:
        """Settings 应包含 database 子配置与 url。"""
        assert hasattr(app_settings, "database")
        assert "url" in app_settings.database.model_fields

    def test_settings_has_jwt_config(self, app_settings) -> None:
        """Settings 应包含 JWT 配置字段。"""
        assert hasattr(app_settings, "jwt_secret_key")
        assert hasattr(app_settings, "jwt_algorithm")
        assert app_settings.jwt_algorithm == "HS256"

    def test_env_override_database_url(self, app_settings) -> None:
        """环境变量 DATABASE__URL 应覆盖默认数据库地址。"""
        # conftest 已设置 DATABASE__URL=sqlite+aiosqlite:///:memory:
        assert "sqlite" in app_settings.database.url

    def test_env_override_jwt_secret(self, app_settings) -> None:
        """环境变量 JWT_SECRET_KEY 应覆盖默认密钥。"""
        assert app_settings.jwt_secret_key == "test-secret-key-not-for-production"

    def test_yaml_defaults_loaded(self) -> None:
        """default.yaml 存在时应被加载为默认配置基线。"""
        from app.core.config import DEFAULT_CONFIG_PATH, _load_yaml_defaults

        if DEFAULT_CONFIG_PATH.exists():
            defaults = _load_yaml_defaults()
            assert isinstance(defaults, dict)
            # default.yaml 应至少包含 app 或 database 节
            assert "app" in defaults or "database" in defaults

    def test_load_yaml_returns_empty_for_missing_file(self) -> None:
        """yaml 文件不存在时应返回空字典。"""
        from app.core.config import _load_yaml_defaults

        with patch("app.core.config.DEFAULT_CONFIG_PATH", Path("/nonexistent.yaml")):
            assert _load_yaml_defaults() == {}

    def test_redis_config_defaults(self, app_settings) -> None:
        """Redis 配置应有默认值。"""
        assert app_settings.redis.max_connections > 0
        assert app_settings.redis.default_ttl > 0

    def test_llm_cost_budget_defaults(self, app_settings) -> None:
        """LLM 成本预算应有默认值。"""
        assert app_settings.llm.cost_budget.daily_token_limit > 0
        assert 0 < app_settings.llm.cost_budget.alert_threshold <= 1

    def test_cors_config_defaults(self, app_settings) -> None:
        """CORS 配置应包含默认允许的前端源。"""
        assert isinstance(app_settings.cors.allow_origins, list)
        assert len(app_settings.cors.allow_origins) > 0
