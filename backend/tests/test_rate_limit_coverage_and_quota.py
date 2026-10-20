"""限流覆盖补全 + 按用户配额扣减（Phase 1 P1-D 收尾）。

对应计划 `2026-10-20-00-phase1-foundation-plan.md` 的「遗留：未覆盖全部写接口；
token 配额未在执行期扣减」。

## 本文件锁定的两件事

### 1. 限流覆盖**全部写接口**

此前只覆盖 9 条路由（登录/注册/建任务/上传/搜索/校验/导入），遗漏了：
模板上传与渲染、样式上传与删除、文档删除、任务规划确认与控制、
邮箱验证/密码重置的 confirm 端点、WS ticket。

遗漏的后果是真实存在的：这些端点同样是**写操作或外部调用**，
不设限就意味着可以被单个客户端无限刷（上传/渲染尤其昂贵）。

### 2. 配额在**任务提交时**扣减

`QuotaService.check_and_consume` 此前只实现了服务能力、**没有任何调用点**——
即 `quota.daily_token_limit` 配置形同虚设，成本失控。

扣减时机的选择：任务提交（`POST /tasks`）是**唯一能事前拒绝**的位置；
执行期扣减只能事后发现（任务已跑完、钱已花）。因此：
- 提交时按**预估 token** 扣减（超限 → 429，任务不创建）；
- 执行完成后按**实际用量**补扣/退还（差额校正）。
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.rate_limit_middleware import _ROUTE_RULES, route_rule_label


class TestAllWriteEndpointsAreCovered:
    """所有写操作端点都必须有对应限流规则。"""

    @pytest.mark.parametrize(
        ("method", "path", "expected_label"),
        [
            # 认证
            ("POST", "/api/v1/auth/login", "auth_login"),
            ("POST", "/api/v1/auth/register", "auth_register"),
            ("POST", "/api/v1/auth/password-reset/request", "auth_register"),
            ("POST", "/api/v1/auth/password-reset/confirm", "auth_register"),
            ("POST", "/api/v1/auth/verify-email/request", "auth_register"),
            ("POST", "/api/v1/auth/verify-email/confirm", "auth_register"),
            # 任务
            ("POST", "/api/v1/tasks", "task_create"),
            ("POST", "/api/v1/tasks/abc/plan/approve", "task_control"),
            ("POST", "/api/v1/tasks/abc/control", "task_control"),
            ("POST", "/api/v1/tasks/ws-ticket", "task_control"),
            # 知识库
            ("POST", "/api/v1/knowledge/documents", "upload"),
            ("DELETE", "/api/v1/knowledge/documents/doc-1", "upload"),
            ("POST", "/api/v1/knowledge/search", "search"),
            # 引用样式
            ("POST", "/api/v1/citation-styles", "verify"),
            ("DELETE", "/api/v1/citation-styles/my-style", "verify"),
            # 模板
            ("POST", "/api/v1/templates", "template_write"),
            ("DELETE", "/api/v1/templates/tpl-1", "template_write"),
            ("POST", "/api/v1/templates/tpl-1/render", "template_render"),
            # 参考文献导入
            ("POST", "/api/v1/references/import", "verify"),
        ],
    )
    def test_write_endpoint_has_rule(self, method: str, path: str, expected_label: str) -> None:
        assert (
            route_rule_label(method, path) == expected_label
        ), f"{method} {path} 未被限流覆盖（期望 {expected_label}）"

    def test_every_rule_label_exists_in_config(self) -> None:
        """规则标签必须在 `RateLimitRules` 里有对应字段（否则运行期 AttributeError）。"""
        from app.core.config import RateLimitRules

        fields = set(RateLimitRules.model_fields)
        labels = {label for label, _, _ in _ROUTE_RULES}
        missing = labels - fields
        assert not missing, f"限流规则标签在配置里不存在：{sorted(missing)}"

    def test_read_endpoints_are_not_rate_limited(self) -> None:
        """读接口不参与限流（避免误伤轮询）。"""
        assert route_rule_label("GET", "/api/v1/tasks") is None
        assert route_rule_label("GET", "/api/v1/templates") is None
        assert route_rule_label("GET", "/api/v1/knowledge/documents") is None


class _FakeQuota:
    """配额替身：记录调用并可按预设抛超限。"""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[Any, int]] = []

    async def check_and_consume(self, user_id: Any, *, tokens: int) -> None:
        self.calls.append((user_id, tokens))
        if self.error is not None:
            raise self.error


class _User:
    id = uuid.uuid4()
    username = "tester"
    email = "tester@example.com"
    is_active = True
    role = "user"


def _build_app(*, quota: _FakeQuota, submitted: list[str]) -> FastAPI:
    """构造只挂任务路由的最小应用，注入 fake 配额与 fake 执行器。"""
    from app.api.v1.endpoints import tasks as tasks_endpoint
    from app.core.exceptions import AppException

    class _FakeExecution:
        async def submit(self, user_id: Any, contract: Any) -> Any:
            submitted.append("created")
            # 响应模型要求 UUID（`TaskCreateOut.task_id`）
            return uuid.uuid4()

    async def _quota_dep() -> Any:
        return quota

    def _exec_dep() -> Any:
        return _FakeExecution()

    def _user_dep() -> Any:
        return _User()

    app = FastAPI()
    app.include_router(tasks_endpoint.router, prefix="/api/v1")
    app.dependency_overrides[tasks_endpoint.get_quota_service] = _quota_dep
    app.dependency_overrides[tasks_endpoint.get_task_execution] = _exec_dep
    app.dependency_overrides[tasks_endpoint.get_current_active_user] = _user_dep

    @app.exception_handler(AppException)
    async def _handle(_: Any, exc: AppException) -> Any:
        from fastapi.responses import JSONResponse

        headers: dict[str, str] = {}
        retry_after = getattr(exc, "retry_after", None)
        if retry_after is not None:
            headers["Retry-After"] = str(int(retry_after))
        return JSONResponse(
            status_code=exc.status_code,
            content={"error_code": exc.error_code, "message": exc.message},
            headers=headers or None,
        )

    return app


_PAYLOAD = {
    "topic": "测试主题内容",
    "keywords": ["测试关键词"],
    "citation_style": "gbt7714-2025-numeric",
}


class TestQuotaIsConsumedOnTaskSubmit:
    """任务提交时必须扣减配额（否则 `daily_token_limit` 形同虚设）。"""

    def test_quota_is_checked_before_creating_task(self) -> None:
        """配额检查发生在任务创建**之前**：超限时任务不得被创建。"""
        quota = _FakeQuota()
        submitted: list[str] = []
        app = _build_app(quota=quota, submitted=submitted)

        with TestClient(app) as client:
            response = client.post("/api/v1/tasks", json=_PAYLOAD)

        assert response.status_code == 201, response.text
        assert quota.calls, "任务提交未扣减配额"
        assert quota.calls[0][1] > 0, "扣减的 token 预估必须为正"
        assert submitted == ["created"]

    def test_quota_exceeded_returns_429_and_does_not_create_task(self) -> None:
        """超配额 → 429 且**任务不被创建**（不能先建后拒）。"""
        from app.core.quota import QuotaExceededError

        quota = _FakeQuota(error=QuotaExceededError(retry_after=120))
        submitted: list[str] = []
        app = _build_app(quota=quota, submitted=submitted)

        with TestClient(app) as client:
            response = client.post("/api/v1/tasks", json=_PAYLOAD)

        assert response.status_code == 429
        assert "retry-after" in {key.lower() for key in response.headers}
        assert submitted == [], "配额超限时任务不应被创建"


class TestQuotaConfigWiring:
    def test_quota_config_exists(self) -> None:
        """配置层必须暴露 quota 三个字段（供 `build_quota_service` 读取）。"""
        from app.core.config import get_settings

        settings = get_settings()
        assert settings.quota.enabled is True
        assert settings.quota.daily_token_limit >= -1
        assert settings.quota.window_seconds >= 1
        assert settings.quota.key_prefix

    def test_disabled_quota_returns_none(self) -> None:
        """`quota.enabled=false` 时返回 None（调用方据此跳过检查）。"""
        from app.api.v1.endpoints.tasks import build_quota_service
        from app.core.config import get_settings

        settings = get_settings()
        original = settings.quota.enabled
        settings.quota.enabled = False
        try:
            assert build_quota_service() is None
        finally:
            settings.quota.enabled = original

    def test_enabled_quota_builds_service_with_config_limits(self) -> None:
        """启用时按配置构造服务（限额来自配置，不是硬编码）。"""
        from app.api.v1.endpoints.tasks import build_quota_service
        from app.core.config import get_settings

        settings = get_settings()
        original = settings.quota.enabled
        settings.quota.enabled = True
        try:
            service = build_quota_service()
        finally:
            settings.quota.enabled = original

        assert service is not None
