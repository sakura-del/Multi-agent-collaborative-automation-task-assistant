"""Issue #61 上线前体验修复清单专项测试：

1. Alembic 迁移 `4f8a2d1c9e7b`（free 套餐 3 篇/月 + analytics_event + user_feedback）幂等与单 head；
2. 统一认证 `/email-otp/verify` 首次自动建号返回 `is_new_user=True`，再次登录返回 `False`；
3. 一键示例任务（`is_demo=True`）：首次体验免扣篇数与 token，且 `settle_quota` 不误退未预扣的 token；已有任务后不再免扣；
4. 推广漏斗上报 `POST /analytics/events`、站内反馈 `POST /feedback` 与管理端 `/admin/console/funnel` / `/feedback`。
"""

from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import uuid
from typing import Any

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.db.models  # noqa: F401
from app.api.v1.endpoints import admin_console, auth_unified, growth
from app.core.exceptions import AppException
from app.core.security import create_access_token
from app.db.base import Base
from app.db.models.analytics import AnalyticsEvent, UserFeedback
from app.db.models.task import Task
from app.db.models.user import User
from app.db.session import get_db
from app.orchestration.services.task_execution import TaskExecution
from app.orchestration.services.task_submission import submit_review_task
from app.schemas.task import TaskCreate

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    BACKEND_DIR / "alembic" / "versions" / "4f8a2d1c9e7b_onboarding_free_tier_analytics_feedback.py"
)


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("m_issue61", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestIssue61Migration:
    def test_single_alembic_head_points_to_new_migration(self) -> None:
        script = ScriptDirectory.from_config(Config(str(BACKEND_DIR / "alembic.ini")))
        heads = script.get_heads()
        assert heads == ["4f8a2d1c9e7b"], f"期望唯一 head 为 4f8a2d1c9e7b，实际：{heads}"

    def test_upgrade_updates_free_plan_creates_tables_and_is_idempotent(self) -> None:
        engine = create_engine("sqlite://")
        with engine.begin() as conn:
            conn.exec_driver_sql(
                'CREATE TABLE "user" (id CHAR(32) PRIMARY KEY, username VARCHAR(64))'
            )
            conn.exec_driver_sql(
                "CREATE TABLE plan (id VARCHAR(32) PRIMARY KEY, monthly_reviews INTEGER, description TEXT)"
            )
            conn.exec_driver_sql(
                "INSERT INTO plan (id, monthly_reviews, description) VALUES ('free', 1, 'old')"
            )

            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                mod = _load_migration()
                mod.upgrade()
                mod.upgrade()  # 幂等

            row = conn.exec_driver_sql(
                "SELECT monthly_reviews, description FROM plan WHERE id = 'free'"
            ).first()
            assert row is not None and row[0] == 3
            assert "15" in row[1]

            inspector = inspect(conn)
            for model in (AnalyticsEvent, UserFeedback):
                migrated = {c["name"] for c in inspector.get_columns(model.__tablename__)}
                expected = set(model.__table__.columns.keys())
                assert migrated == expected

            with Operations.context(ctx):
                mod.downgrade()
            tables = set(inspect(conn).get_table_names())
            assert "analytics_event" not in tables
            assert "user_feedback" not in tables
            old_row = conn.exec_driver_sql(
                "SELECT monthly_reviews FROM plan WHERE id = 'free'"
            ).first()
            assert old_row is not None and old_row[0] == 1
        engine.dispose()


class TestIsNewUserFlagOnOtp:
    @pytest.mark.asyncio
    async def test_verify_email_otp_marks_first_signup_only(self) -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async def _get_db():
            async with factory() as s:
                yield s

        class _FakeSender:
            def __init__(self) -> None:
                self.bodies: list[str] = []

            async def send(self, *, to: str, subject: str, body: str) -> None:
                self.bodies.append(body)

        sender = _FakeSender()
        app = FastAPI()
        app.include_router(auth_unified.router, prefix="/api/v1")
        app.dependency_overrides[get_db] = _get_db
        app.dependency_overrides[auth_unified.get_email_sender] = lambda: sender

        with TestClient(app) as client:
            r1 = client.post("/api/v1/auth/email-otp/request", json={"email": "newbie@uni.edu"})
            assert r1.status_code == 200
            code1 = sender.bodies[-1].split("验证码为：")[1][:6]

            v1 = client.post(
                "/api/v1/auth/email-otp/verify",
                json={"email": "newbie@uni.edu", "code": code1},
            )
            assert v1.status_code == 200, v1.text
            assert v1.json()["is_new_user"] is True

            # 清空冷却后同一邮箱再次验证码登录，is_new_user 必须为 False
            await auth_unified._memory_store.delete("email_otp:cooldown:newbie@uni.edu")
            r2 = client.post("/api/v1/auth/email-otp/request", json={"email": "newbie@uni.edu"})
            assert r2.status_code == 200
            code2 = sender.bodies[-1].split("验证码为：")[1][:6]

            v2 = client.post(
                "/api/v1/auth/email-otp/verify",
                json={"email": "newbie@uni.edu", "code": code2},
            )
            assert v2.status_code == 200
            assert v2.json()["is_new_user"] is False

        await engine.dispose()


class TestDemoTaskExemption:
    @pytest.mark.asyncio
    async def test_first_demo_task_skips_quota_and_second_does_not(self) -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        async with factory() as session:
            user = User(username="demo_starter", email="starter@uni.edu", is_active=True)
            session.add(user)
            await session.commit()
            await session.refresh(user)

        class _PlanStub:
            def __init__(self) -> None:
                self.consume_calls = 0

            async def check_document_limit(self, user_id: Any, max_documents: int) -> None:
                return None

            async def check_and_consume_review(self, user_id: Any) -> None:
                self.consume_calls += 1

        class _QuotaStub:
            def __init__(self) -> None:
                self.consume_calls = 0
                self.refund_calls = 0

            async def check_and_consume(self, user_id: Any, *, tokens: int) -> None:
                self.consume_calls += 1

            async def refund(self, user_id: Any, *, tokens: int) -> None:
                self.refund_calls += 1

        class _ExecStub:
            def __init__(self) -> None:
                self.calls: list[dict[str, Any]] = []

            async def submit(self, user_id: Any, request: Any, **kwargs: Any) -> uuid.UUID:
                self.calls.append({"user_id": user_id, "kwargs": kwargs})
                tid = uuid.uuid4()
                async with factory() as s:
                    s.add(
                        Task(
                            id=tid,
                            user_id=user_id,
                            title=request.topic,
                            description="demo",
                            task_type="literature_review",
                            status="completed",
                            config={},
                            model_snapshot=kwargs.get("model_snapshot"),
                        )
                    )
                    await s.commit()
                return tid

        plan = _PlanStub()
        quota = _QuotaStub()
        exec_stub = _ExecStub()

        demo_payload = TaskCreate(
            topic="示例综述主题（测试）",
            keywords=["RAG"],
            max_documents=5,
            offline_dataset=True,
            verify_references=False,
            is_demo=True,
        )

        async with factory() as session:
            first_id = await submit_review_task(
                db=session,
                user_id=user.id,
                payload=demo_payload,
                execution=exec_stub,  # type: ignore[arg-type]
                plan_service=plan,  # type: ignore[arg-type]
                quota=quota,  # type: ignore[arg-type]
            )

        assert plan.consume_calls == 0, "首次一键示例任务不得扣减免费篇数"
        assert quota.consume_calls == 0, "首次一键示例任务不得预扣 token"
        assert exec_stub.calls[0]["kwargs"]["model_snapshot"]["demo_exempt"] is True

        # 验证 TaskExecution.settle_quota 对 demo_exempt 任务不会凭空退款
        real_exec = TaskExecution(
            container=None,  # type: ignore[arg-type]
            session_factory=factory,
            quota_service=quota,
        )
        await real_exec.settle_quota(first_id)
        assert quota.refund_calls == 0, "未预扣的示例任务结算时不得退还 token"

        # 已有过任务后，再次提交 is_demo 必须走正常扣费（防刷）
        async with factory() as session:
            await submit_review_task(
                db=session,
                user_id=user.id,
                payload=demo_payload,
                execution=exec_stub,  # type: ignore[arg-type]
                plan_service=plan,  # type: ignore[arg-type]
                quota=quota,  # type: ignore[arg-type]
            )

        assert plan.consume_calls == 1
        assert quota.consume_calls == 1
        await engine.dispose()


class TestGrowthFunnelAndFeedback:
    def test_analytics_feedback_and_admin_queries(self) -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")

        async def _init() -> tuple[Any, User, User]:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            f = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with f() as s:
                admin = User(username="adm", email="adm@uni.edu", role="admin", is_active=True)
                normal = User(username="stu", email="stu@uni.edu", role="user", is_active=True)
                s.add_all([admin, normal])
                await s.commit()
                await s.refresh(admin)
                await s.refresh(normal)
                return f, admin, normal

        factory, admin, normal = asyncio.run(_init())

        async def _get_db():
            async with factory() as s:
                yield s

        app = FastAPI()
        app.include_router(growth.router, prefix="/api/v1")
        app.include_router(admin_console.router, prefix="/api/v1")
        app.dependency_overrides[get_db] = _get_db

        @app.exception_handler(AppException)
        async def _handle(_: Any, exc: AppException) -> JSONResponse:
            return JSONResponse(
                status_code=exc.status_code,
                content={"error_code": exc.error_code, "message": exc.message},
            )

        user_headers = {"Authorization": f"Bearer {create_access_token(normal.id, role='user')}"}
        admin_headers = {"Authorization": f"Bearer {create_access_token(admin.id, role='admin')}"}

        with TestClient(app) as client:
            # 匿名上报落地页 & 示例页 & 一键示例事件
            ev1 = client.post(
                "/api/v1/analytics/events",
                json={"event_name": "landing_view", "visitor_id": "v-1001", "path": "/"},
            )
            assert ev1.status_code == 202
            ev2 = client.post(
                "/api/v1/analytics/events",
                json={"event_name": "demo_view", "visitor_id": "v-1001", "path": "/demo"},
            )
            assert ev2.status_code == 202
            ev3 = client.post(
                "/api/v1/analytics/events",
                headers=user_headers,
                json={"event_name": "demo_task_clicked", "visitor_id": "v-1001", "path": "/tasks"},
            )
            assert ev3.status_code == 202

            # 普通用户提交站内反馈
            fb = client.post(
                "/api/v1/feedback",
                headers=user_headers,
                json={
                    "category": "suggestion",
                    "content": "希望能尽快接入知网题录检索",
                    "page_path": "/tasks",
                },
            )
            assert fb.status_code == 201, fb.text
            assert fb.json()["category"] == "suggestion"

            # 非管理员拒绝访问管理漏斗与反馈列表
            assert (
                client.get("/api/v1/admin/console/funnel", headers=user_headers).status_code == 403
            )
            assert (
                client.get("/api/v1/admin/console/feedback", headers=user_headers).status_code
                == 403
            )

            # 管理员查看漏斗与反馈
            funnel_resp = client.get("/api/v1/admin/console/funnel?days=7", headers=admin_headers)
            assert funnel_resp.status_code == 200, funnel_resp.text
            funnel = funnel_resp.json()
            assert funnel["landing_visitors"] == 1
            assert funnel["demo_views"] == 1
            assert funnel["demo_task_clicks"] == 1
            assert funnel["registered_users"] == 2

            fb_list = client.get("/api/v1/admin/console/feedback", headers=admin_headers)
            assert fb_list.status_code == 200, fb_list.text
            assert fb_list.json()["total"] == 1
            assert fb_list.json()["items"][0]["username"] == "stu"
            assert "知网" in fb_list.json()["items"][0]["content"]

        asyncio.run(engine.dispose())
