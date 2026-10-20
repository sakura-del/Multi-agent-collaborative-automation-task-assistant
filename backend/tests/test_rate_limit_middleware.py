"""限流中间件接线测试（Phase 1 P1-D 接线层）。

`app/core/rate_limit.py` 的原语已由 verifier 用 49 个用例覆盖；本文件只测**接线层**
（`app/api/rate_limit_middleware.py`）的三件事：

1. 路由 → 规则标签的映射（哪些路径参与、哪些跳过）；
2. 超限返回 429 + `Retry-After` 头；
3. **fail-open**：限流器抛异常 / 熔断后一律放行（不能拦业务）。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.rate_limit_middleware import RateLimitMiddleware, route_rule_label


class TestRouteRuleLabel:
    """路由标签映射：只对「写操作/敏感操作」限流，读接口与探针不参与。"""

    def test_login_and_register_are_limited(self) -> None:
        assert route_rule_label("POST", "/api/v1/auth/login") == "auth_login"
        assert route_rule_label("POST", "/api/v1/auth/register") == "auth_register"

    def test_task_creation_is_limited_and_subpaths_use_control_rule(self) -> None:
        """创建任务走 task_create；确认/控制/WS ticket 走 task_control。

        （此前子路径**完全不限流**——那是 P1-D 的已知缺口：确认与控制同样是写操作，
        可被无限刷。现已补齐，两者用不同限额。）
        """
        assert route_rule_label("POST", "/api/v1/tasks") == "task_create"
        assert route_rule_label("POST", "/api/v1/tasks/") == "task_create"
        assert route_rule_label("POST", "/api/v1/tasks/abc/plan/approve") == "task_control"
        assert route_rule_label("POST", "/api/v1/tasks/abc/control") == "task_control"
        assert route_rule_label("POST", "/api/v1/tasks/ws-ticket") == "task_control"

    def test_uploads_and_search_are_limited(self) -> None:
        assert route_rule_label("POST", "/api/v1/knowledge/documents") == "upload"
        assert route_rule_label("POST", "/api/v1/knowledge/search") == "search"

    def test_get_requests_are_not_limited(self) -> None:
        assert route_rule_label("GET", "/api/v1/tasks") is None
        assert route_rule_label("GET", "/api/v1/knowledge/documents") is None

    def test_health_and_docs_are_not_limited(self) -> None:
        assert route_rule_label("POST", "/api/v1/health/ready") is None
        assert route_rule_label("GET", "/docs") is None


class _FakeLimiter:
    """可编程限流器替身：记录调用，按预设决定放行/拒绝/抛错。"""

    def __init__(self, *, allow: bool = True, error: Exception | None = None) -> None:
        self.allow = allow
        self.error = error
        self.calls: list[tuple[str, int, int]] = []

    async def hit(self, key: str, *, limit: int, window_seconds: int) -> tuple[bool, int]:
        self.calls.append((key, limit, window_seconds))
        if self.error is not None:
            raise self.error
        return (self.allow, 0 if not self.allow else limit - 1)


def _app_with(limiter: Any) -> FastAPI:
    """构造只挂限流中间件的最小应用（不依赖全局 settings 的 Redis）。"""
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, limiter=limiter)

    @app.post("/api/v1/auth/login")
    async def login() -> dict[str, str]:  # pragma: no cover - 简单回显
        return {"ok": "login"}

    @app.get("/api/v1/health/ready")
    async def health() -> dict[str, str]:  # pragma: no cover - 简单回显
        return {"ok": "health"}

    @app.post("/api/v1/tasks")
    async def create_task() -> dict[str, str]:  # pragma: no cover - 简单回显
        return {"ok": "task"}

    return app


class TestRateLimitResponse:
    def test_allows_request_when_under_limit(self) -> None:
        limiter = _FakeLimiter(allow=True)
        with TestClient(_app_with(limiter)) as client:
            response = client.post("/api/v1/auth/login")

        assert response.status_code == 200
        assert limiter.calls, "限流器应被调用"

    def test_rejects_with_429_and_retry_after(self) -> None:
        limiter = _FakeLimiter(allow=False)
        with TestClient(_app_with(limiter)) as client:
            response = client.post("/api/v1/auth/login")

        assert response.status_code == 429
        assert "retry-after" in {key.lower() for key in response.headers}
        assert int(response.headers["retry-after"]) >= 1
        body = response.json()
        assert body["error_code"] == "RATE_LIMITED"
        assert body["details"]["route"] == "auth_login"

    def test_health_endpoint_bypasses_limiter(self) -> None:
        limiter = _FakeLimiter(allow=False)
        with TestClient(_app_with(limiter)) as client:
            response = client.get("/api/v1/health/ready")

        assert response.status_code == 200
        assert limiter.calls == [], "健康探针不应计入限流"

    def test_limit_and_window_come_from_configured_rule(self) -> None:
        limiter = _FakeLimiter(allow=True)
        with TestClient(_app_with(limiter)) as client:
            client.post("/api/v1/auth/login")

        key, limit, window = limiter.calls[0]
        assert key.startswith("auth_login:")
        assert limit > 0
        assert window >= 1


class TestFailOpen:
    """限流组件故障绝不能拦业务请求（严格验收项）。"""

    def test_limiter_exception_allows_request(self) -> None:
        limiter = _FakeLimiter(error=RuntimeError("redis down"))
        with TestClient(_app_with(limiter)) as client:
            response = client.post("/api/v1/auth/login")

        assert response.status_code == 200, "限流器异常时必须放行"

    def test_circuit_opens_after_consecutive_failures(self) -> None:
        """连续失败达到阈值后熔断：不再调用限流器，直接放行。"""
        limiter = _FakeLimiter(error=RuntimeError("redis down"))
        app = _app_with(limiter)
        with TestClient(app) as client:
            for _ in range(5):
                assert client.post("/api/v1/auth/login").status_code == 200
            calls_before = len(limiter.calls)
            assert client.post("/api/v1/auth/login").status_code == 200
            # 熔断后不应再尝试调用限流器
            assert len(limiter.calls) == calls_before, "熔断后仍在调用限流器"

    def test_disabled_limiter_skips_everything(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`rate_limit.enabled=false` 时完全不参与限流。"""
        from app.core.config import get_settings

        limiter = _FakeLimiter(allow=False)
        settings = get_settings()
        original = settings.rate_limit.enabled
        settings.rate_limit.enabled = False
        try:
            app = _app_with(limiter)
            with TestClient(app) as client:
                assert client.post("/api/v1/auth/login").status_code == 200
            assert limiter.calls == []
        finally:
            settings.rate_limit.enabled = original
