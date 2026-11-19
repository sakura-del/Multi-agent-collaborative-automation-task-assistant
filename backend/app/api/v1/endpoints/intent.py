"""意图分类端点（Issue #8 P1）：通用入口一句话 → 意图 + 结构化参数。

## 为什么放后端

- 平台模型调用需要统一计费/限流/凭据托管——浏览器直连是 chat-demo 的反模式；
- 分类 prompt 与输出契约集中管理，前端只消费 {intent, topic, keywords}。

## 失败策略（fail-open）

实现见 `app/services/intent_service.py`：平台模型不可用/超时/JSON 解析失败
一律回落「综述任务 + 原文主题」。分类器是优化，不能挡住主流程。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.api.deps import get_current_active_user
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.intent import IntentRequest, IntentResponse
from app.services.intent_service import classify_text

router = APIRouter(prefix="/tasks", tags=["intent"])

_CURRENT_USER = Annotated[User, Depends(get_current_active_user)]
_DB = Annotated[Any, Depends(get_db)]


@router.post(
    "/classify-intent",
    response_model=IntentResponse,
    summary="一句话意图分类（通用入口专用）",
)
async def classify_intent(
    payload: IntentRequest,
    current_user: _CURRENT_USER,
    db: _DB,
) -> IntentResponse:
    """平台模型一次轻量调用；失败 fail-open 放行综述主流程。"""
    del current_user, db  # 鉴权已完成；模型走全局 manager（平台 key）
    return await classify_text(payload.text)
