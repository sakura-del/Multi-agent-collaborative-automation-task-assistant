"""计费端点（Phase 2 · 计费套餐 Task 5）。

## 三个端点

| 方法 | 路径 | 鉴权 | 说明 |
| --- | --- | --- | --- |
| `GET` | `/billing/usage` | 双通道 | 我的当前套餐与本月用量 |
| `GET` | `/billing/plans` | **公开** | 套餐列表（定价页要用） |
| `POST` | `/billing/subscribe` | **admin only** | 手工开通/变更套餐 |

## 为什么 `plans` 是公开的

定价页要在登录前可看（用户先比价再注册）。套餐列表不含任何用户数据。

## 为什么 `subscribe` 只给 admin

本轮不接支付网关（见计划 §4），开通靠管理员手工操作。
普通用户能自己开通 = 免费升级到机构版。

`source='manual'` + `note` 记录「谁开的、为什么」，为将来对账留依据。
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin_user, get_current_user_or_api_key
from app.billing.services.plan_service import DEFAULT_PERIOD_DAYS, PlanService
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.billing import PlanOut, SubscribeRequest, SubscriptionOut, UsageOut

router = APIRouter(prefix="/billing", tags=["billing"])

#: 用量与套餐列表：双通道（网页 + 开放 API / MCP 都要能查）。
ApiUserDep = Annotated[User, Depends(get_current_user_or_api_key)]
#: 开通套餐：**admin only**（本轮无支付网关，见模块 docstring）。
AdminDep = Annotated[User, Depends(get_current_admin_user)]
DbDep = Annotated[AsyncSession, Depends(get_db)]


def _service(db: AsyncSession) -> PlanService:
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _factory():  # type: ignore[no-untyped-def]
        yield db

    return PlanService(_factory)  # type: ignore[arg-type]


@router.get("/usage", response_model=UsageOut, summary="我的套餐与本月用量")
async def get_usage(current_user: ApiUserDep, db: DbDep) -> UsageOut:
    """当前套餐 + 本月已用/剩余篇数。

    `reviews_remaining` 为 `null` 表示不限篇数（机构版）。
    前端进度条直接用这三个字段，不要自己算（口径在服务端）。
    """
    service = _service(db)
    usage = await service.get_usage(current_user.id)
    return UsageOut(**usage)  # type: ignore[arg-type]


@router.get("/plans", response_model=list[PlanOut], summary="套餐列表（公开）")
async def list_plans(db: DbDep) -> list[PlanOut]:
    """上架套餐（按价格升序）。**无需鉴权** —— 定价页要能登录前展示。"""
    service = _service(db)
    plans = await service.list_active_plans()
    return [
        PlanOut(
            plan_id=plan.id,
            name=plan.name,
            monthly_reviews=plan.monthly_reviews,
            max_workspaces=plan.max_workspaces,
            max_seats_per_workspace=plan.max_seats_per_workspace,
            allow_api_access=plan.allow_api_access,
            allow_team=plan.allow_team,
            price_cents=plan.price_cents,
            description=plan.description,
        )
        for plan in plans
    ]


@router.post(
    "/subscribe", response_model=SubscriptionOut, status_code=201, summary="开通/变更套餐（管理员）"
)
async def subscribe(
    payload: SubscribeRequest,
    current_admin: AdminDep,
    db: DbDep,
) -> SubscriptionOut:
    """手工开通或变更套餐（**仅管理员**）。

    `note` 会落库，用于回答「这个用户的专业版是谁开的、为什么」。
    """
    service = _service(db)
    try:
        subscription = await service.subscribe(
            payload.user_id,
            payload.plan_id,
            note=payload.note or f"由管理员 {current_admin.username} 手工开通",
            days=payload.days or DEFAULT_PERIOD_DAYS,
            seats=payload.seats,
        )
    except ValueError as exc:
        message = str(exc)
        status = 404 if "不存在" in message else 400
        raise HTTPException(status_code=status, detail=message) from exc
    return SubscriptionOut(
        subscription_id=subscription.id,
        user_id=subscription.user_id,
        plan_id=subscription.plan_id,
        status=subscription.status,
        period_start=subscription.period_start,
        period_end=subscription.period_end,
        seats_purchased=subscription.seats_purchased,
        source=subscription.source,
        note=subscription.note,
    )


@router.post("/cancel/{user_id}", status_code=204, summary="取消套餐（管理员）")
async def cancel_subscription(user_id: UUID, current_admin: AdminDep, db: DbDep) -> None:
    """取消某用户的订阅（回落免费档）。仅管理员。"""
    service = _service(db)
    await service.cancel(user_id)


#: 让 `Any` 导入不被 lint 判为未使用（FastAPI 依赖签名里的 `Any`）
__all__ = ["router"]
_ = Any
