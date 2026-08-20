"""鉴权端点测试：注册 / 登录 / 刷新 / me。

阶段1验收：覆盖鉴权链路与边界情况。
使用内存 SQLite + TestClient，无需真实服务。
"""

from __future__ import annotations


class TestRegister:
    """注册端点。"""

    def test_register_success(self, client) -> None:
        """正常注册返回 201 与用户信息。"""
        resp = client.post(
            "/api/v1/auth/register",
            json={
                "username": "newuser",
                "email": "new@test.com",
                "password": "password123",
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["username"] == "newuser"
        assert body["email"] == "new@test.com"
        assert body["role"] == "user"
        assert "id" in body
        # 不应返回密码哈希
        assert "password_hash" not in body

    def test_register_duplicate_username(self, client) -> None:
        """重复用户名返回 422。"""
        payload = {
            "username": "dup",
            "email": "dup1@test.com",
            "password": "password123",
        }
        client.post("/api/v1/auth/register", json=payload)
        resp = client.post(
            "/api/v1/auth/register",
            json={
                "username": "dup",
                "email": "dup2@test.com",
                "password": "password123",
            },
        )
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "VALIDATION_ERROR"

    def test_register_short_password_rejected(self, client) -> None:
        """短密码被 Pydantic 校验拒绝。"""
        resp = client.post(
            "/api/v1/auth/register",
            json={"username": "short", "email": "s@t.com", "password": "123"},
        )
        assert resp.status_code == 422

    def test_register_invalid_email(self, client) -> None:
        """非法邮箱格式被拒绝。"""
        resp = client.post(
            "/api/v1/auth/register",
            json={
                "username": "bademail",
                "email": "not-an-email",
                "password": "password123",
            },
        )
        assert resp.status_code == 422


class TestLogin:
    """登录端点。"""

    def _register(self, client, username="loginuser", password="password123") -> None:
        client.post(
            "/api/v1/auth/register",
            json={
                "username": username,
                "email": f"{username}@test.com",
                "password": password,
            },
        )

    def test_login_success(self, client) -> None:
        """正确凭据登录返回令牌。"""
        self._register(client)
        resp = client.post(
            "/api/v1/auth/login",
            json={"account": "loginuser", "password": "password123"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert "refresh_token" in body
        assert body["token_type"] == "bearer"

    def test_login_by_email(self, client) -> None:
        """邮箱登录。"""
        self._register(client)
        resp = client.post(
            "/api/v1/auth/login",
            json={"account": "loginuser@test.com", "password": "password123"},
        )
        assert resp.status_code == 200

    def test_login_wrong_password(self, client) -> None:
        """错误密码返回 401。"""
        self._register(client)
        resp = client.post(
            "/api/v1/auth/login",
            json={"account": "loginuser", "password": "wrongpassword"},
        )
        assert resp.status_code == 401

    def test_login_nonexistent_user(self, client) -> None:
        """不存在的用户返回 401（不泄漏存在性）。"""
        resp = client.post(
            "/api/v1/auth/login",
            json={"account": "ghost", "password": "password123"},
        )
        assert resp.status_code == 401

    def test_login_expires_in_uses_settings(self, client, monkeypatch) -> None:
        """expires_in 应从 settings 派生，而非硬编码。"""
        from app.api.v1.endpoints import auth

        monkeypatch.setattr(auth.settings, "jwt_access_token_expire_minutes", 15)
        self._register(client, username="expiryuser")
        response = client.post(
            "/api/v1/auth/login",
            json={"account": "expiryuser", "password": "password123"},
        )
        assert response.status_code == 200
        assert response.json()["expires_in"] == 900


class TestMe:
    """当前用户信息端点。"""

    def _login(self, client) -> str:
        client.post(
            "/api/v1/auth/register",
            json={
                "username": "meuser",
                "email": "me@test.com",
                "password": "password123",
            },
        )
        resp = client.post(
            "/api/v1/auth/login",
            json={"account": "meuser", "password": "password123"},
        )
        return resp.json()["access_token"]

    def test_me_with_valid_token(self, client) -> None:
        """带有效 token 获取用户信息。"""
        token = self._login(client)
        resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["username"] == "meuser"

    def test_me_without_token(self, client) -> None:
        """无 token 返回 401。"""
        resp = client.get("/api/v1/auth/me")
        assert resp.status_code == 401

    def test_me_with_invalid_token(self, client) -> None:
        """无效 token 返回 401。"""
        resp = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer invalidtoken"})
        assert resp.status_code == 401


class TestRefresh:
    """令牌刷新端点。"""

    def _login(self, client) -> dict:
        client.post(
            "/api/v1/auth/register",
            json={
                "username": "refreshuser",
                "email": "refresh@test.com",
                "password": "password123",
            },
        )
        resp = client.post(
            "/api/v1/auth/login",
            json={"account": "refreshuser", "password": "password123"},
        )
        return resp.json()

    def test_refresh_success(self, client) -> None:
        """用 refresh_token 换发新令牌。"""
        tokens = self._login(client)
        resp = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": tokens["refresh_token"]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert "refresh_token" in body

    def test_refresh_with_access_token_rejected(self, client) -> None:
        """用 access_token 冒充 refresh_token 应被拒绝。"""
        tokens = self._login(client)
        resp = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": tokens["access_token"]},
        )
        assert resp.status_code == 401

    def test_refresh_with_invalid_token(self, client) -> None:
        """非法 refresh_token 返回 401。"""
        resp = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "invalid.token.here"},
        )
        assert resp.status_code == 401
