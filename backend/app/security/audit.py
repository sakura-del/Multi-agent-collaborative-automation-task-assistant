"""审计日志写入（Issue #25 沉淀项 3，补入 #11 安全清单）。

## 记录什么

只记**关键操作**，不做全量请求日志（那是 access log 的职责，写库会被淹没）：

- 凭据变更（BYOK 创建/更新/删除）——凭据泄露时能回答「谁什么时候加的」；
- 计费变更（管理员手工开通/取消套餐）——涉及钱，必须留痕；
- 账号安全动作（密码重置等）可按需接入同一入口。

## 为什么不存明文细节

`metadata` 只放**操作参数摘要**（改了哪些字段、套餐 id），绝不能放 api_key、
密码、密文。凭据本身只回显 `sk-…abcd`，审计同理。

## 为什么 best-effort 而不是强一致

业务服务内部已提交，审计再插一行；若审计写失败就回滚业务，用户会看到
「操作失败但其实成功了」。这里记 error 日志让监控发现审计管道故障
（`audit_write_failed`），业务照常——与旁路审计管道的业界做法一致。
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models.audit import AuditLog
from app.db.models.user import User

logger = get_logger(__name__)


def request_ip(request: Request | None) -> str | None:
    """客户端 IP（与限流同一口径：trust_proxy 时只认网关覆盖写的 X-Real-IP）。"""
    if request is None:
        return None
    if settings.rate_limit.trust_proxy:
        forwarded = (request.headers.get("x-real-ip") or "").strip()
        if forwarded:
            return forwarded[:64]
    client = request.client
    return (client.host if client else None) or None


async def record_audit(
    session: AsyncSession,
    *,
    actor: User | None,
    action: str,
    target_type: str,
    target_id: str | uuid.UUID | None = None,
    metadata: dict[str, Any] | None = None,
    request: Request | None = None,
) -> None:
    """写一条审计日志；失败只记日志，不影响已完成的业务操作。"""
    try:
        session.add(
            AuditLog(
                actor_id=actor.id if actor is not None else None,
                actor_name=actor.username if actor is not None else "system",
                action=action[:64],
                target_type=target_type[:32],
                target_id=str(target_id)[:64] if target_id is not None else None,
                metadata_json=metadata or {},
                ip=request_ip(request),
            )
        )
        await session.commit()
    except Exception as exc:
        await session.rollback()
        logger.error(
            "audit_write_failed",
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            error=str(exc)[:200],
        )
