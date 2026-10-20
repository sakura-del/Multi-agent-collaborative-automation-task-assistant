"""EmailToken Repository：一次性令牌的存储与失效（Phase 1 P1-C）。

约定（同 `db/repositories/knowledge.py`）：
- 只按 ``token_hash`` 查询（调用方负责把明文 token 哈希后传入，本层不接触明文）；
- 事务边界由 service / 端点层统一控制：本层只 flush，不 commit。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update

from app.db.models.email_token import EmailToken
from app.db.repositories.base import BaseRepository


class EmailTokenRepository(BaseRepository[EmailToken]):
    """邮箱/密码生命周期令牌的数据访问。"""

    model = EmailToken

    async def get_by_hash(self, token_hash: str) -> EmailToken | None:
        """按哈希查询令牌；不存在返回 ``None``（不区分「无效」与「不存在」）。"""
        result = await self.db.execute(
            select(EmailToken).where(EmailToken.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def invalidate_outstanding(self, user_id: UUID, purpose: str, *, now: datetime) -> int:
        """把该用户同用途**未消费**的令牌全部标记为已用（重新申请时旧链接作废）。

        返回被标记的条数（仅用于日志/诊断）。
        """
        result = await self.db.execute(
            update(EmailToken)
            .where(
                EmailToken.user_id == user_id,
                EmailToken.purpose == purpose,
                EmailToken.used_at.is_(None),
            )
            .values(used_at=now)
        )
        # AsyncSession.execute 的静态类型是 Result，UPDATE 实际返回 CursorResult（有 rowcount）
        return int(getattr(result, "rowcount", 0) or 0)
