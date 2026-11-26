"""E2E 种子脚本环境门禁测试。

确保 `seed_all()` 与 `main()` 在生产环境 (`APP__ENV=production`) 下立即拒跑，
不会读写数据库或覆写管理员凭据；同时在测试环境正常放行进入参数校验。
"""

from __future__ import annotations

import pytest

from app.core.config import get_settings
from scripts import seed_e2e_data


class TestSeedE2EDataGuard:
    """E2E 种子脚本非生产环境门禁测试。"""

    @pytest.mark.asyncio
    async def test_rejects_production_env_lowercase(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """APP__ENV=production 时必须在第一时间抛 RuntimeError 拒跑。"""
        monkeypatch.setenv("APP__ENV", "production")
        monkeypatch.delenv("E2E_DEMO_PASSWORD", raising=False)
        monkeypatch.delenv("E2E_SECONDARY_PASSWORD", raising=False)
        get_settings.cache_clear()
        try:
            with pytest.raises(RuntimeError, match=r"production|禁止"):
                await seed_e2e_data.seed_all()
        finally:
            get_settings.cache_clear()

    def test_rejects_production_env_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """APP__ENV=PRODUCTION（大小写混合）同样必须被拦截，且 main() 异常终止。"""
        monkeypatch.setenv("APP__ENV", "PRODUCTION")
        monkeypatch.delenv("E2E_DEMO_PASSWORD", raising=False)
        get_settings.cache_clear()
        try:
            with pytest.raises(RuntimeError, match=r"production|禁止"):
                seed_e2e_data.main()
        finally:
            get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_allows_test_env_past_guard(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """APP__ENV=test 时不应被环境门禁拦截（应进入后续密码环境变量校验）。"""
        monkeypatch.setenv("APP__ENV", "test")
        monkeypatch.delenv("E2E_DEMO_PASSWORD", raising=False)
        get_settings.cache_clear()
        try:
            with pytest.raises(SystemExit):
                await seed_e2e_data.seed_all()
        finally:
            get_settings.cache_clear()
