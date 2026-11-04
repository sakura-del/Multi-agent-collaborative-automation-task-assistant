"""Cloudflare Pages 跨域：CORS 必须能按**正则**放行 Pages 域名。

## 为什么不能靠 `allow_origins` 逐个列举

- Pages 每次预览部署的域名都不同：`<hash>.<project>.pages.dev`；
- 本项目 `allow_credentials=True`，而浏览器**禁止**在带凭据时使用 `*`。

所以需要 `allow_origin_regex`，且必须限定到本项目前缀（不能写 `.*`）。

## 另一处易漏：跨域下载拿不到文件名

产物下载靠 `Content-Disposition` 取文件名，但该响应头默认**不对跨域请求暴露**，
必须写进 `expose_headers`，否则前端只能拿到一个 `blob` 而不知道叫什么名字。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import create_app

PROJECT = "multi-agent-assistant"
PAGES_ORIGIN = f"https://{PROJECT}.pages.dev"
PREVIEW_ORIGIN = f"https://abc123.{PROJECT}.pages.dev"
EVIL_ORIGIN = "https://evil.example.com"
PATTERN = rf"^https://([a-z0-9-]+\.)?{PROJECT}\.pages\.dev$"


def _client(monkeypatch, regex: str) -> TestClient:
    """按给定正则重建应用（CORS 中间件在 create_app 时装配，改配置必须重建）。"""
    monkeypatch.setattr(settings.cors, "allow_origin_regex", regex)
    return TestClient(create_app())


def _preflight(client: TestClient, origin: str):
    return client.options(
        "/api/v1/tasks",
        headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
    )


class TestPagesOriginRegex:
    def test_production_domain_allowed(self, monkeypatch) -> None:
        resp = _preflight(_client(monkeypatch, PATTERN), PAGES_ORIGIN)

        assert resp.status_code == 200
        assert resp.headers.get("access-control-allow-origin") == PAGES_ORIGIN

    def test_preview_domain_allowed(self, monkeypatch) -> None:
        """预览部署是 `<hash>.project.pages.dev`，同样要放行。"""
        resp = _preflight(_client(monkeypatch, PATTERN), PREVIEW_ORIGIN)

        assert resp.headers.get("access-control-allow-origin") == PREVIEW_ORIGIN

    def test_other_origin_rejected(self, monkeypatch) -> None:
        resp = _preflight(_client(monkeypatch, PATTERN), EVIL_ORIGIN)

        assert "access-control-allow-origin" not in resp.headers

    def test_regex_empty_means_disabled(self, monkeypatch) -> None:
        """不配置正则时保持原行为：只认 allow_origins 白名单。"""
        resp = _preflight(_client(monkeypatch, ""), PAGES_ORIGIN)

        assert "access-control-allow-origin" not in resp.headers


class TestExposeHeaders:
    def test_content_disposition_is_exposed(self, monkeypatch) -> None:
        """跨域下载要靠它拿文件名。

        注意：`access-control-expose-headers` 只出现在**实际响应**上，
        预检（OPTIONS）响应里没有——所以这里必须发一个真实 GET。
        """
        client = _client(monkeypatch, PATTERN)
        resp = client.get("/api/v1/health", headers={"Origin": PAGES_ORIGIN})

        exposed = resp.headers.get("access-control-expose-headers", "").lower()
        assert "content-disposition" in exposed
