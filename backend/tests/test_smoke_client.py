"""Compose 冒烟客户端的纯逻辑测试（Plan 5 Task 3）。

不启动容器：用 httpx 的 MockTransport 覆盖成功链路与各失败分支，
保证冒烟脚本本身的判定逻辑（就绪等待、产物校验、事件递增）是可测的。
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.tools.smoke import smoke


def _ready_payload() -> dict[str, Any]:
    return {"status": "ready", "checks": {"database": "ok", "redis": "ok", "chroma": "ok"}}


def _docx_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", "<w:document/>")
    return buffer.getvalue()


def _make_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)


def _route(request: httpx.Request) -> httpx.Response:
    """成功链路的最小路由（任务状态推进用闭包外状态控制）。"""
    path = request.url.path
    if path.endswith("/health/ready"):
        return httpx.Response(200, json=_ready_payload())
    if path.endswith("/auth/register"):
        return httpx.Response(201, json={"id": "u1", "username": "smoke"})
    if path.endswith("/auth/login"):
        return httpx.Response(
            200, json={"access_token": "tok", "refresh_token": "r", "expires_in": 900}
        )
    if path.endswith("/knowledge/documents"):
        return httpx.Response(
            201,
            json={
                "id": "d1",
                "title": "冒烟样本文档",
                "doc_type": "txt",
                "index_status": "ready",
                "chunk_count": 1,
                "created_at": "2026-10-18T00:00:00Z",
            },
        )
    if path.endswith("/plan/approve"):
        return httpx.Response(200, json={"task_id": "t1", "status": "running", "revision": 1})
    if path.endswith("/events"):
        return httpx.Response(
            200,
            json={
                "events": [
                    {"event_id": 1, "task_id": "t1", "status": "created"},
                    {"event_id": 2, "task_id": "t1", "status": "running"},
                ],
                "next_event_id": 2,
            },
        )
    if "/artifacts/" in path:
        if "a2" in path:
            return httpx.Response(200, content=_docx_bytes())
        return httpx.Response(200, content="# 综述\n\n结论 [E1]\n".encode())
    if path.endswith("/tasks") and request.method == "POST":
        return httpx.Response(201, json={"task_id": "t1", "status": "created"})
    if path.endswith("/tasks/t1"):
        return httpx.Response(200, json={"task_id": "t1", "status": "completed", "revision": 1})
    return httpx.Response(404, json={"message": f"unmocked {path}"})


class TestSmokeHelpers:
    def test_wait_ready_returns_payload(self) -> None:
        with _make_client(_route) as client:
            payload = smoke.wait_ready(client)
        assert payload["status"] == "ready"

    def test_register_and_login_returns_bearer_header(self) -> None:
        with _make_client(_route) as client:
            username, headers = smoke.register_and_login(client)
        assert username.startswith("smoke")
        assert headers["Authorization"] == "Bearer tok"

    def test_upload_document_rejects_not_ready_index(self) -> None:
        def not_ready(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                201,
                json={
                    "id": "d1",
                    "title": "x",
                    "doc_type": "txt",
                    "index_status": "failed",
                    "chunk_count": 0,
                    "created_at": "2026-10-18T00:00:00Z",
                },
            )

        with _make_client(not_ready) as client:
            with pytest.raises(smoke.SmokeError):
                smoke.upload_document(client, {"Authorization": "Bearer tok"})

    def test_verify_artifacts_rejects_missing_citation_marker(self) -> None:
        final = {
            "task_id": "t1",
            "status": "completed",
            "review_report": {"final_markdown": "没有引用标记的正文"},
            "artifacts": [],
        }
        with _make_client(_route) as client:
            with pytest.raises(smoke.SmokeError):
                smoke.verify_artifacts(client, {"Authorization": "Bearer tok"}, final)

    def test_verify_artifacts_rejects_non_monotonic_events(self) -> None:
        def bad_events(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/events"):
                return httpx.Response(
                    200,
                    json={
                        "events": [
                            {"event_id": 3, "task_id": "t1", "status": "a"},
                            {"event_id": 2, "task_id": "t1", "status": "b"},
                        ],
                        "next_event_id": 3,
                    },
                )
            return _route(request)

        final = {
            "task_id": "t1",
            "status": "completed",
            "review_report": {"final_markdown": "# x\n[E1]"},
            "artifacts": [
                {"artifact_id": "a1", "kind": "markdown"},
                {"artifact_id": "a2", "kind": "docx"},
            ],
        }
        with _make_client(bad_events) as client:
            with pytest.raises(smoke.SmokeError):
                smoke.verify_artifacts(client, {"Authorization": "Bearer tok"}, final)

    def test_verify_artifacts_accepts_valid_payload(self) -> None:
        final = {
            "task_id": "t1",
            "status": "completed",
            "review_report": {"final_markdown": "# 综述\n\n结论 [E1]\n"},
            "artifacts": [
                {"artifact_id": "a1", "kind": "markdown"},
                {"artifact_id": "a2", "kind": "docx"},
            ],
        }
        with _make_client(_route) as client:
            smoke.verify_artifacts(client, {"Authorization": "Bearer tok"}, final)


class TestSmokeMain:
    def test_main_returns_zero_on_happy_path(self, monkeypatch) -> None:
        state: dict[str, bool] = {"approved": False}

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.endswith("/tasks/t1"):
                if not state["approved"]:
                    state["approved"] = True
                    return httpx.Response(
                        200,
                        json={"task_id": "t1", "status": "waiting_approval", "revision": 1},
                    )
                return httpx.Response(
                    200,
                    json={
                        "task_id": "t1",
                        "status": "completed",
                        "revision": 1,
                        "review_report": {"final_markdown": "# 综述\n\n结论 [E1]\n"},
                        "artifacts": [
                            {"artifact_id": "a1", "kind": "markdown"},
                            {"artifact_id": "a2", "kind": "docx"},
                        ],
                    },
                )
            if path.endswith("/artifacts/a2"):
                return httpx.Response(200, content=_docx_bytes())
            if path.endswith("/artifacts/a1"):
                return httpx.Response(200, content="# 综述\n\n结论 [E1]\n".encode())
            return _route(request)

        original_client = httpx.Client

        def client_factory(*args: Any, **kwargs: Any) -> httpx.Client:
            kwargs.pop("transport", None)
            return original_client(transport=httpx.MockTransport(handler), timeout=5.0)

        monkeypatch.setattr(smoke.httpx, "Client", client_factory)
        assert smoke.main() == 0

    def test_main_returns_nonzero_when_readiness_never_arrives(self, monkeypatch) -> None:
        def failing(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={"message": "not ready"})

        original_client = httpx.Client

        def client_factory(*args: Any, **kwargs: Any) -> httpx.Client:
            kwargs.pop("transport", None)
            return original_client(transport=httpx.MockTransport(failing), timeout=5.0)

        monkeypatch.setattr(smoke.httpx, "Client", client_factory)
        monkeypatch.setattr(smoke, "READY_TIMEOUT", 0.2)
        monkeypatch.setattr(smoke.time, "sleep", lambda _seconds: None)
        assert smoke.main() == 1
