"""管理员侧 BYOK 渗透率看板（Issue #15 P2）。

## 为什么单独一个 admin 路由

- 普通用户的 `/billing/usage/*` 只读自己的数据；
- 看板要跨用户聚合，必须 `admin` 角色，且**不能**走开放 API Key
  （Key 属于用户，不能用来读全站数据）。

## 指标口径

见 `app/billing/byok_analytics.py` 的模块文档：任务占比、token 占比、
用户渗透率、有凭据用户占比四个数各有分母，页面上必须一起显示，
否则「渗透率 0%」分不清是没人用还是没人配。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin_user
from app.billing.byok_analytics import build_byok_overview
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.admin_byok import ByokOverviewOut

router = APIRouter(prefix="/admin/byok", tags=["admin"])

AdminDep = Annotated[User, Depends(get_current_admin_user)]
DbDep = Annotated[AsyncSession, Depends(get_db)]


@router.get("/overview", response_model=ByokOverviewOut, summary="BYOK 渗透率看板")
async def byok_overview(
    _admin: AdminDep,
    db: DbDep,
    days: Annotated[int, Query(ge=1, le=90)] = 30,
) -> ByokOverviewOut:
    """窗口内 BYOK 任务/token 占比、用户渗透率、凭据健康与 Top 用户。"""
    data = await build_byok_overview(db, days=days)
    return ByokOverviewOut(**data)  # type: ignore[arg-type]
