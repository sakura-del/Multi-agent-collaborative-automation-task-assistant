"""第三方 OAuth（Google / GitHub）授权、回调、自动建号与换取 JWT 测试。"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.api.v1.endpoints import auth_unified
from app.core.config import get_settings


class _FakeHttpResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


class _FakeAsyncClient:
    """替代 httpx.AsyncClient，模拟 Google 与 GitHub 的 token/userinfo 接口。"""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def post(self, url: str, **kwargs: object) -> _FakeHttpResponse:
        if "googleapis.com" in url:
            return _FakeHttpResponse({"access_token": "google-acc"})
        return _FakeHttpResponse({"access_token": "github-acc"})

    async def get(self, url: str, **kwargs: object) -> _FakeHttpResponse:
        if "googleapis.com" in url:
            return _FakeHttpResponse(
                {
                    "sub": "google-uid-1001",
                    "email": "OAuth.User@Example.com",
                    "email_verified": True,
                }
            )
        if url.endswith("/user"):
            return _FakeHttpResponse({"id": 990011, "login": "octocat"})
        return _FakeHttpResponse(
            [
                {"email": "secondary@example.com", "primary": False, "verified": True},
                {"email": "oauth.user@example.com", "primary": True, "verified": True},
            ]
        )


def test_oauth_unconfigured_redirects_with_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """未配置密钥时跳转回前端登录页并附带明确错误提示，不返回 500。"""
    settings = get_settings()
    monkeypatch.setattr(settings.oauth.google, "client_id", "")
    monkeypatch.setattr(settings.oauth.google, "client_secret", "")

    resp = client.get("/api/v1/auth/oauth/google/authorize", follow_redirects=False)
    assert resp.status_code == 302
    assert "oauth_error=" in resp.headers["location"]


def test_oauth_google_and_github_link_same_user(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Google 首次登录自动建号，GitHub 同邮箱登录自动绑定同一本地账号。"""
    settings = get_settings()
    monkeypatch.setattr(settings.oauth.google, "client_id", "g-id")
    monkeypatch.setattr(settings.oauth.google, "client_secret", "g-sec")
    monkeypatch.setattr(settings.oauth.github, "client_id", "gh-id")
    monkeypatch.setattr(settings.oauth.github, "client_secret", "gh-sec")
    monkeypatch.setattr(auth_unified.httpx, "AsyncClient", _FakeAsyncClient)

    # 1) 查询启用状态
    p_resp = client.get("/api/v1/auth/oauth/providers")
    assert p_resp.status_code == 200
    assert p_resp.json() == {"google": True, "github": True}

    # 2) Google 授权 → 拿 state → 回调 → 拿 exchange_code → 换 JWT
    auth_resp = client.get("/api/v1/auth/oauth/google/authorize", follow_redirects=False)
    assert auth_resp.status_code == 302
    google_state = parse_qs(urlparse(auth_resp.headers["location"]).query)["state"][0]

    cb_resp = client.get(
        f"/api/v1/auth/oauth/google/callback?code=g-code&state={google_state}",
        follow_redirects=False,
    )
    assert cb_resp.status_code == 302
    exchange_code = parse_qs(urlparse(cb_resp.headers["location"]).query)["code"][0]

    ex_resp = client.post("/api/v1/auth/oauth/exchange", json={"code": exchange_code})
    assert ex_resp.status_code == 200
    google_token = ex_resp.json()["access_token"]

    me_google = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {google_token}"},
    )
    assert me_google.status_code == 200
    google_uid = me_google.json()["id"]

    # 3) 同一邮箱通过 GitHub 登录 → 复用同一本地 user_id
    gh_auth = client.get("/api/v1/auth/oauth/github/authorize", follow_redirects=False)
    gh_state = parse_qs(urlparse(gh_auth.headers["location"]).query)["state"][0]
    gh_cb = client.get(
        f"/api/v1/auth/oauth/github/callback?code=gh-code&state={gh_state}",
        follow_redirects=False,
    )
    gh_code = parse_qs(urlparse(gh_cb.headers["location"]).query)["code"][0]
    gh_ex = client.post("/api/v1/auth/oauth/exchange", json={"code": gh_code})
    assert gh_ex.status_code == 200

    me_github = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {gh_ex.json()['access_token']}"},
    )
    assert me_github.status_code == 200
    assert me_github.json()["id"] == google_uid
