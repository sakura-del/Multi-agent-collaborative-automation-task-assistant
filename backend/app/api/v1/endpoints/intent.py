"""意图分类端点（Issue #8 P1）：通用入口一句话 → 意图 + 结构化参数。

## 为什么放后端

- 平台模型调用需要统一计费/限流/凭据托管——浏览器直连是 chat-demo 的反模式；
- 分类 prompt 与输出契约集中管理，前端只消费 {intent, topic, keywords}。

## 失败策略（fail-open）

平台模型不可用/超时/JSON 解析失败 → 一律回落
`{intent: "literature_review", topic: 原文, keywords: [原文前 30 字]}`：
入口的本职是综述任务，分类器挂了不该挡住主流程（P1 的关键词提取只是优化）。
"""

from __future__ import annotations

import json
import re
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import get_current_active_user
from app.db.models.user import User
from app.db.session import get_db
from app.llm.models import Message, MessageRole

router = APIRouter(prefix="/tasks", tags=["intent"])

_CURRENT_USER = Annotated[User, Depends(get_current_active_user)]
_DB = Annotated[Any, Depends(get_db)]

_CLASSIFY_SYSTEM = (
    "你是学术写作平台的意图路由器。用户会输入一句话，"
    "判断它是否是要生成文献综述（literature_review）任务，"
    "并提取规范的研究主题（topic，去口语化，≤60 字）与检索关键词（keywords，2~6 个，"
    "中英文均可，单个 ≤20 字）。只输出 JSON："
    '{"intent":"literature_review","topic":"...","keywords":["..."]}，'
    '非综述请求输出 {"intent":"other"}。不要输出任何其他文字。'
)


class IntentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=2, max_length=500)


class IntentResponse(BaseModel):
    intent: Literal["literature_review", "other"]
    topic: str
    keywords: list[str] = Field(default_factory=list, max_length=6)
    source: Literal["llm", "fallback"]


def _fallback(text: str) -> IntentResponse:
    return IntentResponse(
        intent="literature_review",
        topic=text[:60],
        keywords=[text[:30]],
        source="fallback",
    )


def _parse_llm_json(content: str, text: str) -> IntentResponse | None:
    """容错解析：剥 markdown 代码栏 + 提取第一个 JSON 对象。"""
    cleaned = re.sub(r"```(?:json)?|```", "", content).strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match is None:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    intent = data.get("intent")
    if intent == "other":
        return IntentResponse(intent="other", topic=text[:60], keywords=[], source="llm")
    if intent != "literature_review":
        return None
    topic = str(data.get("topic") or text[:60]).strip()
    raw_keywords = data.get("keywords") or []
    keywords = [
        str(item).strip() for item in raw_keywords if isinstance(item, str) and item.strip()
    ][:6]
    return IntentResponse(
        intent="literature_review",
        topic=topic[:60] or text[:60],
        keywords=keywords or [text[:30]],
        source="llm",
    )


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
    from app.llm.factory import get_llm_manager

    text = payload.text.strip()
    manager = get_llm_manager()
    try:
        response = await manager.chat(
            [
                Message(role=MessageRole.SYSTEM, content=_CLASSIFY_SYSTEM),
                Message(role=MessageRole.USER, content=text),
            ],
            temperature=0.1,
            max_tokens=200,
        )
    except Exception:
        return _fallback(text)

    parsed = _parse_llm_json(str(response.content or ""), text)
    return parsed if parsed is not None else _fallback(text)
