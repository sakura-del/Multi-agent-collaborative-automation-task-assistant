"""健康检查端点测试。

阶段0验收：`/health` 返回 200，包含 status / version 字段。
就绪检查为真实探测（DB/Redis/Chroma），通过 monkeypatch 探针控制结果。
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.main import app


def test_health_check_returns_ok() -> None:
    """`/health` 应返回 200 且 status=ok。"""
    client = TestClient(app)
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "version" in body
    assert "app" in body


def test_health_check_has_request_id_header() -> None:
    """响应应携带 X-Request-ID 头，便于链路追踪。"""
    client = TestClient(app)
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert "X-Request-ID" in response.headers


def test_health_check_echoes_custom_request_id() -> None:
    """请求头传入的 X-Request-ID 应被原样回传。"""
    client = TestClient(app)
    custom_id = "test-trace-id-12345"
    response = client.get("/api/v1/health", headers={"X-Request-ID": custom_id})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == custom_id


def test_readiness_check_endpoint_all_ok(monkeypatch) -> None:
    """`/health/ready` 全部探测通过时应返回 200 且三项 ok。"""
    from app.api.v1.endpoints import health

    async def _ok() -> bool:
        return True

    monkeypatch.setattr(health, "probe_database", _ok)
    monkeypatch.setattr(health, "probe_redis", _ok)
    monkeypatch.setattr(health, "probe_vector", _ok)

    client = TestClient(app)
    response = client.get("/api/v1/health/ready")

    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    assert body["status"] == "ready"
    # `vector` 是与后端无关的稳定键；`chroma` 别名是否存在取决于当前后端，
    # 两种情形分别在 test_readiness.TestVectorCheckKeyCompatibility 里锁定
    assert {"database", "redis", "vector"} <= set(body["checks"])
    assert body["checks"]["vector"] == "ok"


def test_readiness_check_endpoint_db_down(monkeypatch) -> None:
    """`/health/ready` 数据库探测失败时应返回 503 且 database=failed。"""
    from app.api.v1.endpoints import health

    async def _ok() -> bool:
        return True

    async def _bad() -> bool:
        return False

    monkeypatch.setattr(health, "probe_database", _bad)
    monkeypatch.setattr(health, "probe_redis", _ok)
    monkeypatch.setattr(health, "probe_vector", _ok)

    client = TestClient(app)
    response = client.get("/api/v1/health/ready")

    assert response.status_code == 503
    body: dict[str, Any] = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["database"] == "failed"
