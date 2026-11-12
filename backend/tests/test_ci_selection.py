"""锁定 CI 选片规则（`scripts/ci/select_tests.py`）。

为什么这套规则值得测试：选片器**漏掉一个测试**的后果是"CI 绿了但没真验"，
比直接失败更危险。所以这里把每条规则的反面也钉住——
该全量的必须判全量，未映射的新模块必须保守升级成全量。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

CI_DIR = Path(__file__).resolve().parents[2] / "scripts" / "ci"
BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def selector():
    spec = importlib.util.spec_from_file_location("ci_select_tests", CI_DIR / "select_tests.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["ci_select_tests"] = module
    spec.loader.exec_module(module)
    return module


class TestModeSelection:
    def test_docs_only_runs_nothing(self, selector) -> None:
        selection = selector.select(["docs/runbooks/ecs-deployment.md", "AGENTS.md"])
        assert selection.mode == "none"

    def test_frontend_only_skips_backend(self, selector) -> None:
        assert selector.select(["frontend/src/views/Tasks.vue"]).mode == "none"

    def test_auth_endpoint_picks_auth_tests(self, selector) -> None:
        selection = selector.select(["backend/app/api/v1/endpoints/auth.py"])
        assert selection.mode == "selected"
        assert "tests/test_auth.py" in selection.tests
        assert "tests/test_refresh_rotation.py" in selection.tests

    def test_deploy_change_picks_config_lock_tests(self, selector) -> None:
        selection = selector.select(["deploy/ecs/nginx-api-tls.conf"])
        assert "tests/test_gateway_security_headers.py" in selection.tests
        assert "tests/test_firewall_cloudflare_only.py" in selection.tests

    def test_ops_change_picks_watchdog_tests(self, selector) -> None:
        selection = selector.select(["ops/watchdog.py"])
        assert "tests/test_ops_watchdog.py" in selection.tests

    @pytest.mark.parametrize(
        "path",
        [
            "backend/app/core/config.py",
            "backend/app/db/models/user.py",
            "backend/app/main.py",
            "backend/alembic/versions/abc_add_x.py",
            "backend/pyproject.toml",
            "backend/tests/conftest.py",
            "config/default.yaml",
        ],
    )
    def test_global_premises_force_full(self, selector, path: str) -> None:
        assert selector.select([path]).mode == "full"

    def test_unmapped_backend_module_is_conservative(self, selector) -> None:
        """新增模块没登记映射时宁可全量——漏跑的代价远大于多跑十分钟。"""
        assert selector.select(["backend/app/brandnew/service.py"]).mode == "full"

    def test_changed_test_file_runs_itself(self, selector) -> None:
        selection = selector.select(["backend/tests/test_auth.py"])
        assert "tests/test_auth.py" in selection.tests

    def test_empty_change_set_falls_back_to_smoke(self, selector) -> None:
        selection = selector.select([])
        assert selection.mode == "selected"
        assert set(selection.tests) == set(selector.ALWAYS_TESTS)

    def test_mixed_docs_and_code_still_runs_tests(self, selector) -> None:
        selection = selector.select(["docs/x.md", "backend/app/api/v1/endpoints/auth.py"])
        assert selection.mode == "selected"


class TestMappingIntegrity:
    """映射表里写错路径不会报错、只会静默少跑——所以逐条校验存在性。"""

    def test_always_tests_exist(self, selector) -> None:
        for item in selector.ALWAYS_TESTS:
            assert (BACKEND / item).is_file(), f"冒烟集里的 {item} 不存在"

    def test_literal_mappings_exist(self, selector) -> None:
        missing: list[str] = []
        for _pattern, tests in selector.AREA_TESTS:
            for item in tests:
                if "*" in item:
                    if not list(BACKEND.glob(item)):
                        missing.append(item)
                elif not (BACKEND / item).is_file():
                    missing.append(item)
        assert not missing, f"映射表指向不存在的测试：{missing}"

    def test_glob_expansion_returns_real_files(self, selector) -> None:
        expanded = selector.expand_globs(["tests/test_task*.py"], str(BACKEND))
        assert expanded, "glob 没展开出任何文件"
        for item in expanded:
            assert (BACKEND / item).is_file()
