"""出站端点的 SSRF 防护与 DNS pinning 测试（Issue #15 P1）。

## 锁定的不变量

1. 私网/环回/链路本地**字面量**直接拒绝；
2. 域名解析到内网也拒绝（创建期拦不住的那一半）；
3. 真正发请求时，连接的是**已校验的 IP**，不是再次解析的域名；
4. 保留原始 Host 头（否则虚拟主机/网关 404）；
5. HTTPS 下证书仍按**域名**校验（SNI 未被 IP 覆盖）。
"""

from __future__ import annotations

import datetime as dt
import json
import ssl
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.core.exceptions import LLMException, ValidationException
from app.llm.endpoint_guard import PinnedDNSAsyncHTTPTransport, resolve_public_ips


class TestResolve:
    async def test_public_literal_allowed(self) -> None:
        assert await resolve_public_ips("8.8.8.8") == ["8.8.8.8"]

    @pytest.mark.parametrize(
        "host",
        ["127.0.0.1", "10.0.0.8", "169.254.169.254", "::1", "fc00::1"],
    )
    async def test_private_literal_rejected(self, host: str) -> None:
        with pytest.raises(ValidationException):
            await resolve_public_ips(host)

    async def test_localhost_dns_rejected(self) -> None:
        """域名解析到环回 —— 创建期只看到 `localhost` 六个字母。"""
        with pytest.raises(ValidationException) as exc:
            await resolve_public_ips("localhost")
        assert "内网" in exc.value.message

    async def test_allow_private_escape_hatch(self) -> None:
        assert await resolve_public_ips("127.0.0.1", allow_private=True) == ["127.0.0.1"]

    async def test_unknown_host_is_readable_error(self) -> None:
        with pytest.raises(LLMException) as exc:
            await resolve_public_ips("does-not-exist.invalid")
        assert "无法解析" in exc.value.message


@dataclass
class _Server:
    base_url: str
    hosts: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)


@pytest.fixture
def pinned_server(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Server]:
    """本地 HTTP 服务；把 `relay.test` 固定解析到 127.0.0.1。"""
    state = _Server(base_url="")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            state.hosts.append(self.headers.get("Host", ""))
            state.paths.append(self.path)
            payload = json.dumps({"data": [{"id": "model-a"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.base_url = f"127.0.0.1:{server.server_port}"

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


class TestPinnedTransport:
    async def test_connects_to_validated_ip_with_original_host(
        self, pinned_server: _Server
    ) -> None:
        transport = PinnedDNSAsyncHTTPTransport()
        async with httpx.AsyncClient(transport=transport, timeout=5) as client:
            response = await client.get(
                f"http://relay.test:{pinned_server.base_url.split(':')[1]}/v1/models"
            )

        assert response.status_code == 200
        assert pinned_server.hosts == [f"relay.test:{pinned_server.base_url.split(':')[1]}"]
        assert pinned_server.paths == ["/v1/models"]

    async def test_private_resolution_blocks_before_connect(self) -> None:
        """resolve 返回内网时抛错，请求不会发出去。"""
        # 直接用 IP 字面量：无需 DNS，必被拒绝
        transport = PinnedDNSAsyncHTTPTransport()
        async with httpx.AsyncClient(transport=transport, timeout=3) as client:
            with pytest.raises(ValidationException):
                await client.get("http://127.0.0.1:9/v1/models")


def _make_cert(tmp_path: Path, hostname: str) -> tuple[Path, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


class TestTlsSni:
    async def test_certificate_is_verified_against_domain(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """连接走 127.0.0.1，但证书必须按 relay.test 校验（SNI 生效）。"""
        cert_path, key_path = _make_cert(tmp_path, "relay.test")
        state = _Server(base_url="")

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:
                state.hosts.append(self.headers.get("Host", ""))
                payload = b'{"data": []}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args: Any) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
        server.socket = context.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        state.base_url = f"127.0.0.1:{server.server_port}"

        async def _resolve(host: str, *, allow_private: bool = False) -> list[str]:
            assert host == "relay.test"
            return ["127.0.0.1"]

        monkeypatch.setattr("app.llm.endpoint_guard.resolve_public_ips", _resolve)

        verify = ssl.create_default_context(cafile=str(cert_path))
        transport = PinnedDNSAsyncHTTPTransport(verify=verify)
        try:
            async with httpx.AsyncClient(transport=transport, timeout=5) as client:
                response = await client.get(f"https://relay.test:{server.server_port}/v1/models")
            assert response.status_code == 200
            assert state.hosts == [f"relay.test:{server.server_port}"]
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
