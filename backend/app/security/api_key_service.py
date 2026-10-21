"""API Key 服务（Phase 2 · 开放 API Task 2）。

## 三条安全原则

**① 明文只在创建时出现一次。**
`create` 返回 `(record, plaintext)`，之后任何读取路径都拿不到明文 ——
连数据库里都只有哈希。

**② 失效原因不对外区分。**
吊销 / 过期 / 不存在 → 统一返回 `None`，调用方一律 401。
区分原因等于告诉攻击者「这个 Key 曾经有效」（与登录「用户名或密码错误」同理）。

**③ `last_used_at` 写库要节流。**
不节流的代价：每个 API 请求一次 UPDATE，高并发下是真实的写放大。
60 秒窗口内只写一次 —— `last_used_at` 的用途是「判断 Key 是否还在用」，
秒级精度毫无意义。

## Scope 判定

`has_scope` 精确匹配（不做通配）。scope 字符串是**契约**，
`API_KEY_SCOPES` 是唯一真源；端点声明时若拼错，鉴权会静默失效 ——
所以端点侧用常量而不是字面量（见 `app/api/deps.py`）。
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.db.models.api_key import (
    API_KEY_PREFIX,
    API_KEY_SCOPES,
    DEFAULT_API_KEY_SCOPES,
    PREFIX_DISPLAY_LENGTH,
    ApiKey,
)
from app.db.models.user import User

logger = get_logger(__name__)

#: 明文随机部分字节数（32 字节 ≈ 256 bit 熵，不可猜）。
_KEY_BYTES = 32

#: `last_used_at` 写库节流窗口（秒）。
_LAST_USED_THROTTLE_SECONDS = 60


def hash_api_key(plaintext: str) -> str:
    """计算 Key 的存储哈希（sha256 hex）。

    **为什么用 sha256 而不是 bcrypt/argon2**：
    - Key 是高熵随机串（256 bit），不存在「弱口令字典」攻击面；
    - 认证是**每个 API 请求都要做**的热路径，bcrypt 的 ~100ms 会让 API 不可用；
    - 密码用 bcrypt 是因为密码熵低、需要慢哈希；Key 不需要。

    这是行业标准做法（GitHub / Stripe 的 API Key 都是快速哈希）。
    """
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


class ApiKeyService:
    """API Key 的生成、校验与吊销。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    # ===== 生成 =====

    async def create(
        self,
        user_id: uuid.UUID,
        *,
        name: str,
        scopes: list[str] | None = None,
        expires_in_days: int | None = None,
    ) -> tuple[ApiKey, str]:
        """生成新 Key，返回 `(记录, 明文)`。

        **明文只在此刻返回一次**，调用方必须立刻展示给用户（前端一次性弹窗 +
        复制按钮）。之后任何接口都不返回明文。
        """
        resolved_scopes = list(scopes) if scopes is not None else list(DEFAULT_API_KEY_SCOPES)
        if not resolved_scopes:
            raise ValueError("至少需要一个 scope（空 scope 的 Key 什么都不能做）")
        unknown = [scope for scope in resolved_scopes if scope not in API_KEY_SCOPES]
        if unknown:
            raise ValueError(
                f"未知 scope：{', '.join(unknown)}（可选：{', '.join(API_KEY_SCOPES)}）"
            )

        clean_name = (name or "").strip()
        if not clean_name:
            raise ValueError("Key 名称不能为空")

        plaintext = API_KEY_PREFIX + secrets.token_urlsafe(_KEY_BYTES)
        expires_at = (
            datetime.now(UTC) + timedelta(days=expires_in_days)
            if expires_in_days is not None
            else None
        )

        async with self._session_factory() as session:
            record = ApiKey(
                user_id=user_id,
                name=clean_name,
                prefix=plaintext[:PREFIX_DISPLAY_LENGTH],
                key_hash=hash_api_key(plaintext),
                scopes=resolved_scopes,
                expires_at=expires_at,
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
            logger.info(
                "api_key_created",
                key_id=str(record.id),
                user_id=str(user_id),
                prefix=record.prefix,
                scopes=resolved_scopes,
            )
            return record, plaintext

    # ===== 校验 =====

    async def authenticate(self, plaintext: str) -> tuple[ApiKey, User] | None:
        """校验明文 Key，返回 `(记录, 用户)`；任何失效原因都返回 `None`。"""
        candidate = (plaintext or "").strip()
        if not candidate or not candidate.startswith(API_KEY_PREFIX):
            return None

        async with self._session_factory() as session:
            record = await session.scalar(
                select(ApiKey).where(ApiKey.key_hash == hash_api_key(candidate))
            )
            if record is None:
                return None
            if record.revoked_at is not None:
                return None
            if record.expires_at is not None:
                expires_at = record.expires_at
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=UTC)
                if expires_at <= datetime.now(UTC):
                    return None

            user = await session.get(User, record.user_id)
            if user is None or not user.is_active:
                # 用户被禁用后其 Key 也立即失效（否则禁用形同虚设）
                return None

            await self._touch_last_used(session, record)
            return record, user

    async def _touch_last_used(self, session: AsyncSession, record: ApiKey) -> None:
        """更新 `last_used_at`（**节流**，见模块 docstring 第 ③ 条）。"""
        now = datetime.now(UTC)
        if record.last_used_at is not None:
            last = record.last_used_at
            if last.tzinfo is None:
                last = last.replace(tzinfo=UTC)
            if (now - last).total_seconds() < _LAST_USED_THROTTLE_SECONDS:
                return
        record.last_used_at = now
        await session.commit()

    # ===== Scope =====

    @staticmethod
    def has_scope(record: ApiKey, scope: str) -> bool:
        """精确匹配 scope（不做通配）。"""
        return scope in (record.scopes or [])

    # ===== 列表与吊销 =====

    async def list_for_user(
        self, user_id: uuid.UUID, *, include_revoked: bool = False
    ) -> list[ApiKey]:
        """列出用户的 Key。

        默认**不含已吊销的**（用户视角只看还能用的）；
        `include_revoked=True` 给审计/管理视角用。
        排序带 `id`：同事务写入的行 `created_at` 相同（AGENTS #14）。
        """
        async with self._session_factory() as session:
            stmt = (
                select(ApiKey)
                .where(ApiKey.user_id == user_id)
                .order_by(ApiKey.created_at.desc(), ApiKey.id.desc())
            )
            if not include_revoked:
                stmt = stmt.where(ApiKey.revoked_at.is_(None))
            rows = await session.execute(stmt)
            return list(rows.scalars().all())

    async def revoke(self, user_id: uuid.UUID, key_id: uuid.UUID) -> None:
        """吊销 Key（软删除，保留审计）。重复吊销幂等。"""
        async with self._session_factory() as session:
            record = await session.scalar(
                select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == user_id)
            )
            if record is None:
                raise ValueError("Key 不存在")
            if record.revoked_at is None:
                record.revoked_at = datetime.now(UTC)
                await session.commit()
                logger.info(
                    "api_key_revoked",
                    key_id=str(key_id),
                    user_id=str(user_id),
                    prefix=record.prefix,
                )
