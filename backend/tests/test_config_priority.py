"""配置优先级缺陷回归（P1，AGENTS.md §6.1 记录项）。

## 缺陷

`get_settings()` 旧实现是 `Settings(**defaults)`——把 `default.yaml` 的内容当作
**init 参数**传入。pydantic-settings 的优先级是
`init 参数 > 环境变量 > .env > 默认值`，所以 YAML 反而**压过**了 `.env` 与环境变量：

    REFERENCE_VERIFICATION__CONTACT_EMAIL=me@example.org  # 不生效
    contact_email: ""                                     # YAML 里的空值赢

后果（已实际发生）：Issue #3 的 `contact_email` 只能靠手改 yaml 注入，
无法通过部署环境变量配置；任何「用环境变量覆盖 yaml 默认值」的运维手段都失效。

## 正确语义

`config/default.yaml` 只是**默认值基线**，必须让 `.env` 与环境变量能覆盖它。
本文件锁定：

1. 环境变量覆盖 yaml 顶层标量（`JWT_SECRET_KEY`）；
2. 环境变量覆盖 yaml **嵌套**字段（`REFERENCE_VERIFICATION__CONTACT_EMAIL`、
   `LLM__FALLBACK_CHAIN` 这类 `__` 分隔）；
3. 环境变量覆盖 yaml 子模型里的列表/数值；
4. 没有环境变量时，yaml 值仍然生效（不能为了修覆盖而丢掉 yaml 基线）；
5. 代码里未在 yaml 出现的字段仍取类默认值。
"""

from __future__ import annotations

import textwrap

import pytest

#: 临时 yaml 基线：故意给出一组"非默认"值，便于断言谁赢
YAML_BASELINE = textwrap.dedent("""
    app:
      env: "production"
      debug: true
    database:
      url: "postgresql+asyncpg://yaml:yaml@yamlhost:5432/yaml_db"
    reference_verification:
      contact_email: ""
      max_concurrency: 2
      verify_offline_tasks: false
    jwt_secret_key: "yaml-secret"
    """)


@pytest.fixture
def yaml_config(tmp_path, monkeypatch):
    """把 DEFAULT_CONFIG_PATH 指向临时 yaml，并清空 settings 缓存。"""
    from app.core import config as config_module

    path = tmp_path / "default.yaml"
    path.write_text(YAML_BASELINE, encoding="utf-8")
    monkeypatch.setattr(config_module, "DEFAULT_CONFIG_PATH", path)
    config_module.get_settings.cache_clear()
    yield path
    config_module.get_settings.cache_clear()


class TestEnvOverridesYaml:
    def test_env_overrides_yaml_top_level_scalar(self, yaml_config, monkeypatch) -> None:
        """环境变量必须覆盖 yaml 顶层标量（缺陷下 yaml 的 "yaml-secret" 会赢）。"""
        from app.core.config import get_settings

        monkeypatch.setenv("JWT_SECRET_KEY", "env-secret-wins")

        settings = get_settings()

        assert settings.jwt_secret_key == "env-secret-wins"

    def test_env_overrides_yaml_nested_field(self, yaml_config, monkeypatch) -> None:
        """Issue #3 实际阻塞点：contact_email 必须能用环境变量注入。"""
        from app.core.config import get_settings

        monkeypatch.setenv("REFERENCE_VERIFICATION__CONTACT_EMAIL", "ops@example.org")

        settings = get_settings()

        assert settings.reference_verification.contact_email == "ops@example.org"

    def test_env_overrides_yaml_nested_numeric(self, yaml_config, monkeypatch) -> None:
        from app.core.config import get_settings

        monkeypatch.setenv("REFERENCE_VERIFICATION__MAX_CONCURRENCY", "8")

        settings = get_settings()

        assert settings.reference_verification.max_concurrency == 8

    def test_env_overrides_yaml_nested_bool(self, yaml_config, monkeypatch) -> None:
        """布尔字段（true/false 字符串解析）也要能覆盖。"""
        from app.core.config import get_settings

        monkeypatch.setenv("REFERENCE_VERIFICATION__VERIFY_OFFLINE_TASKS", "true")

        settings = get_settings()

        assert settings.reference_verification.verify_offline_tasks is True

    def test_env_overrides_yaml_nested_list(self, yaml_config, monkeypatch) -> None:
        """列表型嵌套字段（fallback_chain 这类）覆盖语义。"""
        from app.core.config import get_settings

        monkeypatch.setenv(
            "LLM__FALLBACK_CHAIN",
            '[{"provider": "deepseek", "model": "deepseek-chat"}]',
        )

        settings = get_settings()

        assert len(settings.llm.fallback_chain) == 1
        assert settings.llm.fallback_chain[0].provider == "deepseek"


class TestYamlStillProvidesDefaults:
    def test_yaml_value_used_when_no_env(self, yaml_config, monkeypatch) -> None:
        """没有环境变量时 yaml 必须仍然生效（不能为修覆盖而丢基线）。"""
        from app.core.config import get_settings

        monkeypatch.delenv("REFERENCE_VERIFICATION__MAX_CONCURRENCY", raising=False)

        settings = get_settings()

        assert settings.reference_verification.max_concurrency == 2
        assert settings.app.env == "production"

    def test_yaml_nested_dict_merges_with_class_defaults(self, yaml_config) -> None:
        """yaml 只给部分字段时，其余字段回落到类默认值（不是空/None）。"""
        from app.core.config import get_settings

        settings = get_settings()

        # yaml 只写了 max_concurrency 与 contact_email
        assert settings.reference_verification.max_concurrency == 2
        assert settings.reference_verification.similarity_threshold == pytest.approx(0.90)

    def test_missing_yaml_file_falls_back_to_class_defaults(self, tmp_path, monkeypatch) -> None:
        """yaml 不存在时不能崩，全部用类默认值 + 环境变量。"""
        from app.core import config as config_module

        monkeypatch.setattr(config_module, "DEFAULT_CONFIG_PATH", tmp_path / "missing.yaml")
        config_module.get_settings.cache_clear()
        try:
            settings = config_module.get_settings()
            assert settings.app.env in ("development", "test", "staging", "production")
            assert settings.jwt_algorithm == "HS256"
        finally:
            config_module.get_settings.cache_clear()


class TestRealYamlStillWorks:
    def test_repository_yaml_is_loaded(self) -> None:
        """回归：仓库真实 default.yaml 仍被读到（fallback_chain 非空是既有断言）。

        **不写死首项厂商**（2026-10-27）：原断言 `fallback_chain[0].provider == "deepseek"`
        把「YAML 加载成功」这条性质绑在了具体厂商上；切默认模型后必然误报。
        该守的性质是「链条非空且每项完整」。
        """
        from app.core.config import get_settings

        settings = get_settings()

        assert settings.llm.fallback_chain, "真实 default.yaml 的 fallback_chain 未被加载"
        for item in settings.llm.fallback_chain:
            assert item.provider, f"降级链项缺 provider: {item}"
            assert item.model, f"降级链项缺 model: {item}"
