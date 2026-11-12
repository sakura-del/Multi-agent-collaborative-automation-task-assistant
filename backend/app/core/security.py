"""安全工具：密码哈希与 JWT 令牌。

阶段0任务3：JWT 鉴权基础。
- 密码：passlib bcrypt 哈希存储，不存明文
- 令牌：python-jose 生成/校验 access_token 与 refresh_token
- 令牌类型区分：access（短时业务调用）/ refresh（长时换发新 access）

设计要点：
- 所有时间计算以 UTC 为准，避免时区问题
- sub 字段存 user_id（UUID 字符串），token_invalid_before 支持强制下线
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

# 直接使用 bcrypt 库（不经过 passlib），规避 passlib 1.7.x 与 bcrypt 4.x 的兼容 bug
# passlib 对 bcrypt 4.1+ 会误报 "password cannot be longer than 72 bytes"
import bcrypt
from jose import JWTError, jwt

from app.core.config import settings
from app.core.exceptions import UnauthorizedException

# 令牌类型常量，写入 JWT payload 的 "type" 字段，校验时区分 access / refresh
TOKEN_TYPE_ACCESS = "access"
TOKEN_TYPE_REFRESH = "refresh"


def utc_timestamp(value: datetime) -> float:
    """把数据库读回的 datetime 转成 Unix 秒，**naive 一律按 UTC 解释**。

    为什么需要它：`token_invalid_before` 列是 `DateTime(timezone=True)`，但
    **SQLite 不保留 tzinfo**——读回来是 naive datetime。直接调 `naive.timestamp()`
    会让 Python 按**本地时区**解释：
    - UTC 容器里恰好正确（本地=UTC）；
    - UTC+8 机器上会少算 28800 秒，导致「重置密码后旧 token 应立即失效」被绕过。

    写入端用的是 UTC（`datetime.now(UTC)`），所以读回值也应按 UTC 解释，
    这样行为与部署时区无关。
    """
    return (value.replace(tzinfo=UTC) if value.tzinfo is None else value).timestamp()


def _truncate_pwd(password: str) -> bytes:
    """bcrypt 限制密码最长 72 字节，超出截断（与 passlib 默认行为一致）。"""
    return password.encode("utf-8")[:72]


def hash_password(password: str) -> str:
    """对明文密码做 bcrypt 哈希。"""
    return bcrypt.hashpw(_truncate_pwd(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """校验明文密码与哈希是否匹配。"""
    try:
        return bcrypt.checkpw(_truncate_pwd(plain), hashed.encode("utf-8"))
    except ValueError:
        # 哈希格式非法等异常，统一返回校验失败
        return False


def _create_token(
    *,
    subject: str,
    token_type: str,
    expires_delta: timedelta,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """生成 JWT 令牌的内部实现。

    subject: 令牌主体，此处为 user_id（UUID 字符串）
    token_type: access / refresh，校验时必须匹配
    """
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: uuid.UUID, role: str = "user") -> str:
    """生成 access_token：业务接口调用凭据，有效期较短。"""
    return _create_token(
        subject=str(user_id),
        token_type=TOKEN_TYPE_ACCESS,
        expires_delta=timedelta(minutes=settings.jwt_access_token_expire_minutes),
        extra_claims={"role": role},
    )


def create_refresh_token(user_id: uuid.UUID) -> str:
    """生成 refresh_token：仅用于换发新 access_token，有效期较长。

    **必须带 `jti`**（Issue #11 M-1）：refresh 是一次性凭据，换发后旧令牌要能被精确吊销。
    没有唯一标识就只能按用户整体吊销，多端登录会被互相踢下线。
    """
    return _create_token(
        subject=str(user_id),
        token_type=TOKEN_TYPE_REFRESH,
        expires_delta=timedelta(days=settings.jwt_refresh_token_expire_days),
        extra_claims={"jti": uuid.uuid4().hex},
    )


def decode_token(token: str, expected_type: str) -> dict[str, Any]:
    """解码并校验 JWT 令牌。

    校验项：
    - 签名有效且未过期
    - token_type 与预期一致（防止用 refresh_token 调业务接口）
    任何校验失败均抛 401，由全局异常处理器统一返回。
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError as exc:
        raise UnauthorizedException("令牌无效或已过期", details={"reason": str(exc)}) from exc

    if payload.get("type") != expected_type:
        raise UnauthorizedException(
            "令牌类型不匹配",
            details={"expected": expected_type, "got": payload.get("type")},
        )
    return payload
