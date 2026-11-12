"""Readiness 端点测试：真实探测 DB / Redis / 向量库。

覆盖：
- 全部探测通过 → 200 且 status=ready
- 任一探测失败 → 503 且对应依赖标记 failed
- 探测失败不影响 /health 存活端点

> 2026-11-12（Issue #11 M-5）：向量后端默认从 chroma 改为 pgvector，
> 本文件改为**与后端无关**的打桩方式（替 `probe_vector`），并分别锁定
> 「pgvector 模式无 chroma 别名」与「chroma 模式保留兼容别名」两条行为。
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


@pytest.fixture
def vector_backend(monkeypatch):
    """把向量后端固定为 pgvector（返回 setter 可切到别的后端）。"""
    from app.core.config import settings

    monkeypatch.setattr(settings.rag, "vector_backend", "pgvector", raising=False)

    def _set(name: str) -> None:
        monkeypatch.setattr(settings.rag, "vector_backend", name, raising=False)

    return _set


def _ready(client: TestClient) -> Any:
    return client.get("/api/v1/health/ready")


def _probe_returning(result: bool):
    async def _probe() -> bool:
        return result

    return _probe


def _stub(monkeypatch, *, database: bool, redis: bool, vector: bool) -> None:
    """按给定结果替掉三个探测函数（不关心后端实现）。"""
    from app.api.v1.endpoints import health

    monkeypatch.setattr(health, "probe_database", _probe_returning(database))
    monkeypatch.setattr(health, "probe_redis", _probe_returning(redis))
    monkeypatch.setattr(health, "probe_vector", _probe_returning(vector))


class TestReadinessProbes:
    """readiness 依赖探针：通过 monkeypatch 控制三个探测函数。"""

    def test_all_probes_ok_returns_200_ready(
        self, client: TestClient, monkeypatch, vector_backend
    ) -> None:
        """全部探测通过 → 200 且 status=ready，三项均为 ok。"""
        _stub(monkeypatch, database=True, redis=True, vector=True)

        resp = _ready(client)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ready"
        assert body["checks"]["database"] == "ok"
        assert body["checks"]["redis"] == "ok"
        assert body["checks"]["vector"] == "ok"

    def test_database_failure_returns_503(
        self, client: TestClient, monkeypatch, vector_backend
    ) -> None:
        """数据库探测失败 → 503 且 database=failed，其余 ok。"""
        _stub(monkeypatch, database=False, redis=True, vector=True)

        resp = _ready(client)
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "not_ready"
        assert body["checks"]["database"] == "failed"
        assert body["checks"]["redis"] == "ok"
        assert body["checks"]["vector"] == "ok"

    def test_redis_failure_returns_503(
        self, client: TestClient, monkeypatch, vector_backend
    ) -> None:
        """Redis 探测失败 → 503 且 redis=failed。"""
        _stub(monkeypatch, database=True, redis=False, vector=True)

        resp = _ready(client)
        assert resp.status_code == 503
        assert resp.json()["checks"]["redis"] == "failed"

    def test_vector_failure_returns_503(
        self, client: TestClient, monkeypatch, vector_backend
    ) -> None:
        """向量库探测失败 → 503 且 vector=failed。"""
        _stub(monkeypatch, database=True, redis=True, vector=False)

        resp = _ready(client)
        assert resp.status_code == 503
        assert resp.json()["checks"]["vector"] == "failed"

    def test_all_probes_fail_returns_503(
        self, client: TestClient, monkeypatch, vector_backend
    ) -> None:
        """全部探测失败 → 503 且三项均 failed。"""
        _stub(monkeypatch, database=False, redis=False, vector=False)

        resp = _ready(client)
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "not_ready"
        for dep in ("database", "redis", "vector"):
            assert body["checks"][dep] == "failed"

    def test_health_liveness_unaffected_by_readiness(
        self, client: TestClient, monkeypatch, vector_backend
    ) -> None:
        """readiness 探测失败不影响 /health 存活端点（仍 200）。"""
        _stub(monkeypatch, database=False, redis=False, vector=False)

        assert client.get("/api/v1/health").status_code == 200


class TestVectorCheckKeyCompatibility:
    """`checks.vector` 是稳定键；chroma 模式下额外保留 `chroma` 别名。"""

    def test_pgvector_mode_has_no_chroma_alias(
        self, client: TestClient, monkeypatch, vector_backend
    ) -> None:
        _stub(monkeypatch, database=True, redis=True, vector=True)

        body = _ready(client).json()
        assert body["checks"]["vector"] == "ok"
        assert "chroma" not in body["checks"], "pgvector 模式下不应再造一个 chroma 键"

    def test_chroma_mode_keeps_legacy_alias(
        self, client: TestClient, monkeypatch, vector_backend
    ) -> None:
        """切换期兼容：老看板/脚本读的是 `checks.chroma`。"""
        vector_backend("chroma")
        _stub(monkeypatch, database=True, redis=True, vector=True)

        body = _ready(client).json()
        assert body["checks"]["vector"] == "ok"
        assert body["checks"]["chroma"] == "ok"
