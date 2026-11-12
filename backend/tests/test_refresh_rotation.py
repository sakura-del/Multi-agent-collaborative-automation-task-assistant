"""refresh token 轮换必须让旧令牌立即失效（Issue #11 M-1）。

## 现状（2026-11-11 安全排查实测）

用 refresh_token 换发新令牌后，**同一个 refresh_token 还能再换一次并返回 200**。
影响：refresh 一旦被窃取，即使合法用户刷新过，攻击者仍能在 7 天内持续换取 access；
用户也无从踢掉（`token_invalid_before` 没变）。

## 修复口径

1. 签发 refresh 时带唯一 `jti`；
2. 每次轮换把旧 `jti` 写入吊销表，过期时间取令牌自身的 `exp`（自然过期即自动清理）；
3. 旧 `jti` 再来 → **401 且整个家族作废**（`token_invalid_before = now`）。

第 3 条为什么必须连坐：旧令牌被重放时，只有两种可能——合法客户端重试，或者攻击者先用了。
分辨不出来的时候，让双方手里的令牌一起失效是唯一安全的选择（RFC 9700 §4.14.2）。

## 兼容性

本修复前签发的 refresh 令牌没有 `jti`，无法登记吊销，一律拒绝并要求重新登录。
这是有意的：留着它们等于留一条绕过路径。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt

from app.core.config import get_settings
from app.db.repositories.revoked_token import RevokedTokenRepository


def _register_and_login(client, username: str = "rotuser") -> dict:
    """注册并登录，返回登录响应体（含 access/refresh/expires_in）。"""
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
    assert resp.status_code == 200, resp.text
    return resp.json()


def _refresh(client, refresh_token: str):
    return client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})


def _me(client, access_token: str):
    return client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {access_token}"})


def _decode(token: str) -> dict:
    return jwt.decode(
        token,
        get_settings().jwt_secret_key,
        algorithms=[get_settings().jwt_algorithm],
    )


class TestRotationInvalidatesOldToken:
    def test_old_refresh_token_rejected_after_rotation(self, client) -> None:
        """轮换一次后，旧 refresh 再用必须 401（核心缺陷）。"""
        tokens = _register_and_login(client)

        rotated = _refresh(client, tokens["refresh_token"])
        assert rotated.status_code == 200, rotated.text

        replay = _refresh(client, tokens["refresh_token"])
        assert replay.status_code == 401, f"旧 refresh 仍可用：{replay.text}"

    def test_new_refresh_token_still_works(self, client) -> None:
        """轮换得到的新 refresh 必须还能正常换发（别把正常链路一起堵死）。"""
        tokens = _register_and_login(client)
        rotated = _refresh(client, tokens["refresh_token"]).json()

        again = _refresh(client, rotated["refresh_token"])
        assert again.status_code == 200, again.text

    def test_replay_after_grace_revokes_whole_family(self, client, monkeypatch) -> None:
        """超过宽限期仍被重放的旧令牌 → 判定泄露，该用户全部令牌失效。

        ⚠️ 这里必须改 **模块级 `settings`**（`app.core.config.settings`），不是
        `get_settings()`：别的测试（`test_config_priority` / `test_embedding`）会
        `cache_clear()`，之后 `get_settings()` 返回新对象，而端点读的是导入时那个旧对象。
        改错了对象 → 端点仍用默认 60 秒宽限期，测试单独跑绿、全量跑红。
        """
        from app.core.config import settings as config_settings

        monkeypatch.setattr(config_settings, "jwt_refresh_reuse_grace_seconds", 0)

        tokens = _register_and_login(client)
        rotated = _refresh(client, tokens["refresh_token"]).json()

        # 重放前：轮换得到的 access 可用
        assert _me(client, rotated["access_token"]).status_code == 200

        assert _refresh(client, tokens["refresh_token"]).status_code == 401

        # 家族作废：轮换得到的 access 与 refresh 都不能再用
        assert _me(client, rotated["access_token"]).status_code == 401, "重放后旧家族 access 仍有效"
        assert (
            _refresh(client, rotated["refresh_token"]).status_code == 401
        ), "重放后新 refresh 仍有效"

    def test_replay_within_grace_rejects_only_that_token(self, client) -> None:
        """宽限期内重放：只拒这次，不作废家族。

        真实场景：两个标签页同时遇到 access 过期，都会用同一个 refresh 去换。
        第二个必须 401（旧令牌不能复活），但第一个拿到的会话不能跟着死。
        """
        tokens = _register_and_login(client)
        rotated = _refresh(client, tokens["refresh_token"]).json()

        replay = _refresh(client, tokens["refresh_token"])
        assert replay.status_code == 401

        # 没有连坐：轮换出来的令牌仍然可用
        assert _me(client, rotated["access_token"]).status_code == 200
        assert _refresh(client, rotated["refresh_token"]).status_code == 200

    def test_second_use_in_same_session_rejected(self, client) -> None:
        """同一 refresh 连用两次：只能有一次成功（第二次必 401）。"""
        tokens = _register_and_login(client)
        first = _refresh(client, tokens["refresh_token"])
        second = _refresh(client, tokens["refresh_token"])

        assert first.status_code == 200
        assert second.status_code == 401


class TestTokenShape:
    def test_refresh_token_carries_unique_jti(self, client) -> None:
        """每个 refresh 令牌必须有唯一 jti——没有它就无法精确吊销。"""
        tokens = _register_and_login(client)
        rotated = _refresh(client, tokens["refresh_token"]).json()

        first_jti = _decode(tokens["refresh_token"]).get("jti")
        second_jti = _decode(rotated["refresh_token"]).get("jti")

        assert first_jti, "refresh 令牌缺少 jti"
        assert second_jti, "轮换后的 refresh 令牌缺少 jti"
        assert first_jti != second_jti

    def test_access_token_has_no_jti(self, client) -> None:
        """access 不参与轮换吊销，不该带 jti（带了会让人误以为可吊销）。"""
        tokens = _register_and_login(client)
        assert _decode(tokens["access_token"]).get("jti") is None

    def test_legacy_refresh_token_without_jti_rejected(self, client) -> None:
        """修复前签发的令牌没有 jti，无法吊销 → 一律拒绝。"""
        tokens = _register_and_login(client)
        user_id = _me(client, tokens["access_token"]).json()["id"]

        settings = get_settings()
        now = datetime.now(UTC)
        legacy = jwt.encode(
            {
                "sub": user_id,
                "type": "refresh",
                "iat": now,
                "exp": now + timedelta(days=7),
            },
            settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm,
        )

        assert _refresh(client, legacy).status_code == 401


class TestRefreshResponse:
    def test_expires_in_matches_configured_access_ttl(self, client) -> None:
        """refresh 的 expires_in 必须与 access 实际有效期一致（曾硬编码 3600）。"""
        tokens = _register_and_login(client)
        rotated = _refresh(client, tokens["refresh_token"]).json()

        expected = get_settings().jwt_access_token_expire_minutes * 60
        assert rotated["expires_in"] == expected


class TestRevokedTokenRepository:
    async def test_add_and_exists(self, db_session) -> None:
        repo = RevokedTokenRepository(db_session)
        jti = uuid.uuid4().hex
        user_id = uuid.uuid4()

        await repo.add(jti, user_id=user_id, expires_at=datetime.now(UTC) + timedelta(days=7))
        await db_session.commit()

        assert await repo.exists(jti) is True
        assert await repo.exists(uuid.uuid4().hex) is False

    async def test_add_is_idempotent(self, db_session) -> None:
        """同一 jti 重复登记不能炸（重放请求会走到同一条路径）。"""
        repo = RevokedTokenRepository(db_session)
        jti = uuid.uuid4().hex
        user_id = uuid.uuid4()
        expires_at = datetime.now(UTC) + timedelta(days=7)

        await repo.add(jti, user_id=user_id, expires_at=expires_at)
        await repo.add(jti, user_id=user_id, expires_at=expires_at)
        await db_session.commit()

        assert await repo.exists(jti) is True

    async def test_purge_expired_keeps_live_entries(self, db_session) -> None:
        """过期条目清掉，未过期的必须留着——清早了等于给旧令牌开门。"""
        repo = RevokedTokenRepository(db_session)
        now = datetime.now(UTC)
        live_jti = uuid.uuid4().hex
        dead_jti = uuid.uuid4().hex

        await repo.add(live_jti, user_id=uuid.uuid4(), expires_at=now + timedelta(days=1))
        await repo.add(dead_jti, user_id=uuid.uuid4(), expires_at=now - timedelta(seconds=1))
        await db_session.commit()

        purged = await repo.purge_expired(now=now)
        await db_session.commit()

        assert purged == 1
        assert await repo.exists(live_jti) is True
        assert await repo.exists(dead_jti) is False


def test_multiple_users_do_not_share_revocations(client) -> None:
    """吊销按 jti 精确匹配：A 用户轮换不能把 B 用户的令牌一起废掉。"""
    first = _register_and_login(client, "chain-a")
    second = _register_and_login(client, "chain-b")

    assert _refresh(client, first["refresh_token"]).status_code == 200
    assert _refresh(client, second["refresh_token"]).status_code == 200
