"""模型探活与列表拉取测试（Issue #15 P1）。

探活是**唯一**用用户 key 主动外呼的入口：必须走 pin 传输层、结果必须可读、
错误不能把 key 带进文案。
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from app.llm.probe import fetch_models


@dataclass
class _State:
    status: int = 200
    body: bytes = b'{"data": [{"id": "model-a"}, {"id": "model-b"}]}'
    content_type: str = "application/json"
    hosts: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)


@pytest.fixture
def models_server(monkeypatch: pytest.MonkeyPatch) -> Iterator[_State]:
    state = _State()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            state.hosts.append(self.headers.get("Host", ""))
            state.paths.append(self.path)
            self.send_response(state.status)
            self.send_header("Content-Type", state.content_type)
            self.send_header("Content-Length", str(len(state.body)))
            self.end_headers()
            self.wfile.write(state.body)

        def log_message(self, *args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.port = server.server_port  # type: ignore[attr-defined]

    async def _resolve(host: str, *, allow_private: bool = False) -> list[str]:
        assert host == "relay.test"
        return ["127.0.0.1"]

    monkeypatch.setattr("app.llm.endpoint_guard.resolve_public_ips", _resolve)
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def _base_url(state: _State) -> str:
    return f"http://relay.test:{state.port}/v1"  # type: ignore[attr-defined]


class TestFetchModels:
    async def test_success_returns_models(self, models_server: _State) -> None:
        result = await fetch_models(
            base_url=_base_url(models_server), api_key="sk-user-123456", allow_private=True
        )

        assert result.ok is True
        assert result.models == ["model-a", "model-b"]
        assert result.error is None
        assert result.latency_ms >= 0
        assert models_server.paths == ["/v1/models"]
        assert models_server.hosts == [f"relay.test:{models_server.port}"]  # type: ignore[attr-defined]

    async def test_401_is_readable_and_has_no_key(self, models_server: _State) -> None:
        models_server.status = 401
        models_server.body = b'{"error": "invalid key"}'

        result = await fetch_models(
            base_url=_base_url(models_server), api_key="sk-secret-should-not-appear"
        )

        assert result.ok is False
        assert "被拒绝" in (result.error or "")
        assert "sk-secret-should-not-appear" not in (result.error or "")

    async def test_404_explains_models_endpoint(self, models_server: _State) -> None:
        models_server.status = 404
        models_server.body = b""

        result = await fetch_models(base_url=_base_url(models_server), api_key="sk-user-123456")

        assert result.ok is False
        assert "/models" in (result.error or "")

    async def test_non_json_response(self, models_server: _State) -> None:
        models_server.body = b"<html>not json</html>"
        models_server.content_type = "text/html"

        result = await fetch_models(base_url=_base_url(models_server), api_key="sk-user-123456")

        assert result.ok is False
        assert "JSON" in (result.error or "")

    async def test_empty_list_is_still_connected(self, models_server: _State) -> None:
        models_server.body = b'{"data": []}'

        result = await fetch_models(base_url=_base_url(models_server), api_key="sk-user-123456")

        assert result.ok is True
        assert result.models == []
        assert "未返回模型列表" in (result.error or "")

    async def test_bare_array_payload(self, models_server: _State) -> None:
        models_server.body = b'[{"id": "m1"}, {"name": "m2"}, "m3"]'

        result = await fetch_models(base_url=_base_url(models_server), api_key="sk-user-123456")

        assert result.models == ["m1", "m2", "m3"]

    async def test_private_resolution_rejected(self) -> None:
        """不走 pin 传输层的裸调用会读到内网；这里必须被拦住。"""
        result = await fetch_models(base_url="http://127.0.0.1:9/v1", api_key="sk-user-123456")
        assert result.ok is False
        assert "内网" in (result.error or "")
