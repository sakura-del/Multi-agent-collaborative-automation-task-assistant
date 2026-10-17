"""任务事件回放与 WebSocket 流式测试（Plan 2 Task 6）。

覆盖：
- 票据签发要求任务归属；单次消费、任务不匹配与 TTL 过期均被拒绝；
- `events?after=2` 只返回 3 号及之后的事件；
- WebSocket 先发快照，再补拉遗漏事件，随后转发实时事件；
- 无效票据的连接被关闭（4401）。

复用 test_task_api 的 `api_env` 夹具：文件型 SQLite + TestClient portal 事件循环，
每个测试替换 `app.state.runtime` 为注入确定性 Planner/Agent 的 TaskExecution。
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from starlette.websockets import WebSocketDisconnect

from app.orchestration.events import InMemoryTicketService
from tests.test_task_api import (
    _auth,
    _create,
    _register_and_login,
    _wait_status,
)
from tests.test_task_api import api_env as task_api_env_fixture  # noqa: F401


@pytest.fixture(name="api_env")
def _shared_api_env(request):
    """转发 test_task_api 的 api_env 夹具（避免导入名与测试参数名冲突）。"""
    yield request.getfixturevalue("task_api_env_fixture")


class TestTicketService:
    """票据服务单元行为（内存实现）。"""

    async def test_consume_is_single_use_and_task_bound(self) -> None:
        service = InMemoryTicketService()
        user_id, task_id, other_task = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

        ticket = await service.issue(user_id, task_id)
        assert await service.consume(ticket, other_task) is None  # 任务不匹配
        assert await service.consume(ticket, task_id) is None  # 首次消费已弹出

        ticket = await service.issue(user_id, task_id)
        assert await service.consume(ticket, task_id) == user_id
        assert await service.consume(ticket, task_id) is None  # 单次消费

    async def test_expired_ticket_is_rejected(self) -> None:
        service = InMemoryTicketService()
        ticket = await service.issue(uuid.uuid4(), uuid.uuid4(), ttl_seconds=0)
        await asyncio.sleep(0.01)
        assert await service.consume(ticket, uuid.uuid4()) is None


class TestTicketIssuance:
    def test_ticket_requires_task_ownership(self, api_env) -> None:
        client, install_runtime = api_env
        install_runtime()
        alice = _register_and_login(client, "olive")
        bob = _register_and_login(client, "peggy")
        task_id = _create(client, alice).json()["task_id"]
        _wait_status(client, alice, task_id, {"waiting_approval"})

        denied = client.post("/api/v1/ws-ticket", json={"task_id": task_id}, headers=_auth(bob))
        assert denied.status_code == 404

        granted = client.post("/api/v1/ws-ticket", json={"task_id": task_id}, headers=_auth(alice))
        assert granted.status_code == 200
        body = granted.json()
        assert body["ticket"]
        assert body["expires_in"] == 60

    def test_ticket_requires_authentication(self, api_env) -> None:
        client, install_runtime = api_env
        install_runtime()
        resp = client.post("/api/v1/ws-ticket", json={"task_id": str(uuid.uuid4())})
        assert resp.status_code == 401


class TestEventReplay:
    def test_events_after_filters_earlier_events(self, api_env) -> None:
        client, install_runtime = api_env
        install_runtime()
        token = _register_and_login(client, "quinn")
        task_id = _create(client, token).json()["task_id"]
        _wait_status(client, token, task_id, {"waiting_approval"})
        client.post(
            f"/api/v1/tasks/{task_id}/plan/approve",
            json={"revision": 1},
            headers=_auth(token),
        )
        _wait_status(client, token, task_id, {"completed"})

        all_events = client.get(
            f"/api/v1/tasks/{task_id}/events?after=0", headers=_auth(token)
        ).json()
        assert len(all_events["events"]) >= 3

        filtered = client.get(
            f"/api/v1/tasks/{task_id}/events?after=2", headers=_auth(token)
        ).json()
        event_ids = [event["event_id"] for event in filtered["events"]]
        assert event_ids
        assert event_ids == list(range(3, 3 + len(event_ids)))


class TestWebSocketStreaming:
    def test_websocket_sends_snapshot_then_live_events(self, api_env) -> None:
        client, install_runtime = api_env
        install_runtime()
        token = _register_and_login(client, "rita")
        task_id = _create(client, token).json()["task_id"]
        _wait_status(client, token, task_id, {"waiting_approval"})
        ticket = client.post(
            "/api/v1/ws-ticket", json={"task_id": task_id}, headers=_auth(token)
        ).json()["ticket"]

        with client.websocket_connect(f"/api/v1/tasks/{task_id}/ws?ticket={ticket}") as websocket:
            first = websocket.receive_json()
            assert first["type"] == "snapshot"
            assert first["data"]["task_id"] == task_id
            assert first["data"]["status"] == "waiting_approval"

            # 触发实时事件：确认规划 → 后台执行
            approved = client.post(
                f"/api/v1/tasks/{task_id}/plan/approve",
                json={"revision": 1},
                headers=_auth(token),
            )
            assert approved.status_code == 200

            live = websocket.receive_json()
            assert live["type"] == "event"
            assert live["data"]["event_id"] > 0
            assert live["data"]["task_id"] == task_id

    def test_websocket_replays_events_after_id(self, api_env) -> None:
        client, install_runtime = api_env
        install_runtime()
        token = _register_and_login(client, "sam")
        task_id = _create(client, token).json()["task_id"]
        _wait_status(client, token, task_id, {"waiting_approval"})
        client.post(
            f"/api/v1/tasks/{task_id}/plan/approve",
            json={"revision": 1},
            headers=_auth(token),
        )
        _wait_status(client, token, task_id, {"completed"})
        ticket = client.post(
            "/api/v1/ws-ticket", json={"task_id": task_id}, headers=_auth(token)
        ).json()["ticket"]

        with client.websocket_connect(
            f"/api/v1/tasks/{task_id}/ws?ticket={ticket}&after_event_id=2"
        ) as websocket:
            first = websocket.receive_json()
            assert first["type"] == "snapshot"
            replayed = websocket.receive_json()
            assert replayed["type"] == "event"
            assert replayed["data"]["event_id"] == 3

    def test_websocket_rejects_invalid_ticket(self, api_env) -> None:
        client, install_runtime = api_env
        install_runtime()
        token = _register_and_login(client, "tina")
        task_id = _create(client, token).json()["task_id"]

        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(
                f"/api/v1/tasks/{task_id}/ws?ticket=not-a-real-ticket"
            ) as websocket:
                websocket.receive_json()
        assert exc_info.value.code == 4401

    def test_ticket_cannot_be_reused(self, api_env) -> None:
        client, install_runtime = api_env
        install_runtime()
        token = _register_and_login(client, "uma")
        task_id = _create(client, token).json()["task_id"]
        _wait_status(client, token, task_id, {"waiting_approval"})
        ticket = client.post(
            "/api/v1/ws-ticket", json={"task_id": task_id}, headers=_auth(token)
        ).json()["ticket"]

        with client.websocket_connect(f"/api/v1/tasks/{task_id}/ws?ticket={ticket}") as websocket:
            assert websocket.receive_json()["type"] == "snapshot"

        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(
                f"/api/v1/tasks/{task_id}/ws?ticket={ticket}"
            ) as websocket:
                websocket.receive_json()
        assert exc_info.value.code == 4401
