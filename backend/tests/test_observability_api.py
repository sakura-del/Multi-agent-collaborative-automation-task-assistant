"""可观测性端点测试（Phase 2 · 可观测性 Task 5）。

## 两个端点，两套安全语义

| 端点 | 鉴权 | 为什么 |
| --- | --- | --- |
| `GET /metrics` | **无** | Prometheus 抓取器不带业务 token；靠网络层隔离（runbook 记录） |
| `GET /api/v1/observability/overview` | **必须 Bearer** | 含用户自己的任务量与 token 成本，必须 owner-scoped |

`/metrics` 无鉴权意味着**绝不允许 PII**：不能有 user_id / 任务标题 / 真实路径。
本文件用「断言响应里不含敏感串」的方式把这条约束钉死。
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core import metrics
from app.db.models.task import Task


@pytest.fixture(autouse=True)
def _clean_registry() -> None:
    metrics.reset_for_tests()


@pytest.fixture
def client() -> TestClient:
    from app.main import create_app

    return TestClient(create_app())


@pytest.fixture
def overview_app() -> Any:
    """独立 app：内存 SQLite + 伪造当前用户。

    为什么不打真库：`get_current_active_user` 会查 `user` 表，而本测试只关心
    owner-scoped 聚合逻辑。用 `dependency_overrides` 注入固定 user_id，
    就能精确验证「只统计这个 user_id 的任务」。
    """
    import asyncio
    from collections.abc import AsyncIterator

    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    import app.db.models  # noqa: F401  确保 ORM 全部注册
    from app.api.v1.endpoints import observability as observability_endpoint
    from app.db.base import Base
    from app.db.models.user import User
    from app.db.session import get_db

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    user_id = uuid.uuid4()

    async def _create_all() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    asyncio.run(_create_all())

    async def _get_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    async def _fake_user() -> User:
        user = User(
            id=user_id,
            username="tester",
            email="tester@example.com",
            password_hash="x",
            is_active=True,
        )
        return user

    application = FastAPI()
    application.include_router(observability_endpoint.router, prefix="/api/v1")
    application.dependency_overrides[get_db] = _get_db
    application.dependency_overrides[observability_endpoint.get_current_active_user] = _fake_user
    # 供测试造数据用
    application.state.session_factory = factory

    return application, user_id


class TestMetricsEndpoint:
    def test_metrics_is_public(self, client: TestClient) -> None:
        """无 token 也能抓（Prometheus 抓取器不带业务 token）。"""
        resp = client.get("/metrics")

        assert resp.status_code == 200
        assert "text/plain" in resp.headers["content-type"]

    def test_metrics_returns_prometheus_format(self, client: TestClient) -> None:
        metrics.inc_counter("maa_quota_rejected")

        resp = client.get("/metrics")

        body = resp.text
        assert "# TYPE maa_quota_rejected_total counter" in body
        assert "maa_quota_rejected_total 1.0" in body

    def test_metrics_has_all_required_metric_families(self, client: TestClient) -> None:
        resp = client.get("/metrics")
        body = resp.text

        for name in (
            "maa_http_requests_total",
            "maa_http_request_duration_seconds",
            "maa_tasks_total",
            "maa_task_tokens_total",
            "maa_task_nodes_total",
            "maa_literature_source_errors_total",
            "maa_rate_limit_rejected_total",
            "maa_quota_rejected_total",
        ):
            assert name in body, f"/metrics 缺少 {name}"

    def test_metrics_contains_no_pii(self, client: TestClient) -> None:
        """**无鉴权端点绝不能有 PII**（这条是硬约束，不是风格问题）。

        用一个固定的 UUID 当"用户 id"打点后抓取，断言它没出现在响应里。
        """
        secret_user_id = "11111111-2222-3333-4444-555555555555"
        metrics.inc_counter("maa_quota_rejected")
        resp = client.get("/metrics")

        assert secret_user_id not in resp.text
        # 也不该出现任务标题之类的自由文本字段名
        assert "task_title" not in resp.text


class TestOverviewEndpoint:
    def test_requires_auth(self, client: TestClient) -> None:
        resp = client.get("/api/v1/observability/overview")

        assert resp.status_code == 401

    def test_returns_owner_scoped_stats(self, overview_app: tuple[Any, uuid.UUID]) -> None:
        """必须只统计自己的任务，且契约字段齐全（前端看板依赖）。"""
        app, _ = overview_app
        client = TestClient(app)
        resp = client.get("/api/v1/observability/overview")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body) >= {"tasks", "success_rate", "tokens", "recent"}
        assert set(body["tasks"]) >= {
            "total",
            "completed",
            "failed",
            "cancelled",
            "waiting_human",
        }
        # 新用户：全 0、成功率 0（不抛错、不返回 null）
        assert body["tasks"]["total"] == 0
        assert body["success_rate"] == 0.0
        assert body["tokens"]["total_used"] == 0
        assert body["tokens"]["avg_per_task"] == 0
        assert body["recent"] == []

    def test_aggregates_own_tasks(self, overview_app: tuple[Any, uuid.UUID]) -> None:
        """有任务时应正确聚合：状态分布 / token 总量 / 成功率 / 最近列表。"""
        import asyncio

        app, user_id = overview_app
        factory = app.state.session_factory  # type: ignore[attr-defined]

        async def _seed() -> None:
            async with factory() as session:
                for status, tokens in (
                    ("completed", 30_000),
                    ("completed", 10_000),
                    ("waiting_human", 5_000),
                    ("cancelled", 0),
                ):
                    session.add(
                        Task(
                            user_id=user_id,
                            title=f"任务-{status}",
                            description="d",
                            status=status,
                            used_tokens=tokens,
                        )
                    )
                await session.commit()

        asyncio.run(_seed())
        client = TestClient(app)
        body = client.get("/api/v1/observability/overview").json()

        assert body["tasks"]["total"] == 4
        assert body["tasks"]["completed"] == 2
        assert body["tasks"]["waiting_human"] == 1
        assert body["tasks"]["cancelled"] == 1
        # 成功率 = completed / 全部终态 = 2/4
        assert body["success_rate"] == 0.5
        assert body["tokens"]["total_used"] == 45_000
        assert body["tokens"]["avg_per_task"] == 45_000 // 4
        assert len(body["recent"]) == 4
        for row in body["recent"]:
            assert set(row) >= {"task_id", "status", "used_tokens", "duration_seconds"}

    def test_other_users_data_not_visible(self, overview_app: tuple[Any, uuid.UUID]) -> None:
        """别人的任务不能出现在我的看板里（owner-scoped 的硬断言）。"""
        import asyncio

        app, user_id = overview_app
        factory = app.state.session_factory  # type: ignore[attr-defined]
        other_user = uuid.uuid4()

        async def _seed() -> None:
            async with factory() as session:
                session.add(
                    Task(
                        user_id=other_user,
                        title="B 的任务",
                        description="d",
                        status="completed",
                        used_tokens=999_999,
                    )
                )
                session.add(
                    Task(
                        user_id=user_id,
                        title="我的任务",
                        description="d",
                        status="completed",
                        used_tokens=1_000,
                    )
                )
                await session.commit()

        asyncio.run(_seed())
        client = TestClient(app)
        body = client.get("/api/v1/observability/overview").json()

        assert body["tasks"]["total"] == 1, "只能看到自己的那 1 条"
        assert body["tokens"]["total_used"] == 1_000, "不能泄漏别人的 token 用量"
        assert all(row["used_tokens"] != 999_999 for row in body["recent"])
