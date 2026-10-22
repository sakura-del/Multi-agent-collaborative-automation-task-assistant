"""建任务时的双配额接线测试（Phase 2 · 计费套餐 Task 3）。

## 本文件锁定**最重要的一件事**：两条限制不能混淆

| 限制 | 存储 | 错误码 | 用户该怎么办 |
| --- | --- | --- | --- |
| **篇数（月）** | 数据库 | `PLAN_QUOTA_EXCEEDED` | 升级套餐 / 等下月 |
| **token（日）** | Redis | `TOKEN_BUDGET_EXCEEDED` | 明天再试（买套餐也没用） |

如果两者都用 `QUOTA_EXCEEDED`，用户看到 429 会以为「买套餐能解决」，
买完发现还是不行 —— 这是真实的客诉来源。

## 顺序为什么重要

**先查篇数，再查 token**。反过来的话，用户会先看到「token 预算用尽」
这种他无法理解的错误，而实际上他真正撞的是篇数限制。

## 拒绝时不留下僵尸任务

所有配额检查必须在 `execution.submit` **之前**。先建后拒会留下一个
注定失败的任务，且用户已消耗一次建任务限额。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

import app.db.models  # noqa: F401
from app.api.v1.endpoints import tasks as tasks_endpoint
from app.billing.services.plan_service import PlanService
from app.core.exceptions import AppException
from app.core.quota import QuotaService
from app.core.security import create_access_token
from app.db.base import Base
from app.db.models.billing import SEED_PLANS, Plan
from app.db.models.user import User
from app.db.session import get_db


class _FakeExecution:
    """记录 submit 调用次数（验证「拒绝时不提交」）。"""

    def __init__(self) -> None:
        self.submit_calls = 0

    async def submit(self, user_id, request):  # type: ignore[no-untyped-def]
        self.submit_calls += 1
        return uuid.uuid4()

    async def list_tasks(self, user_id, *, skip=0, limit=20, workspace_ids=()):  # type: ignore[no-untyped-def]
        return []


class _FakeRedis:
    """极简 Redis 假实现（只支持配额 Lua 的 GET/INCRBY/EXPIRE 语义）。"""

    def __init__(self, *, allow: bool) -> None:
        self.allow = allow
        self.store: dict[str, int] = {}

    async def eval(self, script: str, numkeys: int, key: str, *args: Any) -> Any:
        if "INCRBY" in script:
            amount = int(args[0])
            limit = int(args[1])
            used = self.store.get(key, 0)
            if used + amount > limit:
                return [0, used]
            self.store[key] = used + amount
            return [1, self.store[key]]
        # refund
        amount = int(args[0])
        self.store[key] = max(0, self.store.get(key, 0) - amount)
        return self.store[key]


@pytest.fixture
def api() -> Any:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _create_all() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            for spec in SEED_PLANS:
                session.add(Plan(**spec, is_active=True))
            await session.commit()

    asyncio.run(_create_all())

    async def _get_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    execution = _FakeExecution()
    # token 配额：默认宽松（篇数限制先触发）
    redis = _FakeRedis(allow=True)
    quota = QuotaService(redis_client=redis, daily_limit=10_000_000)

    application = FastAPI()
    application.include_router(tasks_endpoint.router, prefix="/api/v1")
    # 用量端点（Task 3 的 TestUsageEndpoint 需要）
    from app.api.v1.endpoints import billing as billing_endpoint

    application.include_router(billing_endpoint.router, prefix="/api/v1")

    @application.exception_handler(AppException)
    async def _handle(_: Any, exc: AppException) -> Any:
        headers: dict[str, str] = {}
        retry_after = getattr(exc, "retry_after", None)
        if retry_after is not None:
            headers["Retry-After"] = str(max(1, int(retry_after)))
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error_code": exc.error_code,
                "message": exc.message,
                "details": exc.details,
            },
            headers=headers or None,
        )

    application.dependency_overrides[get_db] = _get_db
    application.dependency_overrides[tasks_endpoint.get_task_execution] = lambda: execution
    application.dependency_overrides[tasks_endpoint.get_quota_service] = lambda: quota
    # 套餐服务走独立 session（见 get_plan_service 的 docstring）
    application.state.plan_service = PlanService(factory)
    application.state.factory = factory
    application.state.execution = execution
    application.state.redis = redis
    application.state.quota = quota
    application.state.client = TestClient(application)
    return application


async def _make_user(factory, name: str) -> User:
    async with factory() as session:
        user = User(username=name, email=f"{name}@example.com", password_hash="x", is_active=True)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


def _user(api, name: str) -> User:
    return asyncio.run(_make_user(api.state.factory, name))


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


def _payload(suffix: str = "") -> dict[str, Any]:
    return {
        "topic": f"计费套餐测试 {suffix}".strip(),
        "keywords": ["billing"],
        "max_documents": 2,
    }


def _subscribe(api, user: User, plan_id: str) -> None:
    asyncio.run(PlanService(api.state.factory).subscribe(user.id, plan_id, note="测试"))


class TestPlanQuotaBlocksCreate:
    def test_free_second_task_returns_plan_quota_error(self, api) -> None:
        """免费档 1 篇/月：第 2 篇返回 **PLAN_QUOTA_EXCEEDED**。"""
        user = _user(api, "q1")
        client = api.state.client

        first = client.post("/api/v1/tasks", headers=_headers(user), json=_payload("a"))
        assert first.status_code == 201, first.text

        second = client.post("/api/v1/tasks", headers=_headers(user), json=_payload("b"))
        assert second.status_code == 429, second.text
        body = second.json()
        assert body["error_code"] == "PLAN_QUOTA_EXCEEDED"
        assert body["details"]["limit"] == 1
        assert body["details"]["used"] == 1

    def test_rejected_task_not_submitted(self, api) -> None:
        """**拒绝时不留下僵尸任务**：`submit` 不应被调用第二次。"""
        user = _user(api, "q2")
        client = api.state.client
        client.post("/api/v1/tasks", headers=_headers(user), json=_payload("a"))
        assert api.state.execution.submit_calls == 1

        client.post("/api/v1/tasks", headers=_headers(user), json=_payload("b"))

        assert api.state.execution.submit_calls == 1, "被拒绝的请求不能提交任务"

    def test_upgrade_unblocks(self, api) -> None:
        user = _user(api, "q3")
        client = api.state.client
        client.post("/api/v1/tasks", headers=_headers(user), json=_payload("a"))
        assert (
            client.post("/api/v1/tasks", headers=_headers(user), json=_payload("b")).status_code
            == 429
        )

        _subscribe(api, user, "student")

        assert (
            client.post("/api/v1/tasks", headers=_headers(user), json=_payload("c")).status_code
            == 201
        )


class TestTokenBudgetDistinguished:
    def test_token_budget_uses_different_error_code(self, api) -> None:
        """**关键**：token 护栏用 `TOKEN_BUDGET_EXCEEDED`，与篇数错误码不同。"""
        user = _user(api, "t1")
        _subscribe(api, user, "pro")  # 篇数宽松
        # token 护栏调到极小，让预扣 100k 必然超限
        api.state.quota = QuotaService(redis_client=api.state.redis, daily_limit=1)
        api.dependency_overrides[tasks_endpoint.get_quota_service] = lambda: api.state.quota
        client = api.state.client

        resp = client.post("/api/v1/tasks", headers=_headers(user), json=_payload("a"))

        assert resp.status_code == 429, resp.text
        body = resp.json()
        assert (
            body["error_code"] == "TOKEN_BUDGET_EXCEEDED"
        ), f"token 护栏必须用独立错误码，实际：{body['error_code']}"
        assert body["error_code"] != "PLAN_QUOTA_EXCEEDED"

    def test_token_budget_message_mentions_waiting(self, api) -> None:
        """token 护栏的消息应说明「明天再试」，而不是「升级套餐」。"""
        user = _user(api, "t2")
        _subscribe(api, user, "pro")
        api.state.quota = QuotaService(redis_client=api.state.redis, daily_limit=1)
        api.dependency_overrides[tasks_endpoint.get_quota_service] = lambda: api.state.quota
        client = api.state.client

        resp = client.post("/api/v1/tasks", headers=_headers(user), json=_payload("a"))

        body = resp.json()
        assert "套餐" not in body["message"], "token 护栏不该提套餐（买套餐也解决不了）"
        assert resp.headers.get("Retry-After"), "应带 Retry-After"


class TestOrderOfChecks:
    def test_plan_quota_checked_before_token_budget(self, api) -> None:
        """**顺序**：篇数先于 token。

        两者都超限时，用户应看到「篇数用完」（可理解、可解决），
        而不是「token 预算用尽」（无法自行解决）。
        """
        user = _user(api, "o1")
        client = api.state.client
        # 先耗尽免费档的 1 篇
        client.post("/api/v1/tasks", headers=_headers(user), json=_payload("a"))
        # 再把 token 护栏也调到极小（两者同时超限）
        api.state.quota = QuotaService(redis_client=api.state.redis, daily_limit=1)
        api.dependency_overrides[tasks_endpoint.get_quota_service] = lambda: api.state.quota

        resp = client.post("/api/v1/tasks", headers=_headers(user), json=_payload("b"))

        assert resp.status_code == 429
        assert (
            resp.json()["error_code"] == "PLAN_QUOTA_EXCEEDED"
        ), "篇数限制应优先暴露（用户可解决）"


class TestUsageEndpoint:
    def test_usage_reflects_consumption(self, api) -> None:
        """用量接口应反映真实消耗（前端进度条依赖）。"""
        user = _user(api, "u1")
        _subscribe(api, user, "student")
        client = api.state.client
        client.post("/api/v1/tasks", headers=_headers(user), json=_payload("a"))

        resp = client.get("/api/v1/billing/usage", headers=_headers(user))

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["reviews_used"] == 1
        assert body["monthly_reviews"] == 10
        assert body["reviews_remaining"] == 9
        assert body["plan_id"] == "student"
