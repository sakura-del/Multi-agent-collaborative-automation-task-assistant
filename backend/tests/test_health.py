"""健康检查端点测试。

阶段0验收：`/health` 返回 200，包含 status / version 字段。
"""

from __future__ import annotations

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


def test_readiness_check_endpoint() -> None:
    """`/health/ready` 应返回 ready 状态。"""
    client = TestClient(app)
    response = client.get("/api/v1/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert "checks" in body
