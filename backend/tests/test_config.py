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
        import os

        # conftest 或 CI 已通过环境变量设置 JWT_SECRET_KEY，
        # settings 应读取到该值（不硬编码具体值，兼容不同环境的密钥）
        expected = os.environ.get("JWT_SECRET_KEY")
        assert expected is not None
        assert app_settings.jwt_secret_key == expected

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


class TestProjectRootResolution:
    """配置根目录解析（Plan 5 Task 8 实测：容器内曾解析到 "/" 导致 fallback_chain 为空）。"""

    def test_repository_root_contains_config(self) -> None:
        from app.core.config import DEFAULT_CONFIG_PATH, PROJECT_ROOT

        assert (PROJECT_ROOT / "config").is_dir()
        assert DEFAULT_CONFIG_PATH.is_file()

    def test_fallback_chain_is_loaded_from_yaml(self) -> None:
        from app.core.config import get_settings

        chain = get_settings().llm.fallback_chain
        assert chain, "fallback_chain 不应为空（YAML 未被读到）"
        assert chain[0].provider == "deepseek"

    def test_container_layout_resolves_to_app_dir(self, tmp_path, monkeypatch) -> None:
        """模拟容器布局 /app/app/core/config.py + /app/config/default.yaml。"""

        # 构造 /app 等价结构
        app_dir = tmp_path / "app"
        core_dir = app_dir / "app" / "core"
        core_dir.mkdir(parents=True)
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "default.yaml").write_text("llm: {}\n", encoding="utf-8")
        fake = core_dir / "config.py"
        fake.write_text("", encoding="utf-8")

        # 直接验证解析逻辑：config 与 app 同级时应命中 tmp_path
        file_dir = fake.resolve().parent
        candidates = [
            file_dir.parents[2] if len(file_dir.parents) > 2 else None,
            file_dir.parents[1],
            file_dir.parents[0],
        ]
        resolved = next(
            (c for c in candidates if c and (c / "config").is_dir()),
            candidates[0] or file_dir,
        )
        assert resolved == tmp_path.resolve()
