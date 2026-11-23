"""Agent 工具网关 HTTP 适配（Issue #8 P1）。

    GET  /api/v1/agent/tools                 工具目录（12 个，含 JSON Schema）
    POST /api/v1/agent/tools/{name}/invoke   执行一个工具（写操作需 confirmed）
    POST /api/v1/agent/route                 一句话 → 意图 + 建议工具调用

## 为什么鉴权只用 JWT

工具包含创建/取消任务、导入文献等写操作，且没有开放 API 的 scope 体系
（`tasks:write` 等只覆盖任务端点）。宁可先只允许登录态调用，也不要把
「取消任务」暴露给一把只读 API Key。开放 API 需要工具能力时，应先在
Key 上补 scope 再放开。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user
from app.api.v1.endpoints.tasks import ExecutionDep, PlanServiceDep, QuotaDep, WorkspaceIdsDep
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.intent import IntentResponse
from app.services.intent_service import classify_text
from app.tools.agent import ToolContext, invoke_tool, list_tools

router = APIRouter(prefix="/agent", tags=["agent"])

CurrentUserDep = Annotated[User, Depends(get_current_active_user)]
DbDep = Annotated[AsyncSession, Depends(get_db)]


class ToolInvokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    arguments: dict[str, Any] = Field(
        default_factory=dict, description="工具参数（按 JSON Schema）"
    )
    confirmed: bool = Field(
        False,
        description="写操作必须显式确认；未确认返回 409 TOOL_CONFIRMATION_REQUIRED",
    )


class ToolCatalogOut(BaseModel):
    count: int
    tools: list[dict[str, Any]]


class AgentRouteResponse(IntentResponse):
    """意图分类 + 建议的工具调用（前端可直接渲染确认卡）。"""

    suggested_tool: str | None = None
    suggested_arguments: dict[str, Any] | None = None
    message: str | None = None


@router.get("/tools", response_model=ToolCatalogOut, summary="工具目录（12 个）")
async def get_tools(
    current_user: CurrentUserDep,
    domain: Literal["tasks", "references", "knowledge", "quota", "search"] | None = None,
) -> ToolCatalogOut:
    del current_user  # 仅要求登录；工具目录本身不含用户数据
    tools = list_tools(domain)
    return ToolCatalogOut(count=len(tools), tools=tools)


@router.post(
    "/tools/{name}/invoke",
    summary="执行工具（写操作需 confirmed=true）",
)
async def invoke(
    name: str,
    payload: ToolInvokeRequest,
    current_user: CurrentUserDep,
    db: DbDep,
    execution: ExecutionDep,
    plan_service: PlanServiceDep,
    quota: QuotaDep,
    workspace_ids: WorkspaceIdsDep,
) -> dict[str, Any]:
    ctx = ToolContext(
        db=db,
        user_id=current_user.id,
        execution=execution,
        plan_service=plan_service,
        quota=quota,
        workspace_ids=workspace_ids,
    )
    return await invoke_tool(ctx, name, payload.arguments, confirmed=payload.confirmed)


class AgentRouteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=2, max_length=500)


@router.post("/route", response_model=AgentRouteResponse, summary="一句话路由到工具")
async def route(
    payload: AgentRouteRequest,
    current_user: CurrentUserDep,
) -> AgentRouteResponse:
    """**复用意图分类端点同一实现**（`classify_text`），不复制 prompt。

    综述意图 → 建议 `create_task`（topic/keywords 已规范化，前端出确认卡）；
    其他意图 → 明确告知当前不支持，避免模型自由发挥。
    """
    del current_user  # 仅要求登录：分类不读用户数据，但也不允许匿名刷模型
    intent = await classify_text(payload.text)
    if intent.intent != "literature_review":
        return AgentRouteResponse(
            **intent.model_dump(),
            message="当前入口只支持文献综述任务；其他类型待后续接入。",
        )
    return AgentRouteResponse(
        **intent.model_dump(),
        suggested_tool="create_task",
        suggested_arguments={"topic": intent.topic, "keywords": intent.keywords},
    )
