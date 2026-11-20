"""BYOK 收尾：可选回退平台模型 + 生成能力门禁（Issue #28）。

锁定的口径：
1. 回退**只在凭据不可用**（401/402/403）时发生；429/超时/5xx 永远不回退；
2. 回退必须**粘住**：首次切换后整条任务都走平台，不再反复撞坏 key；
3. 回退必须**如实标注**（actual_provider/actual_model），否则界面还显示用户模型名；
4. 结算只退**平台实际消耗**（按回退时刻的 token 基线差值），BYOK 阶段花的
   用户自己的钱不能被算成平台退款；
5. 篇数在回退时补扣（BYOK 默认免篇数，回退平台后照扣）。
6. planner 路由保存时，未通过生成探测（json_mode）的凭据必须被拒绝。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.db.models  # noqa: F401
from app.api.v1.endpoints import llm_credentials, llm_routing
from app.core.exceptions import AppException
from app.core.security import create_access_token
from app.db.base import Base
from app.db.models.billing import Plan, UsageCounter
from app.db.models.llm_credential import LLMCredential
from app.db.models.task import Task
from app.db.models.user import User
from app.db.session import get_db
from app.llm.fallback import (
    LLMUnavailableError,
    PlatformFallbackClient,
    is_credential_unusable,
)
from app.orchestration.services.task_execution import TaskExecution

# ===== 纯函数：触发口径 =====


class _HttpError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(f"http {status}")
        self.status_code = status


@pytest.mark.parametrize(
    ("status", "expected"),
    [(401, True), (402, True), (403, True), (429, False), (500, False), (404, False)],
)
def test_credential_unusable_only_for_auth_statuses(status: int, expected: bool) -> None:
    assert is_credential_unusable(_HttpError(status)) is expected


class _Inner:
    """坏 key：前 N 次抛 401，之后成功（模拟「用户修好了 key」之外的一切）。"""

    def __init__(self, *, fail_times: int = 99) -> None:
        self.calls = 0
        self.fail_times = fail_times

    async def chat(self, messages, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise _HttpError(401)
        return {"from": "inner"}

    async def aclose(self) -> None:
        return None


class _Platform:
    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages, **kwargs):
        self.calls += 1
        return {"from": "platform"}


def _fallback(inner, platform, *, enabled=True, on_fallback=None):
    events: list[str] = []

    async def _cb(reason: str) -> None:
        events.append(reason)

    client = PlatformFallbackClient(
        inner,
        platform=lambda: platform,
        on_fallback=on_fallback or _cb,
        enabled=enabled,
    )
    return client, events


@pytest.mark.asyncio
async def test_fallback_switches_and_sticks() -> None:
    inner, platform = _Inner(), _Platform()
    client, events = _fallback(inner, platform)

    first = await client.chat([{"role": "user", "content": "hi"}])
    second = await client.chat([{"role": "user", "content": "hi again"}])

    assert first == {"from": "platform"}
    assert second == {"from": "platform"}
    assert platform.calls == 2
    assert len(events) == 1  # 只标注一次
    assert client.fell_back is True


@pytest.mark.asyncio
async def test_fallback_disabled_raises() -> None:
    inner, platform = _Inner(), _Platform()
    client, events = _fallback(inner, platform, enabled=False)

    with pytest.raises(_HttpError):
        await client.chat([])
    assert platform.calls == 0
    assert events == []


@pytest.mark.asyncio
async def test_rate_limit_never_falls_back() -> None:
    class _Limited:
        async def chat(self, messages, **kwargs):
            raise _HttpError(429)

    inner, platform = _Limited(), _Platform()
    client, events = _fallback(inner, platform)

    with pytest.raises(_HttpError):
        await client.chat([])
    assert platform.calls == 0
    assert events == []


@pytest.mark.asyncio
async def test_platform_unavailable_does_not_mark_fallback() -> None:
    """平台没配置时不能把任务标成「已回退」——标注了却没走到，账就乱了。"""
    inner = _Inner()
    events: list[str] = []

    async def _cb(reason: str) -> None:
        events.append(reason)

    client = PlatformFallbackClient(
        inner,
        platform=lambda: None,
        on_fallback=_cb,
        enabled=True,
    )
    with pytest.raises(LLMUnavailableError):
        await client.chat([])
    assert events == []
    assert client.fell_back is False


# ===== TaskExecution：标注 / 计费 / 结算 =====


class _Quota:
    def __init__(self) -> None:
        self.reserved: list[int] = []
        self.refunded: list[int] = []

    async def check_and_consume(self, user_id, *, tokens: int) -> None:
        self.reserved.append(tokens)

    async def refund(self, user_id, *, tokens: int) -> None:
        self.refunded.append(tokens)


class _PlatformStub:
    provider = "siliconflow"
    model = "glm-5.3"


class _DummyContainer:
    def __init__(self, factory) -> None:
        self.session_factory = factory

    def track(self, *args, **kwargs) -> None:  # pragma: no cover - 测试不跑后台协程
        raise AssertionError("不应启动后台协程")


@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


async def _user(factory, name: str = "byok-user") -> User:
    async with factory() as session:
        user = User(
            username=name, email=f"{name}@example.com", password_hash="x", role="user", is_active=True
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _seed_plan(factory) -> None:
    """free 套餐缺种子时服务会内存兜底；这里显式建一份，便于断言扣减。"""
    async with factory() as session:
        session.add(
            Plan(id="free", name="免费版", monthly_reviews=5, is_active=True)
        )
        await session.commit()


async def _task(factory, user: User, *, status: str = "running") -> Task:
    async with factory() as session:
        task = Task(
            user_id=user.id,
            title="回退用例",
            description="d",
            task_type="literature_review",
            status=status,
            config={},
            is_byok=True,
            llm_credential_id=None,
            model_snapshot={"is_byok": True, "name": "用户 key", "provider": "deepseek", "model": "deepseek-chat"},
            used_tokens=50_000,
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        return task


def _execution(factory, quota: _Quota) -> TaskExecution:
    execution = TaskExecution(_DummyContainer(factory), quota_service=quota)  # type: ignore[arg-type]
    execution._platform_llm = lambda: _PlatformStub()  # type: ignore[method-assign]
    return execution


@pytest.mark.asyncio
async def test_apply_platform_fallback_marks_and_bills(factory) -> None:
    await _seed_plan(factory)
    user = await _user(factory)
    task = await _task(factory, user)
    quota = _Quota()
    execution = _execution(factory, quota)

    applied = await execution._apply_platform_fallback(task_id=task.id, reason="HTTP 401")
    assert applied is True

    async with factory() as session:
        row = await session.get(Task, task.id)
        assert row.actual_provider == "siliconflow"
        assert row.actual_model == "glm-5.3"
        assert row.fallback_token_base == 50_000
        # 篇数：BYOK 默认免篇数，回退平台后照扣
        counter = (
            await session.execute(select(UsageCounter.reviews_used))
        ).scalar_one()

    assert counter == 1
    assert quota.reserved == [100_000]

    # 幂等：第二次不重复计费
    applied_again = await execution._apply_platform_fallback(task_id=task.id, reason="HTTP 401")
    assert applied_again is False
    assert quota.reserved == [100_000]


@pytest.mark.asyncio
async def test_settle_refunds_only_platform_usage(factory) -> None:
    """基线差值退款：BYOK 阶段花的自己的钱不能被算成平台退款。"""
    await _seed_plan(factory)
    user = await _user(factory)
    task = await _task(factory, user)
    quota = _Quota()
    execution = _execution(factory, quota)

    await execution._apply_platform_fallback(task_id=task.id, reason="HTTP 401")
    # 回退后又消耗了 30k（总量 80k，基线 50k → 平台实际 30k）
    await execution.add_used_tokens(task.id, 30_000)
    await execution.settle_quota(task.id)

    assert quota.refunded == [100_000 - 30_000]

    async with factory() as session:
        row = await session.get(Task, task.id)
        assert row.quota_settled is True


@pytest.mark.asyncio
async def test_settle_byok_without_fallback_never_refunds(factory) -> None:
    user = await _user(factory)
    task = await _task(factory, user)
    quota = _Quota()
    execution = _execution(factory, quota)

    await execution.settle_quota(task.id)
    assert quota.refunded == []


# ===== API：开关持久化 + 生成探测 + planner 门禁 =====


async def _credential(
    factory,
    user: User,
    *,
    allow_fallback: bool = False,
    json_mode: bool | None = None,
    name: str = "我的 key",
) -> LLMCredential:
    from app.security.credential_cipher import CredentialCipher

    cipher = CredentialCipher.from_settings()
    async with factory() as session:
        caps: dict[str, Any] = {"text": True, "image": False, "reasoning_tiers": []}
        if json_mode is not None:
            caps["json_mode"] = json_mode
        record = LLMCredential(
            user_id=user.id,
            name=name,
            provider="deepseek",
            base_url="https://api.deepseek.com/v1",
            model="deepseek-chat",
            api_key_ciphertext=cipher.encrypt("sk-test-real", user_id=user.id),
            api_key_hint="sk-…fake",
            key_fingerprint="f" * 32,
            status="active",
            capabilities=caps,
            allow_platform_fallback=allow_fallback,
        )
        session.add(record)
        await session.commit()
        await session.refresh(record)
        return record


def _client(factory) -> TestClient:
    from fastapi.testclient import TestClient

    application = FastAPI()
    application.include_router(llm_credentials.router, prefix="/api/v1")
    application.include_router(llm_routing.router, prefix="/api/v1")

    async def _get_db():
        async with factory() as session:
            yield session

    application.dependency_overrides[get_db] = _get_db

    @application.exception_handler(AppException)
    async def _handle(_: Any, exc: AppException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"message": exc.message})

    return TestClient(application)


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


@pytest_asyncio.fixture
async def api(factory):
    await _seed_plan(factory)
    user = await _user(factory, "api-user")
    credential = await _credential(factory, user)
    return {"client": _client(factory), "user": user, "credential": credential, "factory": factory}


def test_allow_fallback_persists(api) -> None:
    client, user, credential = api["client"], api["user"], api["credential"]

    assert client.get(
        f"/api/v1/llm-credentials/{str(credential.id)}", headers=_auth(user)
    ).json()["allow_platform_fallback"] is False

    patched = client.patch(
        f"/api/v1/llm-credentials/{str(credential.id)}",
        headers=_auth(user),
        json={"allow_platform_fallback": True},
    )
    assert patched.status_code == 200
    assert patched.json()["allow_platform_fallback"] is True


def test_planner_gate_rejects_without_generation_probe(api) -> None:
    client, user, credential = api["client"], api["user"], api["credential"]
    response = client.put(
        "/api/v1/llm-routing",
        headers=_auth(user),
        json={"routes": [{"node_type": "planner", "credential_id": str(credential.id)}]},
    )
    assert response.status_code == 400
    assert "planner" in response.json()["detail"]


def test_planner_gate_allows_probed_credential(api) -> None:
    client, user, credential = api["client"], api["user"], api["credential"]
    # 探测写入 json_mode（模拟探测成功）
    async def _mark():
        async with api["factory"]() as session:
            row = await session.get(LLMCredential, credential.id)
            caps = dict(row.capabilities or {})
            caps["json_mode"] = True
            row.capabilities = caps
            await session.commit()

    asyncio.run(_mark())

    response = client.put(
        "/api/v1/llm-routing",
        headers=_auth(user),
        json={"routes": [{"node_type": "planner", "credential_id": str(credential.id)}]},
    )
    assert response.status_code == 200


def test_non_planner_route_ignores_generation_gate(api) -> None:
    client, user, credential = api["client"], api["user"], api["credential"]
    response = client.put(
        "/api/v1/llm-routing",
        headers=_auth(user),
        json={"routes": [{"node_type": "extract", "credential_id": str(credential.id)}]},
    )
    assert response.status_code == 200


def test_generation_probe_endpoint_writes_json_mode(api, monkeypatch) -> None:
    client, user, credential = api["client"], api["user"], api["credential"]

    class _Result:
        ok = True
        latency_ms = 320
        error = None
        raw = '{"status": "ok", "count": 2}'
        auth_error = False

    monkeypatch.setattr(
        llm_credentials, "probe_generation", lambda **kwargs: _as_coroutine(_Result())
    )

    response = client.post(
        f"/api/v1/llm-credentials/{str(credential.id)}/probe-generation",
        headers=_auth(user),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True and body["json_mode"] is True

    async def _check():
        async with api["factory"]() as session:
            row = await session.get(LLMCredential, credential.id)
            return (row.capabilities or {}).get("json_mode")

    assert asyncio.run(_check()) is True


def test_generation_probe_auth_failure_counts(api, monkeypatch) -> None:
    client, user, credential = api["client"], api["user"], api["credential"]

    class _Result:
        ok = False
        latency_ms = 40
        error = "API Key 被拒绝（HTTP 401）"
        raw = None
        auth_error = True

    monkeypatch.setattr(
        llm_credentials, "probe_generation", lambda **kwargs: _as_coroutine(_Result())
    )

    response = client.post(
        f"/api/v1/llm-credentials/{str(credential.id)}/probe-generation",
        headers=_auth(user),
    )
    assert response.status_code == 200
    assert response.json()["ok"] is False

    async def _check():
        async with api["factory"]() as session:
            row = await session.get(LLMCredential, credential.id)
            return row.consecutive_failures, (row.capabilities or {}).get("json_mode")

    failures, json_mode = asyncio.run(_check())
    assert failures == 1
    assert json_mode is False


def _as_coroutine(value):
    async def _inner():
        return value

    return _inner()


def test_probe_endpoint_requires_ownership(api) -> None:
    stranger = asyncio.run(_user(api["factory"], "stranger"))
    response = api["client"].post(
        f"/api/v1/llm-credentials/{api['credential'].id}/probe-generation",
        headers=_auth(stranger),
    )
    assert response.status_code == 404


