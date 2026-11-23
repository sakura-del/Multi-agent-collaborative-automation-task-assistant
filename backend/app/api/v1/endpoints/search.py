"""联网搜索端点（2026-11-19 用户决策：Tavily 为默认搜索方案）。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/v1/search/web?q=...` | 通用网页搜索（会话/Agent 工具的前置能力） |

## 为什么现在就要有端点

会话内核（P0）的工具网关会直接复用这个 service；先有 HTTP 端点意味着
① 可以在 UI 上立刻加「搜索」演示 ② 部署后能用 curl 独立验收 provider
的可用性与真实返回质量（不依赖会话内核就绪）。

## 鉴权与成本

强制 JWT：搜索按次计费（Tavily credit），开放 Key 会让成本被脚本刷爆。
后续接入会话时，同一用户级额度由工具网关统一扣除。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.api.deps import get_current_active_user
from app.db.models.user import User
from app.tools.search import WebSearchResult, get_search_provider

router = APIRouter(prefix="/search", tags=["search"])

CurrentUserDep = Annotated[User, Depends(get_current_active_user)]


class WebSearchOut(BaseModel):
    query: str
    provider: str
    results: list[WebSearchResult] = Field(default_factory=list)


@router.get("/web", response_model=WebSearchOut, summary="联网搜索（默认 Tavily）")
async def web_search(
    q: Annotated[str, Query(min_length=2, max_length=300, description="搜索词")],
    current_user: CurrentUserDep,
) -> WebSearchOut:
    """返回网页搜索结果；provider 不可用时抛可读错误（502）。"""
    del current_user  # 鉴权已完成；provider 走全局配置
    from app.core.config import get_settings

    provider = get_search_provider()
    results = await provider.search(q.strip())
    return WebSearchOut(
        query=q.strip(),
        provider=get_settings().search.provider,
        results=results,
    )
