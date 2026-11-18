"""节点级模型路由端点（Issue #22 延伸：混合路由的用户级配置）。

## 语义

- `GET /api/v1/llm-routing`：列出当前用户的节点路由（含凭据名/模型快照，方便前端展示）；
- `PUT /api/v1/llm-routing`：**全量替换**（前端一次提交所有节点）；`credential_id=null`
  或条目缺失 = 移除该节点路由（回到「跟随任务勾选的模型」）。

## 为什么不做成逐条 PATCH

路由是一个整体决策（用户一次想清楚六个节点各用什么），全量替换避免
「删了 extract 又忘了补 write」的中间态，也不用处理并发增删的次序问题。
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user
from app.db.models.llm_credential import LLMCredential, LLMNodeRouting
from app.db.models.user import User
from app.db.session import get_db

CurrentUserDep = Annotated[User, Depends(get_current_active_user)]
DbDep = Annotated[AsyncSession, Depends(get_db)]

router = APIRouter(prefix="/llm-routing", tags=["llm-routing"])

#: 可路由的节点类型（与 app/llm/router.py 的 contextvar 埋点一一对应）
ROUTABLE_NODE_TYPES = ("planner", "search", "extract", "write", "validate", "review")


class NodeRouteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_type: Literal["planner", "search", "extract", "write", "validate", "review"]
    credential_id: uuid.UUID | None = Field(
        default=None, description="该节点固定使用的凭据；null = 移除路由（跟随任务勾选）"
    )


class NodeRoutingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    routes: list[NodeRouteIn] = Field(default_factory=list, max_length=len(ROUTABLE_NODE_TYPES))


class NodeRouteOut(BaseModel):
    node_type: str
    credential_id: uuid.UUID
    credential_name: str
    model: str
    provider: str


class NodeRoutingOut(BaseModel):
    routes: list[NodeRouteOut]


async def _owned_credential(
    session: AsyncSession, user_id: uuid.UUID, credential_id: uuid.UUID
) -> LLMCredential:
    """校验凭据归属；不存在/不属于你 → 404（与凭据端点同语义）。"""
    result = await session.execute(
        select(LLMCredential).where(
            LLMCredential.id == credential_id, LLMCredential.user_id == user_id
        )
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="凭据不存在")
    return record


@router.get("", response_model=NodeRoutingOut, summary="列出我的节点路由")
async def list_routing(current_user: CurrentUserDep, db: DbDep) -> NodeRoutingOut:
    result = await db.execute(
        select(LLMNodeRouting, LLMCredential)
        .join(LLMCredential, LLMCredential.id == LLMNodeRouting.credential_id)
        .where(LLMNodeRouting.user_id == current_user.id)
        .order_by(LLMNodeRouting.node_type)
    )
    routes = [
        NodeRouteOut(
            node_type=routing.node_type,
            credential_id=routing.credential_id,
            credential_name=cred.name,
            model=cred.model,
            provider=cred.provider,
        )
        for routing, cred in result.all()
    ]
    return NodeRoutingOut(routes=routes)


@router.put("", response_model=NodeRoutingOut, summary="全量替换我的节点路由")
async def update_routing(
    payload: NodeRoutingUpdate, current_user: CurrentUserDep, db: DbDep
) -> NodeRoutingOut:
    """全量替换：请求里没出现的节点类型一律**移除**（回到跟随任务勾选）。"""
    seen: set[str] = set()
    resolved: list[tuple[str, uuid.UUID]] = []
    for route in payload.routes:
        if route.node_type in seen:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"节点类型重复：{route.node_type}",
            )
        seen.add(route.node_type)
        if route.credential_id is None:
            continue  # 显式 null = 移除
        credential = await _owned_credential(db, current_user.id, route.credential_id)
        if credential.status != "active":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"凭据已停用：{credential.name}",
            )
        resolved.append((route.node_type, route.credential_id))

    # 全量替换：先删后插（条数 ≤ 6，单事务）
    existing = await db.execute(
        select(LLMNodeRouting).where(LLMNodeRouting.user_id == current_user.id)
    )
    for row in existing.scalars().all():
        await db.delete(row)
    for node_type, credential_id in resolved:
        db.add(
            LLMNodeRouting(
                user_id=current_user.id,
                node_type=node_type,
                credential_id=credential_id,
            )
        )
    await db.commit()

    # 重读返回（含凭据快照）
    result = await db.execute(
        select(LLMNodeRouting, LLMCredential)
        .join(LLMCredential, LLMCredential.id == LLMNodeRouting.credential_id)
        .where(LLMNodeRouting.user_id == current_user.id)
        .order_by(LLMNodeRouting.node_type)
    )
    routes = [
        NodeRouteOut(
            node_type=routing.node_type,
            credential_id=routing.credential_id,
            credential_name=cred.name,
            model=cred.model,
            provider=cred.provider,
        )
        for routing, cred in result.all()
    ]
    return NodeRoutingOut(routes=routes)
