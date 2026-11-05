"""存储配置解析：环境变量必须能覆盖 yaml 基线，且默认仍是 local。

为什么单独测：`STORAGE__*` 一旦没被正确解析，容器里会静默回落到 local
（上传照旧写本地盘），排查起来非常费时——这与当初 CORS 变量没透传
到 compose 白名单是同一类问题。
"""

from __future__ import annotations

from app.core.config import Settings, StorageConfig


class TestStorageDefaults:
    def test_default_backend_is_local(self) -> None:
        """没配任何变量时必须回退 local：开源部署与单测都不需要云凭据。"""
        settings = Settings()

        assert settings.storage.backend == "local"
        assert settings.storage.presign_expires_seconds == 900


class TestStorageEnvOverride:
    def test_env_overrides_yaml(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.setenv("STORAGE__BACKEND", "s3")
        monkeypatch.setenv("STORAGE__S3_BUCKET", "veripaper-storage")
        monkeypatch.setenv("STORAGE__S3_ENDPOINT", "https://s3-cn-east-1.qiniucs.com")
        monkeypatch.setenv("STORAGE__S3_REGION", "cn-east-1")
        monkeypatch.setenv("STORAGE__PRESIGN_EXPIRES_SECONDS", "60")

        settings = Settings()

        assert settings.storage.backend == "s3"
        assert settings.storage.s3_bucket == "veripaper-storage"
        assert settings.storage.s3_endpoint == "https://s3-cn-east-1.qiniucs.com"
        assert settings.storage.s3_region == "cn-east-1"
        assert settings.storage.presign_expires_seconds == 60


class TestStorageConfigModel:
    def test_addressing_style_defaults_to_virtual(self) -> None:
        """七牛只接受 virtual-hosted，默认值必须是 virtual。"""
        assert StorageConfig().s3_addressing_style == "virtual"
