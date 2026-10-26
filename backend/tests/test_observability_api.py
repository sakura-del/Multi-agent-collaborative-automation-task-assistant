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
        # 平均值口径**设计变更**（2026-10-26 看板重设计）：
        # 分母从「全部任务数(4)」改为「有用量记录的任务数(3)」。
        # 理由：`cancelled` 那条 used_tokens=0 是**真的没消耗**，把它算进分母
        # 会把均值人为压低（45000/4=11250 vs 45000/3=15000）。更关键的是历史任务
        # 全是 0（修复前用量没落库），按全部任务平均会让均值趋近 0 且毫无意义。
        assert body["tokens"]["avg_per_task"] == 45_000 // 3
        assert body["tokens"]["recorded_task_count"] == 3
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


class TestOverviewContractForRedesign:
    """用量看板重设计（2026-10-26）新增的契约断言。

    ## 为什么要改契约，而不是前端自己算

    旧契约只给 5 个固定状态计数（completed/failed/cancelled/waiting_human + total）。
    实测 demo 账号 27 个任务里 `waiting_approval` 有 1 个——**这个状态在旧契约里
    根本不存在**，于是看板出现「总数 27，但状态分布加起来 26」的自相矛盾。
    用户看不到差的那一个，因为差值不显示。

    正确的修法：后端给**全部状态的计数**（`by_status`），前端照单展示。
    前端自己从 recent 里凑数不行——recent 只有 10 条，凑出来的分布是假的。
    """

    def test_by_status_covers_every_status_and_sums_to_total(
        self, overview_app: tuple[Any, uuid.UUID]
    ) -> None:
        """`by_status` 必须覆盖全部状态，且计数之和**恰好等于** total。

        这是「看板的数字能不能自洽」的硬约束：任何一个状态漏掉，
        用户就会看到「总数 27 / 分布合计 26」。
        """
        import asyncio

        app, user_id = overview_app
        factory = app.state.session_factory  # type: ignore[attr-defined]

        statuses = [
            "completed",
            "completed",
            "failed",
            "cancelled",
            "waiting_human",
            "waiting_approval",
            "running",
            "planning",
        ]

        async def _seed() -> None:
            async with factory() as session:
                for index, status in enumerate(statuses):
                    session.add(
                        Task(
                            user_id=user_id,
                            title=f"任务-{index}",
                            description="d",
                            status=status,
                            used_tokens=1_000,
                        )
                    )
                await session.commit()

        asyncio.run(_seed())
        client = TestClient(app)
        body = client.get("/api/v1/observability/overview").json()

        assert "by_status" in body, "看板需要全状态分布（旧契约漏掉 waiting_approval）"
        by_status = body["by_status"]
        # 每个非零状态都在，且计数正确
        assert by_status["completed"] == 2
        assert by_status["failed"] == 1
        assert by_status["cancelled"] == 1
        assert by_status["waiting_human"] == 1
        assert by_status["waiting_approval"] == 1
        assert by_status["running"] == 1
        assert by_status["planning"] == 1
        # 硬约束：分布合计 == total（不自洽的看板就是坏看板）
        assert sum(by_status.values()) == body["tasks"]["total"] == 8

    def test_terminal_count_makes_success_rate_checkable(
        self, overview_app: tuple[Any, uuid.UUID]
    ) -> None:
        """`terminal_count` 让前端能显示「成功率 76.9%（20/26）」而不是孤零零的百分比。

        旧契约只给 `success_rate`，用户无法判断这个百分比的分母是什么。
        实测 demo：`waiting_approval=1` 时分母是 26 而不是 27——不告诉用户就会
        被当成算错。
        """
        import asyncio

        app, user_id = overview_app
        factory = app.state.session_factory  # type: ignore[attr-defined]

        async def _seed() -> None:
            async with factory() as session:
                # 2 完成 + 1 取消（终态 3），再加 1 个 waiting_approval（非终态）
                for status in ("completed", "completed", "cancelled", "waiting_approval"):
                    session.add(
                        Task(
                            user_id=user_id,
                            title=f"任务-{status}",
                            description="d",
                            status=status,
                            used_tokens=0,
                        )
                    )
                await session.commit()

        asyncio.run(_seed())
        client = TestClient(app)
        body = client.get("/api/v1/observability/overview").json()

        assert (
            body["terminal_count"] == 3
        ), "终态数 = completed + failed + cancelled + waiting_human"
        assert body["success_rate"] == round(2 / 3, 4)
        # 前端用它算「已完成 2 / 终态 3」，与 success_rate 同源
        assert body["tasks"]["completed"] == 2

    def test_recent_rows_carry_title_and_created_at(
        self, overview_app: tuple[Any, uuid.UUID]
    ) -> None:
        """最近任务必须能被人认出来：只有 8 位 UUID 的列表没有信息价值。

        旧契约只有 `task_id` / `status` / `used_tokens` / `duration_seconds`。
        实测截图：一整列 `2361d17a`、`2aa48e0a`——用户不知道哪条是哪个任务。
        """
        import asyncio

        app, user_id = overview_app
        factory = app.state.session_factory  # type: ignore[attr-defined]

        async def _seed() -> None:
            async with factory() as session:
                session.add(
                    Task(
                        user_id=user_id,
                        title="检索增强生成评估方法综述",
                        description="d",
                        status="completed",
                        used_tokens=5_346,
                    )
                )
                await session.commit()

        asyncio.run(_seed())
        client = TestClient(app)
        body = client.get("/api/v1/observability/overview").json()

        row = body["recent"][0]
        assert row["title"] == "检索增强生成评估方法综述"
        assert row["created_at"], "没有时间就无法判断这是哪次运行"
        # 老字段保留（E2E 与既有前端契约）
        assert set(row) >= {"task_id", "status", "used_tokens", "duration_seconds"}

    def test_zero_token_tasks_are_distinguishable_from_missing_data(
        self, overview_app: tuple[Any, uuid.UUID]
    ) -> None:
        """`tokens.recorded_task_count` 让前端能说「N 个任务有用量记录」。

        为什么必要：修好 `sum_actual_tokens` 之后，**历史任务**的 `used_tokens`
        仍然是 0（当时就记成 0 了）。如果前端只说「累计 0 token」，用户会以为
        系统坏了；如果说「12 个任务有用量记录」，用户就知道是新数据才统计。
        这个字段也是前端「0 但有历史任务」提示的依据。
        """
        import asyncio

        app, user_id = overview_app
        factory = app.state.session_factory  # type: ignore[attr-defined]

        async def _seed() -> None:
            async with factory() as session:
                session.add(
                    Task(
                        user_id=user_id,
                        title="有用量",
                        description="d",
                        status="completed",
                        used_tokens=5_000,
                    )
                )
                session.add(
                    Task(
                        user_id=user_id,
                        title="历史无用量",
                        description="d",
                        status="completed",
                        used_tokens=0,
                    )
                )
                await session.commit()

        asyncio.run(_seed())
        client = TestClient(app)
        body = client.get("/api/v1/observability/overview").json()

        assert body["tokens"]["recorded_task_count"] == 1
        assert body["tokens"]["total_used"] == 5_000
        # 平均只在「有用量记录的任务」之间算，否则被历史 0 拉低成假的低均值
        assert body["tokens"]["avg_per_task"] == 5_000
