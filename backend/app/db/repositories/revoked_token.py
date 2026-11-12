"""RevokedToken Repository：refresh 令牌吊销名单（Issue #11 M-1）。

约定（同 `db/repositories/email_token.py`）：
- 事务边界由端点层控制，本层只 flush，不 commit；
- 查询只按主键 `jti` 命中，不做模糊匹配。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select

from app.db.models.revoked_token import RevokedRefreshToken
from app.db.repositories.base import BaseRepository


class RevokedTokenRepository(BaseRepository[RevokedRefreshToken]):
    """已吊销 refresh 令牌的数据访问。"""

    model = RevokedRefreshToken

    async def exists(self, jti: str) -> bool:
        """该 jti 是否已被吊销。"""
        result = await self.db.execute(
            select(RevokedRefreshToken.jti).where(RevokedRefreshToken.jti == jti)
        )
        return result.scalar_one_or_none() is not None

    async def add(self, jti: str, *, user_id: UUID, expires_at: datetime) -> bool:
        """登记一条吊销记录；重复登记是幂等的（重放请求会走到同一路径）。

        返回 ``True`` 表示本次真的写进去了，``False`` 表示该 jti 早已在名单里。

        为什么返回 bool：并发重放时两个请求都会走到这里，靠数据库唯一约束裁决
        （``ON CONFLICT DO NOTHING``）比应用层「先查后插」可靠；
        插入失败的那一方必须知道自己没抢到，否则同一个令牌会被轮换两次。
        """
        values = {"jti": jti, "user_id": user_id, "expires_at": expires_at}
        dialect_name = self.db.get_bind().dialect.name
        # 两种方言的 Insert 类型不同，这里用 Any 抹平（都支持 on_conflict_do_nothing）
        statement: Any
        if dialect_name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as pg_insert

            statement = pg_insert(RevokedRefreshToken).values(**values)
        else:
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert

            statement = sqlite_insert(RevokedRefreshToken).values(**values)

        result = await self.db.execute(statement.on_conflict_do_nothing(index_elements=["jti"]))
        return int(getattr(result, "rowcount", 0) or 0) == 1

    async def get_revoked_at(self, jti: str) -> datetime | None:
        """该 jti 被吊销的时刻；不在名单里返回 ``None``。

        用于区分「刚被轮换掉的令牌被重复提交」（多标签页同时刷新，正常现象）
        与「隔了很久还在用」（基本可以断定是泄露）。
        """
        result = await self.db.execute(
            select(RevokedRefreshToken.created_at).where(RevokedRefreshToken.jti == jti)
        )
        return result.scalar_one_or_none()

    async def purge_expired(self, *, now: datetime) -> int:
        """清掉已经自然过期的吊销记录，返回删除条数。"""
        result = await self.db.execute(
            delete(RevokedRefreshToken).where(RevokedRefreshToken.expires_at < now)
        )
        return int(getattr(result, "rowcount", 0) or 0)
