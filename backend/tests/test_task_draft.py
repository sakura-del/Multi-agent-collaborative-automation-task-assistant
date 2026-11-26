"""任务创建草稿跨设备云端同步单测（Issue #68）。

覆盖：
1. `GET/PUT/DELETE /api/v1/tasks/draft` CRUD 与跨用户隔离、未鉴权 401；
2. `base_revision` 乐观锁：匹配递增、落后返回 `409 DRAFT_CONFLICT` 带最新云端草稿、`None` 强制覆盖；
3. Schema 校验：超过 `64 KB` 返回 422、未知字段被 `extra="forbid"` 拒绝；
4. `POST /api/v1/tasks` 创建任务成功后自动清除当前用户的云端草稿；
5. Alembic 迁移单 head（`d8b2f5c3a1e9`）以及 SQLite 幂等 `upgrade()` / `downgrade()`。
"""

from __future__ import annotations

import importlib.util
import pathlib
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.deps import get_current_active_user, get_current_user_or_api_key
from app.api.v1.endpoints.tasks import get_plan_service, get_task_execution
from app.api.v1.endpoints.tasks import router as tasks_router
from app.core.exceptions import AppException
from app.db.base import Base
from app.db.models.user import User
from app.db.session import get_db

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
MIGRATION_PATH = BACKEND_DIR / "alembic" / "versions" / "d8b2f5c3a1e9_add_task_draft_table.py"


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("m_issue68_draft", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _setup_db(tmp_path: pathlib.Path) -> tuple[Any, async_sessionmaker[AsyncSession]]:
    db_file = tmp_path / "draft_test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, factory


def _build_app(
    factory: async_sessionmaker[AsyncSession], active_user_holder: dict[str, Any]
) -> FastAPI:
    app = FastAPI()

    @app.exception_handler(AppException)
    async def _handle_app_exc(_: Request, exc: AppException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error_code": exc.error_code, "message": exc.message},
        )

    app.include_router(tasks_router, prefix="/api/v1")

    async def _override_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db

    if "user" in active_user_holder:

        async def _override_user() -> Any:
            return active_user_holder["user"]

        app.dependency_overrides[get_current_active_user] = _override_user
        app.dependency_overrides[get_current_user_or_api_key] = _override_user

    app.dependency_overrides[get_task_execution] = lambda: SimpleNamespace()
    app.dependency_overrides[get_plan_service] = lambda: SimpleNamespace(
        require_feature=lambda *a, **k: None
    )
    return app


class TestTaskDraftApi:
    @pytest.mark.asyncio
    async def test_crud_cross_user_isolation_and_unauthenticated(
        self, tmp_path: pathlib.Path
    ) -> None:
        engine, factory = await _setup_db(tmp_path)
        u1_id = uuid4()
        u2_id = uuid4()
        async with factory() as db:
            db.add(User(id=u1_id, username="u1", email="u1@example.com", password_hash="h"))
            db.add(User(id=u2_id, username="u2", email="u2@example.com", password_hash="h"))
            await db.commit()

        try:
            # 未鉴权 401
            anon_app = _build_app(factory, {})
            anon_client = TestClient(anon_app)
            assert anon_client.get("/api/v1/tasks/draft").status_code == 401

            holder: dict[str, Any] = {
                "user": SimpleNamespace(id=u1_id, username="u1", role="user", is_active=True)
            }
            app = _build_app(factory, holder)
            client = TestClient(app)

            # 初始为空
            r0 = client.get("/api/v1/tasks/draft")
            assert r0.status_code == 200
            assert r0.json() is None

            # 创建草稿 revision = 1
            r1 = client.put(
                "/api/v1/tasks/draft",
                json={
                    "payload": {
                        "workflow_preset": "literature_review",
                        "topic": "检索增强生成综述",
                        "keywords": ["RAG", "检索增强"],
                        "output_language": "zh",
                    },
                    "base_revision": None,
                },
            )
            assert r1.status_code == 200, r1.text
            d1 = r1.json()
            assert d1["revision"] == 1
            assert d1["payload"]["topic"] == "检索增强生成综述"
            assert d1["payload"]["keywords"] == ["RAG", "检索增强"]

            # GET 读回
            r2 = client.get("/api/v1/tasks/draft")
            assert r2.status_code == 200
            assert r2.json()["revision"] == 1

            # 切换到用户 u2：看不到 u1 草稿
            holder["user"] = SimpleNamespace(id=u2_id, username="u2", role="user", is_active=True)
            r_other = client.get("/api/v1/tasks/draft")
            assert r_other.status_code == 200
            assert r_other.json() is None

            # 切回 u1 删除
            holder["user"] = SimpleNamespace(id=u1_id, username="u1", role="user", is_active=True)
            r_del = client.delete("/api/v1/tasks/draft")
            assert r_del.status_code == 204
            assert client.get("/api/v1/tasks/draft").json() is None
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_optimistic_lock_conflict_and_force_overwrite(
        self, tmp_path: pathlib.Path
    ) -> None:
        engine, factory = await _setup_db(tmp_path)
        uid = uuid4()
        async with factory() as db:
            db.add(User(id=uid, username="lockuser", email="lock@example.com", password_hash="h"))
            await db.commit()

        try:
            holder = {
                "user": SimpleNamespace(id=uid, username="lockuser", role="user", is_active=True)
            }
            client = TestClient(_build_app(factory, holder))

            first = client.put(
                "/api/v1/tasks/draft",
                json={"payload": {"topic": "第一版主题"}, "base_revision": None},
            )
            assert first.status_code == 200
            assert first.json()["revision"] == 1

            second = client.put(
                "/api/v1/tasks/draft",
                json={"payload": {"topic": "第二版主题"}, "base_revision": 1},
            )
            assert second.status_code == 200
            assert second.json()["revision"] == 2

            # 携带旧版本号 base_revision=1 → 409 DRAFT_CONFLICT 并回最新云端快照
            conflict = client.put(
                "/api/v1/tasks/draft",
                json={"payload": {"topic": "过期设备写入"}, "base_revision": 1},
            )
            assert conflict.status_code == 409
            detail = conflict.json()["detail"]
            assert detail["code"] == "DRAFT_CONFLICT"
            assert detail["cloud"]["revision"] == 2
            assert detail["cloud"]["payload"]["topic"] == "第二版主题"

            # base_revision=None 强制覆盖 → 成功且 revision 递增到 3
            forced = client.put(
                "/api/v1/tasks/draft",
                json={"payload": {"topic": "强制覆盖版本"}, "base_revision": None},
            )
            assert forced.status_code == 200
            assert forced.json()["revision"] == 3
            assert forced.json()["payload"]["topic"] == "强制覆盖版本"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_rejects_oversized_or_unknown_fields(self, tmp_path: pathlib.Path) -> None:
        engine, factory = await _setup_db(tmp_path)
        uid = uuid4()
        async with factory() as db:
            db.add(User(id=uid, username="valuser", email="val@example.com", password_hash="h"))
            await db.commit()

        try:
            holder = {
                "user": SimpleNamespace(id=uid, username="valuser", role="user", is_active=True)
            }
            client = TestClient(_build_app(factory, holder))

            unknown = client.put(
                "/api/v1/tasks/draft",
                json={"payload": {"topic": "合法主题", "hacker_field": "x"}, "base_revision": None},
            )
            assert unknown.status_code == 422

            # draft_text <= 30000 字符但中文每个字符 3 字节，25000 个中文 ≈ 75 KB > 64 KB
            oversized = client.put(
                "/api/v1/tasks/draft",
                json={"payload": {"topic": "超大草稿", "draft_text": "中" * 25000}},
            )
            assert oversized.status_code == 422
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_create_task_clears_user_draft(self, tmp_path: pathlib.Path) -> None:
        engine, factory = await _setup_db(tmp_path)
        uid = uuid4()
        async with factory() as db:
            db.add(User(id=uid, username="creator", email="creator@example.com", password_hash="h"))
            await db.commit()

        try:
            holder = {
                "user": SimpleNamespace(id=uid, username="creator", role="user", is_active=True)
            }
            client = TestClient(_build_app(factory, holder))

            saved = client.put(
                "/api/v1/tasks/draft",
                json={"payload": {"topic": "待提交的主题", "keywords": ["RAG"]}},
            )
            assert saved.status_code == 200
            assert client.get("/api/v1/tasks/draft").json() is not None

            with patch(
                "app.api.v1.endpoints.tasks.submit_review_task",
                AsyncMock(return_value=uuid4()),
            ):
                created = client.post(
                    "/api/v1/tasks",
                    json={
                        "topic": "待提交的主题测试",
                        "keywords": ["RAG"],
                        "max_documents": 10,
                        "output_language": "zh",
                    },
                )
            assert created.status_code == 201, created.text
            assert client.get("/api/v1/tasks/draft").json() is None
        finally:
            await engine.dispose()


class TestTaskDraftMigration:
    def test_single_alembic_head_points_to_task_draft_migration(self) -> None:
        script = ScriptDirectory.from_config(Config(str(BACKEND_DIR / "alembic.ini")))
        heads = script.get_heads()
        assert heads == ["d8b2f5c3a1e9"], f"期望唯一 head 为 d8b2f5c3a1e9，实际：{heads}"

    def test_upgrade_and_downgrade_are_idempotent(self) -> None:
        engine = create_engine("sqlite://")
        with engine.begin() as conn:
            conn.exec_driver_sql('CREATE TABLE "user" (id CHAR(32) PRIMARY KEY)')
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                mod = _load_migration()
                mod.upgrade()
                mod.upgrade()
                assert inspect(conn).has_table("task_draft")

                mod.downgrade()
                mod.downgrade()
                assert not inspect(conn).has_table("task_draft")
