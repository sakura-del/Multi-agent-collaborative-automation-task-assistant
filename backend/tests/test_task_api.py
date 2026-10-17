"""任务 API 契约测试（Plan 2 Task 5）。

覆盖：
- 创建/列表/详情、规划确认（revision 冲突）、pause/cancel 控制；
- retry-node 与 supply-input 的参数校验；
- 401 未认证、404 跨用户访问、未知字段拒绝；
- 事件回放 after/next_event_id 语义。

测试使用文件型 SQLite（WAL）与 TestClient 的 portal 事件循环，
后台规划/执行任务与 HTTP 请求共享同一事件循环。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.db.models  # noqa: F401  确保全部 ORM 注册到 Base.metadata
from app.db.base import Base
from app.db.session import get_db
from app.orchestration.agents.planner import PlannerAgent
from app.orchestration.base_agent import BaseAgent
from app.orchestration.contracts import TaskStatus
from app.orchestration.events import InMemoryEventPublisher
from app.orchestration.runtime import RuntimeContainer
from app.orchestration.services.task_execution import TaskExecution

# ===== 测试替身 =====


def _plan_payload() -> dict[str, Any]:
    return {
        "version": 1,
        "topic": "检索增强生成证据核验",
        "research_questions": [
            "RAG 如何提升证据可靠性？",
            "如何度量证据可追溯性？",
            "多智能体协作如何影响综述质量？",
        ],
        "search_queries": ["RAG evidence verification"],
        "acceptance_criteria": ["引用可回溯"],
        "nodes": [
            {"id": "n1", "type": "search", "title": "检索文献", "dependencies": []},
            {"id": "n2", "type": "extract", "title": "提取证据", "dependencies": ["n1"]},
            {"id": "n3", "type": "write", "title": "撰写综述", "dependencies": ["n2"]},
            {"id": "n4", "type": "validate", "title": "校验引用", "dependencies": ["n3"]},
            {"id": "n5", "type": "review", "title": "汇总复盘", "dependencies": ["n4"]},
        ],
    }


class _DeterministicPlanner(BaseAgent):
    @property
    def agent_type(self) -> str:
        return "planner"

    async def _run(self, input_data: dict[str, Any]) -> dict[str, Any]:
        from app.orchestration.contracts import TaskPlan

        plan = TaskPlan.model_validate(_plan_payload())
        return {
            "plan": plan.model_dump(mode="json"),
            "dag": PlannerAgent.build_dag(plan).to_dict(),
        }


class _RecordingExecutor(BaseAgent):
    def __init__(self, calls: list[dict[str, Any]] | None = None) -> None:
        super().__init__()
        self.calls = calls if calls is not None else []

    @property
    def agent_type(self) -> str:
        return "executor"

    async def _run(self, input_data: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(dict(input_data))
        return {"result": f"结果-{input_data['node_id']}"}


class _SlowExecutor(BaseAgent):
    """阻塞执行器：用于在任务 RUNNING 时验证 pause 控制。"""

    def __init__(self, delay: float = 5.0) -> None:
        super().__init__()
        self.delay = delay

    @property
    def agent_type(self) -> str:
        return "executor"

    async def _run(self, input_data: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(self.delay)
        return {"result": "slow"}


class _PassingValidator(BaseAgent):
    @property
    def agent_type(self) -> str:
        return "validator"

    async def _run(self, input_data: dict[str, Any]) -> dict[str, Any]:
        return {"passed": True, "score": 90.0, "checks": [], "retryable": False, "feedback": ""}


class _StaticReviewer(BaseAgent):
    @property
    def agent_type(self) -> str:
        return "reviewer"

    async def _run(self, input_data: dict[str, Any]) -> dict[str, Any]:
        return {"summary": "任务执行成功。", "stats": {"error_count": 0}, "root_cause": {}}


# ===== 夹具 =====


@pytest.fixture
def api_env(tmp_path: Path) -> Iterator[Any]:
    """带独立文件型 SQLite 的 TestClient + 运行时安装助手。"""
    from app.main import app

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'task-api.db'}",
        connect_args={"timeout": 10},
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    state = {"created": False}

    async def override_get_db():
        if not state["created"]:
            async with engine.begin() as connection:
                await connection.exec_driver_sql("PRAGMA journal_mode=WAL")
                await connection.run_sync(Base.metadata.create_all)
            state["created"] = True
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    client.__enter__()

    installed: list[RuntimeContainer] = []

    def install_runtime(
        *,
        planner: BaseAgent | None = None,
        executor: BaseAgent | None = None,
    ) -> TaskExecution:
        executor_agent = executor or _RecordingExecutor()
        validator = _PassingValidator()
        reviewer = _StaticReviewer()
        agents = {
            "executor": executor_agent,
            "validator": validator,
            "reviewer": reviewer,
        }

        def agent_factory(agent_type: str) -> Any:
            return agents[agent_type]

        container = RuntimeContainer(
            session_factory=session_factory,
            planner_factory=lambda: planner or _DeterministicPlanner(),
            agent_factory=agent_factory,
            event_publisher=InMemoryEventPublisher(),
        )
        execution = TaskExecution(container)
        app.state.container = container
        app.state.runtime = execution
        installed.append(container)
        return execution

    yield client, install_runtime

    for container in installed:
        try:
            client.portal.call(container.close)
        except Exception:
            pass
    client.__exit__(None, None, None)
    app.dependency_overrides.clear()
    asyncio.run(engine.dispose())


# ===== 辅助函数 =====


def _register_and_login(client: TestClient, username: str) -> str:
    client.post(
        "/api/v1/auth/register",
        json={
            "username": username,
            "email": f"{username}@test.com",
            "password": "password123",
        },
    )
    resp = client.post(
        "/api/v1/auth/login",
        json={"account": username, "password": "password123"},
    )
    assert resp.status_code == 200
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create(client: TestClient, token: str, **overrides: Any) -> Any:
    payload: dict[str, Any] = {
        "topic": "检索增强生成证据核验",
        "keywords": ["RAG", "证据核验"],
    }
    payload.update(overrides)
    return client.post("/api/v1/tasks", json=payload, headers=_auth(token))


def _wait_status(
    client: TestClient,
    token: str,
    task_id: str,
    expected: set[str],
    *,
    timeout: float = 5.0,
) -> dict[str, Any]:
    deadline = time.time() + timeout
    last: dict[str, Any] = {}
    while time.time() < deadline:
        resp = client.get(f"/api/v1/tasks/{task_id}", headers=_auth(token))
        assert resp.status_code == 200
        last = resp.json()
        if last["status"] in expected:
            return last
        time.sleep(0.01)
    raise AssertionError(f"等待状态 {expected} 超时，当前: {last.get('status')}")


# ===== 测试 =====


class TestAuthenticationRequired:
    def test_all_task_routes_require_token(self, api_env) -> None:
        client, install_runtime = api_env
        install_runtime()
        missing = uuid.uuid4()

        assert (
            client.post("/api/v1/tasks", json={"topic": "测试主题", "keywords": ["k"]}).status_code
            == 401
        )
        assert client.get("/api/v1/tasks").status_code == 401
        assert client.get(f"/api/v1/tasks/{missing}").status_code == 401
        assert client.get(f"/api/v1/tasks/{missing}/events").status_code == 401
        assert (
            client.post(
                f"/api/v1/tasks/{missing}/control",
                json={"command": "pause"},
            ).status_code
            == 401
        )


class TestCreateAndQuery:
    def test_create_persists_planning_and_lists(self, api_env) -> None:
        client, install_runtime = api_env
        install_runtime()
        token = _register_and_login(client, "alice")

        resp = _create(client, token)
        assert resp.status_code == 201
        body = resp.json()
        task_id = body["task_id"]
        uuid.UUID(task_id)
        assert body["status"] == TaskStatus.PLANNING.value

        detail = _wait_status(client, token, task_id, {TaskStatus.WAITING_APPROVAL.value})
        assert detail["plan_version"] == 1
        assert detail["revision"] == 1
        assert len(detail["subtasks"]) == 5
        assert detail["plan"]["topic"] == "检索增强生成证据核验"

        listing = client.get("/api/v1/tasks", headers=_auth(token))
        assert listing.status_code == 200
        items = listing.json()
        assert [item["task_id"] for item in items] == [task_id]

        paged = client.get("/api/v1/tasks?limit=1&skip=1", headers=_auth(token))
        assert paged.status_code == 200
        assert paged.json() == []

    def test_create_rejects_unknown_fields_and_short_topic(self, api_env) -> None:
        client, install_runtime = api_env
        install_runtime()
        token = _register_and_login(client, "bob")

        unknown = _create(client, token, unexpected_field="x")
        assert unknown.status_code == 422

        short = _create(client, token, topic="短")
        assert short.status_code == 422

    def test_list_is_owner_scoped(self, api_env) -> None:
        client, install_runtime = api_env
        install_runtime()
        alice = _register_and_login(client, "carol")
        bob = _register_and_login(client, "dave")

        first = _create(client, alice).json()["task_id"]
        second = _create(client, alice, topic="另一个文献综述主题").json()["task_id"]
        _wait_status(client, alice, first, {TaskStatus.WAITING_APPROVAL.value})
        _wait_status(client, alice, second, {TaskStatus.WAITING_APPROVAL.value})

        alice_items = client.get("/api/v1/tasks", headers=_auth(alice)).json()
        assert {item["task_id"] for item in alice_items} == {first, second}

        bob_items = client.get("/api/v1/tasks", headers=_auth(bob)).json()
        assert bob_items == []


class TestApproval:
    def test_approval_moves_to_running_and_completes(self, api_env) -> None:
        client, install_runtime = api_env
        install_runtime()
        token = _register_and_login(client, "erin")
        task_id = _create(client, token).json()["task_id"]
        _wait_status(client, token, task_id, {TaskStatus.WAITING_APPROVAL.value})

        approved = client.post(
            f"/api/v1/tasks/{task_id}/plan/approve",
            json={"revision": 1},
            headers=_auth(token),
        )
        assert approved.status_code == 200
        assert approved.json()["status"] == TaskStatus.RUNNING.value

        completed = _wait_status(client, token, task_id, {TaskStatus.COMPLETED.value})
        assert completed["review_report"]["summary"]
        assert all(subtask["status"] == "completed" for subtask in completed["subtasks"])

    def test_revision_conflict_returns_422(self, api_env) -> None:
        client, install_runtime = api_env
        install_runtime()
        token = _register_and_login(client, "frank")
        task_id = _create(client, token).json()["task_id"]
        _wait_status(client, token, task_id, {TaskStatus.WAITING_APPROVAL.value})

        conflict = client.post(
            f"/api/v1/tasks/{task_id}/plan/approve",
            json={"revision": 99},
            headers=_auth(token),
        )
        assert conflict.status_code == 422
        assert conflict.json()["error_code"] == "VALIDATION_ERROR"

    def test_cross_user_access_returns_404(self, api_env) -> None:
        client, install_runtime = api_env
        install_runtime()
        alice = _register_and_login(client, "grace")
        bob = _register_and_login(client, "heidi")
        task_id = _create(client, alice).json()["task_id"]
        _wait_status(client, alice, task_id, {TaskStatus.WAITING_APPROVAL.value})

        assert client.get(f"/api/v1/tasks/{task_id}", headers=_auth(bob)).status_code == 404
        assert (
            client.post(
                f"/api/v1/tasks/{task_id}/plan/approve",
                json={"revision": 1},
                headers=_auth(bob),
            ).status_code
            == 404
        )
        assert (
            client.post(
                f"/api/v1/tasks/{task_id}/control",
                json={"command": "cancel"},
                headers=_auth(bob),
            ).status_code
            == 404
        )
        assert client.get(f"/api/v1/tasks/{task_id}/events", headers=_auth(bob)).status_code == 404


class TestControl:
    def test_cancel_from_waiting_approval(self, api_env) -> None:
        client, install_runtime = api_env
        install_runtime()
        token = _register_and_login(client, "ivan")
        task_id = _create(client, token).json()["task_id"]
        _wait_status(client, token, task_id, {TaskStatus.WAITING_APPROVAL.value})

        resp = client.post(
            f"/api/v1/tasks/{task_id}/control",
            json={"command": "cancel"},
            headers=_auth(token),
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == TaskStatus.CANCELLED.value

    def test_pause_running_task(self, api_env) -> None:
        client, install_runtime = api_env
        install_runtime(executor=_SlowExecutor(delay=5.0))
        token = _register_and_login(client, "judy")
        task_id = _create(client, token).json()["task_id"]
        _wait_status(client, token, task_id, {TaskStatus.WAITING_APPROVAL.value})
        client.post(
            f"/api/v1/tasks/{task_id}/plan/approve",
            json={"revision": 1},
            headers=_auth(token),
        )

        paused = client.post(
            f"/api/v1/tasks/{task_id}/control",
            json={"command": "pause"},
            headers=_auth(token),
        )
        assert paused.status_code == 200
        assert paused.json()["status"] == TaskStatus.PAUSED.value

        detail = client.get(f"/api/v1/tasks/{task_id}", headers=_auth(token))
        assert detail.json()["status"] == TaskStatus.PAUSED.value

    def test_retry_node_requires_node_id(self, api_env) -> None:
        client, install_runtime = api_env
        install_runtime()
        token = _register_and_login(client, "ken")
        task_id = _create(client, token).json()["task_id"]

        resp = client.post(
            f"/api/v1/tasks/{task_id}/control",
            json={"command": "retry_node"},
            headers=_auth(token),
        )
        assert resp.status_code == 422

    def test_supply_input_requires_payload(self, api_env) -> None:
        client, install_runtime = api_env
        install_runtime()
        token = _register_and_login(client, "luke")
        task_id = _create(client, token).json()["task_id"]

        resp = client.post(
            f"/api/v1/tasks/{task_id}/control",
            json={"command": "supply_input", "input": {}},
            headers=_auth(token),
        )
        assert resp.status_code == 422

    def test_unknown_command_returns_422(self, api_env) -> None:
        client, install_runtime = api_env
        install_runtime()
        token = _register_and_login(client, "mona")
        task_id = _create(client, token).json()["task_id"]

        resp = client.post(
            f"/api/v1/tasks/{task_id}/control",
            json={"command": "explode"},
            headers=_auth(token),
        )
        assert resp.status_code == 422


class TestEvents:
    def test_events_replay_after_id(self, api_env) -> None:
        client, install_runtime = api_env
        install_runtime()
        token = _register_and_login(client, "nina")
        task_id = _create(client, token).json()["task_id"]
        _wait_status(client, token, task_id, {TaskStatus.WAITING_APPROVAL.value})
        client.post(
            f"/api/v1/tasks/{task_id}/plan/approve",
            json={"revision": 1},
            headers=_auth(token),
        )
        _wait_status(client, token, task_id, {TaskStatus.COMPLETED.value})

        page = client.get(f"/api/v1/tasks/{task_id}/events?after=0", headers=_auth(token))
        assert page.status_code == 200
        body = page.json()
        event_ids = [event["event_id"] for event in body["events"]]
        assert event_ids
        assert event_ids == sorted(event_ids)
        assert body["next_event_id"] == event_ids[-1]

        tail = client.get(
            f"/api/v1/tasks/{task_id}/events?after={body['next_event_id']}",
            headers=_auth(token),
        ).json()
        assert tail["events"] == []
        assert tail["next_event_id"] == body["next_event_id"]
