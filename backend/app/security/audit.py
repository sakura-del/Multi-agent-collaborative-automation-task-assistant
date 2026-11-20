"""审计日志写入（Issue #25 沉淀项 3，补入 #11 安全清单）。

## 记录什么

只记**关键操作**，不做全量请求日志（那是 access log 的职责）：

- 凭据变更（BYOK 创建/更新/删除）——凭据泄露时能回答「谁什么时候加的」；
- 计费变更（管理员手工开通/取消套餐）——涉及钱，必须留痕。

## 为什么自己开一个会话（2026-11-20 生产事故）

第一版复用请求的 `db` 会话，审计写失败时对其 `rollback()` ——
**rollback 会把该会话里已加载的对象全部标记过期**，随后端点读取
`record.id` 触发同步懒加载 → `MissingGreenlet`，用户看到 500，
而业务数据其实已经写入。审计是旁路管道，绝不能影响主流程：
现在用同一 bind 另开一个会话，失败只记日志，调用方对象不受影响。

## 为什么参数是原始值而不是 ORM 对象

`actor=User` 看着方便，但把「读属性」的动作推迟到审计函数里；一旦调用方
会话状态变化（过期/关闭），读属性就是隐患。传 `actor_id/actor_name`
快照，审计函数里没有任何懒加载。

## 为什么不存明文细节

`metadata` 只放操作摘要（改了哪些字段、套餐 id），绝不能放 api_key、
密码、密文。
"""

from __future__ import annotations

import uuid
from typing import Any, cast

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models.audit import AuditLog

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
    actor_id: uuid.UUID | None,
    actor_name: str,
    action: str,
    target_type: str,
    target_id: str | uuid.UUID | None = None,
    metadata: dict[str, Any] | None = None,
    request: Request | None = None,
) -> None:
    """写一条审计日志（独立会话）；失败只记 `audit_write_failed`，不影响业务。"""
    try:
        # 运行期一定是 AsyncEngine（AsyncSession 绑定的引擎）；类型标注偏宽
        bind = cast(AsyncEngine, session.get_bind())
        async with AsyncSession(bind=bind, expire_on_commit=False) as own:
            own.add(
                AuditLog(
                    actor_id=actor_id,
                    actor_name=(actor_name or "system")[:64],
                    action=action[:64],
                    target_type=target_type[:32],
                    target_id=str(target_id)[:64] if target_id is not None else None,
                    metadata_json=metadata or {},
                    ip=request_ip(request),
                )
            )
            await own.commit()
    except Exception as exc:
        logger.error(
            "audit_write_failed",
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            error=str(exc)[:300],
        )
