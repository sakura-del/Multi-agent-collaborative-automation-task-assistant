"""限流的客户端身份必须来自网关，不是客户端自报的头（Issue #11 M-2）。

## 现状（2026-11-11 安全排查实测）

生产在 nginx 之后，`request.client.host` 恒为 nginx 容器内网 IP
（实测 Redis key 为 `ratelimit:3600:auth_register:ip:172.18.0.5`）。
后果：**注册 5 次/小时是全局限额**——任意客户端刷满后，所有真实用户 1 小时内无法注册。
登录同理。

## 为什么不能简单地打开 `TRUST_PROXY`

原实现信任 `X-Forwarded-For` 的**第一段**。而 nginx 的 `$proxy_add_x_forwarded_for`
是「客户端自带的 XFF + 真实地址」拼接，第一段正是客户端自己写的。只开开关不锁源站，
等于任何人都能 `X-Forwarded-For: 1.1.1.1` 换一个桶，限流形同虚设。

## 正确口径（本文件锁住）

1. 网关用 `X-Real-IP` 传真实客户端地址——这个头由 nginx **无条件覆盖**写，客户端伪造无效；
2. 应用只读 `X-Real-IP`，且必须能解析成合法 IP，否则退回 socket 地址；
3. **永远不看 `X-Forwarded-For`**（它天然含客户端可写部分）。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.api.rate_limit_middleware import RateLimitMiddleware, _client_identity

SOCKET_CLIENT = ("203.0.113.9", 44321)


def _request(
    headers: dict[str, str] | None = None,
    *,
    client: tuple[str, int] | None = SOCKET_CLIENT,
    user_id: str | None = None,
) -> Request:
    """构造一个最小可用的 Request（只填 `_client_identity` 需要的字段）。"""
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope: dict[str, Any] = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": "/api/v1/auth/login",
        "raw_path": b"/api/v1/auth/login",
        "query_string": b"",
        "headers": raw_headers,
        "client": client,
        "server": ("testserver", 443),
    }
    request = Request(scope)
    if user_id is not None:
        request.state.user_id = user_id
    return request


class TestTrustProxyDisabled:
    def test_uses_socket_peer(self) -> None:
        request = _request(
            {"x-real-ip": "198.51.100.7"},
        )
        assert _client_identity(request, trust_proxy=False) == "ip:203.0.113.9"

    def test_ignores_forwarded_headers(self) -> None:
        request = _request({"x-forwarded-for": "1.1.1.1", "x-real-ip": "198.51.100.7"})
        assert _client_identity(request, trust_proxy=False) == "ip:203.0.113.9"


class TestTrustProxyEnabled:
    def test_uses_gateway_supplied_real_ip(self) -> None:
        request = _request({"x-real-ip": "198.51.100.7"})
        assert _client_identity(request, trust_proxy=True) == "ip:198.51.100.7"

    def test_ignores_x_forwarded_for_entirely(self) -> None:
        """**关键反绕过断言**：只有 XFF、没有 X-Real-IP 时必须退回 socket 地址。

        若这里取 XFF 第一段，任何人加个 `X-Forwarded-For: 1.1.1.1` 就换了一个限流桶。
        """
        request = _request({"x-forwarded-for": "1.1.1.1"})
        assert _client_identity(request, trust_proxy=True) == "ip:203.0.113.9"

    def test_ignores_forwarded_for_even_when_real_ip_present(self) -> None:
        request = _request({"x-real-ip": "198.51.100.7", "x-forwarded-for": "1.1.1.1"})
        assert _client_identity(request, trust_proxy=True) == "ip:198.51.100.7"

    @pytest.mark.parametrize("bad_value", ["not-an-ip", "1.1.1.1, 2.2.2.2", "", "  ", "999.1.1.1"])
    def test_invalid_real_ip_falls_back_to_socket(self, bad_value: str) -> None:
        """网关不会写出这些值；出现了就说明有人在直连源站伪造 → 不能采信。"""
        request = _request({"x-real-ip": bad_value})
        assert _client_identity(request, trust_proxy=True) == "ip:203.0.113.9"

    def test_accepts_ipv6(self) -> None:
        request = _request({"x-real-ip": "2606:4700:4700::1111"})
        identity = _client_identity(request, trust_proxy=True)
        assert identity.startswith("ip:2606:4700:4700::1111")

    def test_normalizes_equivalent_forms(self) -> None:
        """同一个地址的不同写法必须落进同一个桶（否则换个写法就绕过限额）。"""
        expanded = _client_identity(_request({"x-real-ip": "2001:0db8::1"}), trust_proxy=True)
        compressed = _client_identity(_request({"x-real-ip": "2001:db8::1"}), trust_proxy=True)
        assert expanded == compressed


class TestAuthenticatedUserWins:
    def test_user_id_identity_beats_ip(self) -> None:
        request = _request({"x-real-ip": "198.51.100.7"}, user_id="u-1")
        assert _client_identity(request, trust_proxy=True) == "user:u-1"


class _RecordingLimiter:
    """记录每次 hit 的 key，用于观察「不同请求是否落进同一个桶」。"""

    def __init__(self) -> None:
        self.keys: list[str] = []

    async def hit(self, key: str, *, limit: int, window_seconds: int) -> tuple[bool, int]:
        self.keys.append(key)
        return True, max(0, limit - len(self.keys))


def _app_with_middleware(monkeypatch, limiter: _RecordingLimiter) -> TestClient:
    """最小应用：只挂一个与登录同路径的桩路由 + 限流中间件。"""
    from app.core.config import settings as config_settings

    monkeypatch.setattr(config_settings.rate_limit, "trust_proxy", True)
    monkeypatch.setattr(config_settings.rate_limit, "enabled", True)

    app = FastAPI()

    @app.post("/api/v1/auth/login")
    async def fake_login() -> dict[str, bool]:
        return {"ok": True}

    app.add_middleware(RateLimitMiddleware, limiter=limiter)
    return TestClient(app)


class TestMiddlewareBuckets:
    def test_different_real_ips_get_different_buckets(self, monkeypatch) -> None:
        limiter = _RecordingLimiter()
        client = _app_with_middleware(monkeypatch, limiter)

        client.post("/api/v1/auth/login", headers={"X-Real-IP": "198.51.100.7"})
        client.post("/api/v1/auth/login", headers={"X-Real-IP": "198.51.100.8"})

        assert len(set(limiter.keys)) == 2, limiter.keys

    def test_forged_forwarded_for_cannot_switch_bucket(self, monkeypatch) -> None:
        """同一来源伪造不同 XFF，必须仍落进同一个桶。"""
        limiter = _RecordingLimiter()
        client = _app_with_middleware(monkeypatch, limiter)

        client.post(
            "/api/v1/auth/login",
            headers={"X-Real-IP": "198.51.100.7", "X-Forwarded-For": "1.1.1.1"},
        )
        client.post(
            "/api/v1/auth/login",
            headers={"X-Real-IP": "198.51.100.7", "X-Forwarded-For": "2.2.2.2"},
        )

        assert len(set(limiter.keys)) == 1, limiter.keys


@pytest.fixture(scope="module")
def nginx_conf() -> str:
    """读取网关配置：没有 `set_real_ip_from`，应用再正确也只能拿到 CF 节点地址。"""
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "deploy" / "ecs" / "nginx-api-tls.conf"
    assert path.is_file(), f"缺少网关配置：{path}"
    return path.read_text(encoding="utf-8")


class TestGatewayConfig:
    """网关侧的半边。"""

    def test_uses_cloudflare_connecting_ip_header(self, nginx_conf: str) -> None:
        assert "real_ip_header CF-Connecting-IP;" in nginx_conf

    def test_enables_recursive_real_ip(self, nginx_conf: str) -> None:
        assert "real_ip_recursive on;" in nginx_conf

    @pytest.mark.parametrize(
        "range_",
        [
            "173.245.48.0/20",
            "104.16.0.0/13",
            "172.64.0.0/13",
            "131.0.72.0/22",
            "2606:4700::/32",
            "2c0f:f248::/32",
        ],
    )
    def test_trusts_cloudflare_ranges(self, nginx_conf: str, range_: str) -> None:
        assert f"set_real_ip_from {range_};" in nginx_conf, f"缺少 Cloudflare 网段 {range_}"

    def test_every_proxy_block_passes_real_ip_downstream(self, nginx_conf: str) -> None:
        """`X-Real-IP` 必须用 `$remote_addr` 覆盖写：客户端自带的同名头必须被冲掉。"""
        assert nginx_conf.count("proxy_set_header X-Real-IP $remote_addr;") >= 2
        assert "proxy_set_header X-Real-IP $http_x_real_ip;" not in nginx_conf
