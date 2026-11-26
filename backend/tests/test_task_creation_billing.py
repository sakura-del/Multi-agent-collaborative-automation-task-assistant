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
from app.core.config import get_settings
from app.core.exceptions import AppException
from app.core.quota import QuotaService
from app.core.security import create_access_token
from app.db.base import Base
from app.db.models.billing import SEED_PLANS, Plan
from app.db.models.task import Task
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


def _insert_task(api, user: User, *, tokens: int = 42, title: str = "导出任务") -> None:
    """直接落一条任务：`_FakeExecution` 不写库，导出用例需要真实行。"""

    async def _run() -> None:
        async with api.state.factory() as session:
            session.add(
                Task(
                    user_id=user.id,
                    title=title,
                    description="d",
                    task_type="literature_review",
                    status="completed",
                    config={},
                    used_tokens=tokens,
                    quota_settled=True,
                )
            )
            await session.commit()

    asyncio.run(_run())


def _subscribe(api, user: User, plan_id: str) -> None:
    asyncio.run(PlanService(api.state.factory).subscribe(user.id, plan_id, note="测试"))


class TestPlanQuotaBlocksCreate:
    def test_free_fourth_task_returns_plan_quota_error(self, api) -> None:
        """免费档 3 篇/月：第 4 篇返回 **PLAN_QUOTA_EXCEEDED**。"""
        user = _user(api, "q1")
        client = api.state.client

        for idx in range(3):
            ok = client.post("/api/v1/tasks", headers=_headers(user), json=_payload(f"ok{idx}"))
            assert ok.status_code == 201, ok.text

        fourth = client.post("/api/v1/tasks", headers=_headers(user), json=_payload("fourth"))
        assert fourth.status_code == 429, fourth.text
        body = fourth.json()
        assert body["error_code"] == "PLAN_QUOTA_EXCEEDED"
        assert body["details"]["limit"] == 3
        assert body["details"]["used"] == 3

    def test_rejected_task_not_submitted(self, api) -> None:
        """**拒绝时不留下僵尸任务**：第 4 次被拒时 `submit` 不应增加。"""
        user = _user(api, "q2")
        client = api.state.client
        for idx in range(3):
            client.post("/api/v1/tasks", headers=_headers(user), json=_payload(f"a{idx}"))
        assert api.state.execution.submit_calls == 3

        client.post("/api/v1/tasks", headers=_headers(user), json=_payload("b"))

        assert api.state.execution.submit_calls == 3, "被拒绝的请求不能提交任务"

    def test_upgrade_unblocks(self, api) -> None:
        user = _user(api, "q3")
        client = api.state.client
        for idx in range(3):
            client.post("/api/v1/tasks", headers=_headers(user), json=_payload(f"a{idx}"))
        assert (
            client.post("/api/v1/tasks", headers=_headers(user), json=_payload("b")).status_code
            == 429
        )

        _subscribe(api, user, "student")

        assert (
            client.post("/api/v1/tasks", headers=_headers(user), json=_payload("c")).status_code
            == 201
        )

    def test_free_plan_caps_platform_max_documents_at_15(self, api) -> None:
        """免费版平台模型单次上限 15 篇（Issue #61 P0-3）；升级学生版后解锁 20 篇。"""
        user = _user(api, "q_doc_cap")
        client = api.state.client
        over_cap = {**_payload("over"), "max_documents": 20}
        rejected = client.post("/api/v1/tasks", headers=_headers(user), json=over_cap)
        assert rejected.status_code == 422, rejected.text
        assert "15" in rejected.text

        at_cap = {**_payload("at15"), "max_documents": 15}
        allowed = client.post("/api/v1/tasks", headers=_headers(user), json=at_cap)
        assert allowed.status_code == 201, allowed.text

        _subscribe(api, user, "student")
        unlocked = client.post("/api/v1/tasks", headers=_headers(user), json=over_cap)
        assert unlocked.status_code == 201, unlocked.text


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
        # 先耗尽免费档的 3 篇
        for idx in range(3):
            assert (
                client.post(
                    "/api/v1/tasks", headers=_headers(user), json=_payload(f"a{idx}")
                ).status_code
                == 201
            )
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

    def test_usage_exposes_entitlements_over_http(self, api) -> None:
        """HTTP 层必须透出当前套餐权益（2026-10-26 套餐页重设计）。

        服务层已测过（`test_plan_service.py::TestUsagePayloadForPlanPage`），
        这里锁 HTTP 契约：`UsageOut` 少一个字段，前端「当前套餐」卡就会退化成
        「只有一个名字」——而那正是本次要修的缺陷。
        """
        user = _user(api, "u2")
        _subscribe(api, user, "pro")
        client = api.state.client

        resp = client.get("/api/v1/billing/usage", headers=_headers(user))

        assert resp.status_code == 200, resp.text
        body = resp.json()
        # 专业版：团队 ✅、API ✅、5 工作区、10 席位、¥69
        assert body["allow_team"] is True
        assert body["allow_api_access"] is True
        assert body["max_workspaces"] == 5
        assert body["max_seats_per_workspace"] == 10
        assert body["price_cents"] == 6900
        # 订阅信息：到期时间与已购席位
        assert body["period_end"] is not None
        assert body["seats_purchased"] == 1

    def test_usage_exposes_model_split_over_http(self, api) -> None:
        """HTTP 契约必须带 `model_usage`（Issue #15 P2 分账）。

        服务层已测过计算口径，这里只锁「端点有没有把它带出去」：
        `UsageOut` 漏字段时，前端分账卡会整块消失，而用户看不出原因。
        """
        user = _user(api, "u_split")
        client = api.state.client

        resp = client.get("/api/v1/billing/usage", headers=_headers(user))

        assert resp.status_code == 200, resp.text
        body = resp.json()
        split = body["model_usage"]
        assert split["period_key"] == body["period_key"]
        assert split["platform"] == {"tasks": 0, "tokens": 0}
        assert split["byok"] == {"tasks": 0, "tokens": 0}
        assert split["total"] == {"tasks": 0, "tokens": 0}

    def test_usage_export_returns_csv(self, api) -> None:
        """账单导出：CSV + BOM + 附件文件名 + 汇总/明细都在。"""
        user = _user(api, "u_export")
        _insert_task(api, user, tokens=42, title="计费套餐测试 export")
        client = api.state.client

        resp = client.get("/api/v1/billing/usage/export", headers=_headers(user))

        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"].startswith("text/csv")
        assert "attachment" in resp.headers["content-disposition"]
        body = resp.content.decode("utf-8-sig")
        assert body.startswith("类型,周期,模型来源,任务数,Token数")
        assert "汇总,2026-" in body
        assert "明细,2026-" in body
        assert "计费套餐测试 export" in body
        assert ",42," in body

    def test_usage_cost_estimates_by_model(self, api, monkeypatch) -> None:
        """HTTP 契约：成本估算带上模型、单价与「估算」标记。"""
        settings = get_settings()
        monkeypatch.setattr(settings.llm, "default_provider", "openai")
        monkeypatch.setattr(settings.llm, "default_model", "gpt-4o-mini")
        user = _user(api, "u_cost")
        _insert_task(api, user, tokens=1_000_000)
        client = api.state.client

        resp = client.get("/api/v1/billing/usage/cost", headers=_headers(user))

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["currency"] == "USD"
        assert body["unpriced_tokens"] == 0
        row = body["models"][0]
        assert row["model"] == "gpt-4o-mini"
        assert row["model_source"] == "platform_default"
        assert row["priced"] is True
        # 1M token 按 70/30 拆分：0.15*0.7 + 0.60*0.3 = 0.285
        assert row["total_cost"] == "0.285000"

    def test_usage_export_rejects_bad_period(self, api) -> None:
        user = _user(api, "u_export_bad")
        client = api.state.client

        resp = client.get("/api/v1/billing/usage/export?period=2026-13", headers=_headers(user))

        assert resp.status_code == 422, resp.text

    def test_usage_period_end_is_null_for_free_plan(self, api) -> None:
        """免费档没有订阅行 → `period_end` / `seats_purchased` 必须是 null。

        为什么不编一个「本月底」：那是假日期。用户会以为「到 10 月底套餐就到期」，
        而免费档根本没有到期概念。null 让前端能显示「免费档（无到期日）」。
        """
        user = _user(api, "u3")
        client = api.state.client

        resp = client.get("/api/v1/billing/usage", headers=_headers(user))

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["plan_id"] == "free"
        assert body["period_end"] is None
        assert body["seats_purchased"] is None
        # 免费档额度 3 篇，用掉 0 篇
        assert body["monthly_reviews"] == 3
        assert body["reviews_remaining"] == 3
        assert body["max_documents_per_task"] == 15
