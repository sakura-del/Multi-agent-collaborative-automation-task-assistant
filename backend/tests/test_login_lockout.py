"""登录失败退避与限流收紧（Issue #11 M-3）。

## 现状（2026-11-11 安全排查实测）

- 登录限流 10 次/60 秒：**10 次失败不触发 429**，11 次才限——慢速爆破可以长期跑；
- 没有任何账号级失败计数：换 IP 就能继续试同一个账号。

## 修复口径

1. 登录限流收紧到 5 次/60 秒（IP 维度，挡「同一出口的高频尝试」）；
2. 增加**账号维度**的失败计数：连续失败 N 次后锁定 T 分钟，
   期间即使密码正确也返回 429（否则爆破者只要猜中就照常登录，退避就白做了）；
3. 登录成功即清零（正常用户偶尔打错字不会累积到锁定）；
4. 计数与锁定按**规范化后的账号名**（去空格 + 小写），避免 `Demo` / `demo` 各记一份；
5. 兜底组件故障时 fail-open：Redis 不可用不能把所有人挡在门外。
"""

from __future__ import annotations

import pytest

from app.core.login_throttle import InMemoryLoginThrottle

LOCKED_LIMIT = 3


def _register(client, username: str = "lockuser", password: str = "password123") -> None:
    client.post(
        "/api/v1/auth/register",
        json={"username": username, "email": f"{username}@test.com", "password": password},
    )


def _login(client, account: str, password: str):
    return client.post("/api/v1/auth/login", json={"account": account, "password": password})


@pytest.fixture
def throttle():
    """把应用里的登录退避换成「阈值 3、窗口 15 分钟」的内存实现。"""
    from app.main import app

    instance = InMemoryLoginThrottle(limit=LOCKED_LIMIT, window_seconds=900)
    previous = getattr(app.state, "login_throttle", None)
    app.state.login_throttle = instance
    yield instance
    app.state.login_throttle = previous


class TestAccountLockout:
    def test_locks_after_repeated_failures(self, client, throttle) -> None:
        _register(client)
        for _ in range(LOCKED_LIMIT):
            assert _login(client, "lockuser", "wrong-password").status_code == 401

        # 达到阈值后即使密码正确也必须拒绝：否则爆破者猜中就登录，退避没有意义
        locked = _login(client, "lockuser", "password123")
        assert locked.status_code == 429, locked.text
        body = locked.json()
        assert body["error_code"] == "RATE_LIMITED"
        assert int(locked.headers["Retry-After"]) > 0
        assert body["details"]["reason"] == "account_locked"

    def test_lock_is_per_account(self, client, throttle) -> None:
        _register(client, "lockuser")
        _register(client, "otheruser")
        for _ in range(LOCKED_LIMIT):
            _login(client, "lockuser", "wrong-password")

        assert _login(client, "lockuser", "password123").status_code == 429
        # 别人不受牵连（同一 IP 也照常能登）
        assert _login(client, "otheruser", "password123").status_code == 200

    def test_success_resets_counter(self, client, throttle) -> None:
        _register(client)
        for _ in range(LOCKED_LIMIT - 1):
            assert _login(client, "lockuser", "wrong-password").status_code == 401
        assert _login(client, "lockuser", "password123").status_code == 200

        # 清零后再错几次仍不应锁定
        for _ in range(LOCKED_LIMIT - 1):
            assert _login(client, "lockuser", "wrong-password").status_code == 401
        assert _login(client, "lockuser", "password123").status_code == 200

    def test_case_and_space_variation_shares_counter(self, client, throttle) -> None:
        """`Demo` / ` demo ` 与 `demo` 必须共用一个计数器，否则换个大小写就绕过退避。"""
        _register(client, "lockuser")
        for _ in range(LOCKED_LIMIT):
            _login(client, " LOCKUSER ", "wrong-password")

        assert _login(client, "lockuser", "password123").status_code == 429

    def test_unknown_account_also_counts(self, client, throttle) -> None:
        """不存在的账号同样计数：否则「只锁真实账号」本身就是用户枚举信号。"""
        for _ in range(LOCKED_LIMIT):
            assert _login(client, "ghost-user", "whatever").status_code == 401

        locked = _login(client, "ghost-user", "whatever")
        assert locked.status_code == 429


class TestThrottleFailOpen:
    def test_throttle_error_does_not_block_login(self, client, monkeypatch) -> None:
        """退避组件故障（Redis 挂了）不能把所有人挡在门外。"""
        from app.main import app

        class _Broken:
            async def check(self, account: str) -> int | None:
                raise RuntimeError("redis down")

            async def register_failure(self, account: str) -> int:
                raise RuntimeError("redis down")

            async def reset(self, account: str) -> None:
                raise RuntimeError("redis down")

        previous = getattr(app.state, "login_throttle", None)
        app.state.login_throttle = _Broken()
        try:
            _register(client)
            assert _login(client, "lockuser", "password123").status_code == 200
        finally:
            app.state.login_throttle = previous


class TestInMemoryThrottleUnit:
    async def test_counts_up_to_limit(self) -> None:
        throttle = InMemoryLoginThrottle(limit=3, window_seconds=60)
        assert await throttle.check("a") is None
        for _ in range(2):
            await throttle.register_failure("a")
        assert await throttle.check("a") is None
        await throttle.register_failure("a")
        assert await throttle.check("a") is not None

    async def test_reset_clears(self) -> None:
        throttle = InMemoryLoginThrottle(limit=2, window_seconds=60)
        await throttle.register_failure("a")
        await throttle.register_failure("a")
        assert await throttle.check("a") is not None

        await throttle.reset("a")
        assert await throttle.check("a") is None

    async def test_window_expiry_releases_lock(self) -> None:
        clock = {"now": 1000.0}
        throttle = InMemoryLoginThrottle(limit=2, window_seconds=60, clock=lambda: clock["now"])
        await throttle.register_failure("a")
        await throttle.register_failure("a")
        assert await throttle.check("a") is not None

        clock["now"] += 61
        assert await throttle.check("a") is None

    async def test_lock_reports_remaining_seconds(self) -> None:
        clock = {"now": 1000.0}
        throttle = InMemoryLoginThrottle(limit=1, window_seconds=60, clock=lambda: clock["now"])
        await throttle.register_failure("a")
        clock["now"] += 20

        remaining = await throttle.check("a")
        assert remaining is not None and 39 <= remaining <= 41


class TestLoginLimitTightened:
    def test_login_rule_is_five_per_minute(self) -> None:
        """原 10 次/60 秒实测「10 次失败都不触发 429」，慢速爆破成本太低。"""
        from app.core.config import settings

        rule = settings.rate_limit.rules.auth_login
        assert rule.limit == 5
        assert rule.window_seconds == 60

    def test_lockout_threshold_is_configured(self) -> None:
        from app.core.config import settings

        assert settings.rate_limit.login_failure_limit > 0
        assert settings.rate_limit.login_failure_window_seconds >= 60
