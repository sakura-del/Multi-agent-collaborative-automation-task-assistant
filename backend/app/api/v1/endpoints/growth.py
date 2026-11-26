"""轻量漏斗埋点与站内用户反馈端点（Issue #61 P2-10 & P2-11）。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user
from app.core.logging import get_logger
from app.core.security import TOKEN_TYPE_ACCESS, decode_token
from app.db.models.analytics import AnalyticsEvent, UserFeedback
from app.db.models.user import User
from app.db.session import get_db

router = APIRouter(tags=["growth"])
logger = get_logger(__name__)

DbDep = Annotated[AsyncSession, Depends(get_db)]
CurrentUserDep = Annotated[User, Depends(get_current_active_user)]

FunnelEventName = Literal[
    "landing_view",
    "demo_view",
    "register_view",
    "auth_success",
    "task_created",
    "task_completed",
    "demo_task_clicked",
]

FeedbackCategory = Literal["bug", "suggestion", "question", "other"]


class AnalyticsEventIn(BaseModel):
    """前端上报的漏斗埋点（严格白名单，防止任意垃圾灌库）。"""

    model_config = ConfigDict(extra="forbid")

    event_name: FunnelEventName
    visitor_id: str = Field(min_length=4, max_length=64)
    path: str | None = Field(default=None, max_length=255)
    meta: dict[str, Any] | None = None


class AnalyticsEventOut(BaseModel):
    accepted: bool = True


class FeedbackIn(BaseModel):
    """站内反馈提交请求体。"""

    model_config = ConfigDict(extra="forbid")

    category: FeedbackCategory = "suggestion"
    content: str = Field(min_length=2, max_length=2000)
    page_path: str | None = Field(default=None, max_length=255)


class FeedbackOut(BaseModel):
    """站内反馈提交响应体。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    category: str
    content: str
    page_path: str | None = None
    status: str
    created_at: datetime | None = None


def _optional_user_id(request: Request) -> uuid.UUID | None:
    """从 Bearer 请求头静默解析 user_id；失败返回 None，不阻塞公开埋点。"""
    auth_header = request.headers.get("authorization") or ""
    if not auth_header.lower().startswith("bearer "):
        return None
    token = auth_header[7:].strip()
    if not token:
        return None
    try:
        payload = decode_token(token, expected_type=TOKEN_TYPE_ACCESS)
        sub = payload.get("sub")
        return uuid.UUID(str(sub)) if sub else None
    except Exception:
        return None


@router.post(
    "/analytics/events",
    response_model=AnalyticsEventOut,
    status_code=202,
    summary="上报轻量产品漏斗事件（免登录可用）",
)
async def record_analytics_event(
    payload: AnalyticsEventIn,
    request: Request,
    db: DbDep,
) -> AnalyticsEventOut:
    """记录白名单产品漏斗事件，供管理控制台统计注册/创建/完成转化率。"""
    user_id = _optional_user_id(request)
    row = AnalyticsEvent(
        event_name=payload.event_name,
        visitor_id=payload.visitor_id.strip(),
        user_id=user_id,
        path=payload.path.strip() if payload.path else None,
        meta=payload.meta if isinstance(payload.meta, dict) else None,
    )
    db.add(row)
    await db.commit()
    return AnalyticsEventOut(accepted=True)


@router.post(
    "/feedback",
    response_model=FeedbackOut,
    status_code=201,
    summary="提交站内意见反馈",
)
async def submit_feedback(
    payload: FeedbackIn,
    current_user: CurrentUserDep,
    db: DbDep,
) -> FeedbackOut:
    """登录用户提交站内问题反馈与建议。"""
    row = UserFeedback(
        user_id=current_user.id,
        category=payload.category,
        content=payload.content.strip(),
        page_path=payload.page_path.strip() if payload.page_path else None,
        status="open",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    logger.info(
        "user_feedback_submitted",
        feedback_id=str(row.id),
        user_id=str(current_user.id),
        category=row.category,
    )
    return FeedbackOut.model_validate(row)
