"""security 模块测试：密码哈希与 JWT 令牌。

阶段1验收：核心模块覆盖率 ≥70%。
"""

from __future__ import annotations

import uuid

import pytest

from app.core.exceptions import UnauthorizedException
from app.core.security import (
    TOKEN_TYPE_ACCESS,
    TOKEN_TYPE_REFRESH,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


class TestPasswordHashing:
    """密码哈希与校验。"""

    def test_hash_password_returns_bcrypt_hash(self) -> None:
        """哈希结果应以 bcrypt 前缀开头，非明文。"""
        hashed = hash_password("mypassword123")
        assert hashed != "mypassword123"
        assert hashed.startswith("$2")  # bcrypt 哈希前缀

    def test_hash_password_generates_different_salts(self) -> None:
        """同一密码两次哈希结果应不同（salt 随机）。"""
        h1 = hash_password("samepassword")
        h2 = hash_password("samepassword")
        assert h1 != h2

    def test_verify_password_correct(self) -> None:
        """正确密码校验通过。"""
        hashed = hash_password("correctpwd")
        assert verify_password("correctpwd", hashed) is True

    def test_verify_password_wrong(self) -> None:
        """错误密码校验失败。"""
        hashed = hash_password("correctpwd")
        assert verify_password("wrongpwd", hashed) is False

    def test_verify_password_invalid_hash_returns_false(self) -> None:
        """非法哈希格式返回 False 而非抛异常。"""
        assert verify_password("any", "not-a-valid-hash") is False

    def test_truncate_long_password(self) -> None:
        """超过 72 字节的密码应被截断后哈希，不抛异常。"""
        long_pwd = "x" * 100
        hashed = hash_password(long_pwd)
        # 截断后前 72 字节相同，校验应通过
        assert verify_password(long_pwd, hashed) is True
        # 前 72 字节也匹配
        assert verify_password("x" * 72, hashed) is True


class TestJWTToken:
    """JWT 令牌生成与校验。"""

    def test_create_access_token_contains_user_id(self) -> None:
        """access_token 解码后应包含 user_id 与 type=access。"""
        user_id = uuid.uuid4()
        token = create_access_token(user_id, role="user")
        payload = decode_token(token, expected_type=TOKEN_TYPE_ACCESS)
        assert payload["sub"] == str(user_id)
        assert payload["type"] == TOKEN_TYPE_ACCESS
        assert payload["role"] == "user"

    def test_create_refresh_token_type_is_refresh(self) -> None:
        """refresh_token 的 type 字段应为 refresh。"""
        user_id = uuid.uuid4()
        token = create_refresh_token(user_id)
        payload = decode_token(token, expected_type=TOKEN_TYPE_REFRESH)
        assert payload["sub"] == str(user_id)
        assert payload["type"] == TOKEN_TYPE_REFRESH

    def test_decode_token_wrong_type_raises(self) -> None:
        """用 access_token 校验 refresh 类型应抛 401。"""
        token = create_access_token(uuid.uuid4())
        with pytest.raises(UnauthorizedException):
            decode_token(token, expected_type=TOKEN_TYPE_REFRESH)

    def test_decode_token_invalid_raises(self) -> None:
        """非法令牌应抛 401。"""
        with pytest.raises(UnauthorizedException):
            decode_token("invalid.token.here", expected_type=TOKEN_TYPE_ACCESS)

    def test_decode_token_expired_raises(self) -> None:
        """过期令牌应抛 401。"""
        # 通过临时修改过期时间生成已过期令牌
        from datetime import timedelta

        from app.core.security import _create_token

        user_id = uuid.uuid4()
        # 负 timedelta 生成已过期令牌
        token = _create_token(
            subject=str(user_id),
            token_type=TOKEN_TYPE_ACCESS,
            expires_delta=timedelta(seconds=-1),
        )
        with pytest.raises(UnauthorizedException):
            decode_token(token, expected_type=TOKEN_TYPE_ACCESS)
