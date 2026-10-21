"""HTTP 指标中间件测试（Phase 2 · 可观测性 Task 2）。

## 本文件锁定四件必须成立的事

1. **路由模板标签**：`/api/v1/tasks/<uuid>` 记录的标签是
   `/api/v1/tasks/{task_id}`，**不含那个 uuid**。
   这是**基数控制**的硬约束：用真实路径会让每条任务产生一条时间序列，
   几百个任务就能把 Prometheus 打爆（而且每个标签组合都要占内存）。
2. **未匹配路由兜底**：404 的标签是 `__unmatched__`，不是原始路径。
3. **探针不记录**：`/metrics` 与 `/api/v1/health*` 不产生指标
   （抓取与探针是高频调用，会把真实业务数据淹没）。
4. **fail-open**：指标中间件任何异常都不能影响响应返回。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.api.metrics_middleware import MetricsMiddleware
from app.core import metrics


@pytest.fixture(autouse=True)
def _clean_registry() -> None:
    metrics.reset_for_tests()


def _client() -> TestClient:
    app = FastAPI()
    app.add_middleware(MetricsMiddleware)

    @app.get("/api/v1/tasks/{task_id}")
    async def get_task(task_id: str) -> dict[str, str]:
        return {"task_id": task_id}

    @app.get("/api/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics")
    async def metrics_endpoint() -> JSONResponse:
        return JSONResponse({"text": metrics.render()})

    @app.get("/boom")
    async def boom() -> dict[str, str]:
        raise RuntimeError("业务炸了")

    return TestClient(app, raise_server_exceptions=False)


SAMPLE_UUID = "3f7a1c2e-9b4d-4a5f-8c6d-1e2f3a4b5c6d"


class TestRouteTemplateLabels:
    def test_uses_route_template_not_real_path(self) -> None:
        """**基数控制的核心断言**：标签里必须是路由模板。"""
        client = _client()
        client.get(f"/api/v1/tasks/{SAMPLE_UUID}")

        text = metrics.render()
        assert 'route="/api/v1/tasks/{task_id}"' in text, "必须用路由模板作标签"
        assert SAMPLE_UUID not in text, "真实路径（含 UUID）绝不能进标签——会炸基数"

    def test_records_status_code(self) -> None:
        client = _client()
        client.get("/api/v1/health")  # 探针不记
        client.get(f"/api/v1/tasks/{SAMPLE_UUID}")

        text = metrics.render()
        assert 'status="200"' in text

    def test_records_method(self) -> None:
        client = _client()
        client.get(f"/api/v1/tasks/{SAMPLE_UUID}")

        assert 'method="GET"' in metrics.render()

    def test_records_duration_histogram(self) -> None:
        client = _client()
        client.get(f"/api/v1/tasks/{SAMPLE_UUID}")

        text = metrics.render()
        assert "maa_http_request_duration_seconds_count" in text
        assert 'route="/api/v1/tasks/{task_id}"' in text


class TestUnmatchedRoute:
    def test_404_uses_placeholder_label(self) -> None:
        """未匹配路由：用 `__unmatched__` 而不是原始路径（否则随机扫描就能炸基数）。"""
        client = _client()
        client.get("/no/such/endpoint/abc123")

        text = metrics.render()
        assert 'route="__unmatched__"' in text
        assert "abc123" not in text

    def test_404_records_status(self) -> None:
        client = _client()
        client.get("/no/such/endpoint")

        assert 'status="404"' in metrics.render()


class TestProbesAreSkipped:
    def test_metrics_endpoint_not_recorded(self) -> None:
        """`/metrics` 自身不记录：抓取是高频调用，会淹没业务指标。"""
        client = _client()
        client.get("/metrics")

        text = metrics.render()
        assert 'route="/metrics"' not in text

    def test_health_endpoints_not_recorded(self) -> None:
        client = _client()
        client.get("/api/v1/health")

        text = metrics.render()
        assert "/api/v1/health" not in text


class TestFailOpen:
    def test_middleware_error_does_not_break_response(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """指标记录失败不能影响响应。"""

        def boom(*args: object, **kwargs: object) -> None:
            raise RuntimeError("指标炸了")

        monkeypatch.setattr(metrics, "_inc_raw", boom)
        monkeypatch.setattr(metrics, "_observe_raw", boom)

        client = _client()
        resp = client.get(f"/api/v1/tasks/{SAMPLE_UUID}")

        assert resp.status_code == 200
        assert resp.json()["task_id"] == SAMPLE_UUID

    def test_server_error_still_recorded(self) -> None:
        """业务 500 也要被记录（否则"错误率"这个概念不存在）。"""
        client = _client()
        resp = client.get("/boom")

        assert resp.status_code == 500
        text = metrics.render()
        assert 'status="500"' in text
        assert 'route="/boom"' in text
