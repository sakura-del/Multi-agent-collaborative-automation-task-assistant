"""Readiness 端点测试：真实探测 DB / Redis / Chroma。

覆盖：
- 全部探测通过 → 200 且 status=ready
- 任一探测失败 → 503 且对应依赖标记 failed
- 探测失败不影响 /health 存活端点
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> Iterator[TestClient]:
    from app.main import app

    with TestClient(app) as c:
        yield c


def _ready(client: TestClient) -> Any:
    return client.get("/api/v1/health/ready")


class TestReadinessProbes:
    """readiness 依赖探针：通过 monkeypatch 控制三个探测函数。"""

    def test_all_probes_ok_returns_200_ready(self, client: TestClient, monkeypatch) -> None:
        """全部探测通过 → 200 且 status=ready，三项均为 ok。"""
        from app.api.v1.endpoints import health

        async def _ok() -> bool:
            return True

        monkeypatch.setattr(health, "probe_database", _ok)
        monkeypatch.setattr(health, "probe_redis", _ok)
        monkeypatch.setattr(health, "probe_chroma", _ok)

        resp = _ready(client)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ready"
        assert body["checks"]["database"] == "ok"
        assert body["checks"]["redis"] == "ok"
        assert body["checks"]["chroma"] == "ok"

    def test_database_failure_returns_503(self, client: TestClient, monkeypatch) -> None:
        """数据库探测失败 → 503 且 database=failed，其余 ok。"""
        from app.api.v1.endpoints import health

        async def _ok() -> bool:
            return True

        async def _bad() -> bool:
            return False

        monkeypatch.setattr(health, "probe_database", _bad)
        monkeypatch.setattr(health, "probe_redis", _ok)
        monkeypatch.setattr(health, "probe_chroma", _ok)

        resp = _ready(client)
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "not_ready"
        assert body["checks"]["database"] == "failed"
        assert body["checks"]["redis"] == "ok"
        assert body["checks"]["chroma"] == "ok"

    def test_redis_failure_returns_503(self, client: TestClient, monkeypatch) -> None:
        """Redis 探测失败 → 503 且 redis=failed。"""
        from app.api.v1.endpoints import health

        async def _ok() -> bool:
            return True

        async def _bad() -> bool:
            return False

        monkeypatch.setattr(health, "probe_database", _ok)
        monkeypatch.setattr(health, "probe_redis", _bad)
        monkeypatch.setattr(health, "probe_chroma", _ok)

        resp = _ready(client)
        assert resp.status_code == 503
        assert resp.json()["checks"]["redis"] == "failed"

    def test_chroma_failure_returns_503(self, client: TestClient, monkeypatch) -> None:
        """Chroma 探测失败 → 503 且 chroma=failed。"""
        from app.api.v1.endpoints import health

        async def _ok() -> bool:
            return True

        async def _bad() -> bool:
            return False

        monkeypatch.setattr(health, "probe_database", _ok)
        monkeypatch.setattr(health, "probe_redis", _ok)
        monkeypatch.setattr(health, "probe_chroma", _bad)

        resp = _ready(client)
        assert resp.status_code == 503
        assert resp.json()["checks"]["chroma"] == "failed"

    def test_all_probes_fail_returns_503(self, client: TestClient, monkeypatch) -> None:
        """全部探测失败 → 503 且三项均 failed。"""
        from app.api.v1.endpoints import health

        async def _bad() -> bool:
            return False

        monkeypatch.setattr(health, "probe_database", _bad)
        monkeypatch.setattr(health, "probe_redis", _bad)
        monkeypatch.setattr(health, "probe_chroma", _bad)

        resp = _ready(client)
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "not_ready"
        for dep in ("database", "redis", "chroma"):
            assert body["checks"][dep] == "failed"

    def test_health_liveness_unaffected_by_readiness(self, client: TestClient, monkeypatch) -> None:
        """readiness 探测失败不影响 /health 存活端点（仍 200）。"""
        from app.api.v1.endpoints import health

        async def _bad() -> bool:
            return False

        monkeypatch.setattr(health, "probe_database", _bad)
        monkeypatch.setattr(health, "probe_redis", _bad)
        monkeypatch.setattr(health, "probe_chroma", _bad)

        # 存活端点应仍 200
        assert client.get("/api/v1/health").status_code == 200
