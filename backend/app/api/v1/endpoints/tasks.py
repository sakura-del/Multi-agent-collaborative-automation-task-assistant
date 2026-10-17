"""任务生命周期 HTTP adapter（Plan 2 Task 5）。

路由只做 HTTP 校验与响应映射，全部业务委托给 `TaskExecution` 深模块：

    POST   /api/v1/tasks                          创建文献综述任务
    GET    /api/v1/tasks                          当前用户任务列表
    GET    /api/v1/tasks/{task_id}                任务快照
    POST   /api/v1/tasks/{task_id}/plan/approve   确认规划
    POST   /api/v1/tasks/{task_id}/control        人工控制
    GET    /api/v1/tasks/{task_id}/events         事件回放

鉴权：全部端点要求 Bearer access token；跨用户访问统一 404。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse

from app.api.deps import get_current_active_user
from app.db.models.user import User
from app.orchestration.contracts import TaskSnapshot, TaskStatus
from app.orchestration.services.task_execution import TaskExecution
from app.schemas.task import (
    PlanApproval,
    TaskControl,
    TaskCreate,
    TaskCreateOut,
    TaskEventPage,
    TaskSummaryOut,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])


def get_task_execution(request: Request) -> TaskExecution:
    """从应用状态取任务运行时（lifespan 中初始化）。"""
    execution = getattr(request.app.state, "runtime", None)
    if not isinstance(execution, TaskExecution):
        raise RuntimeError("任务运行时未初始化")
    return execution


ExecutionDep = Annotated[TaskExecution, Depends(get_task_execution)]
CurrentUserDep = Annotated[User, Depends(get_current_active_user)]


@router.post(
    "",
    response_model=TaskCreateOut,
    status_code=201,
    summary="创建文献综述任务",
)
async def create_task(
    payload: TaskCreate,
    execution: ExecutionDep,
    current_user: CurrentUserDep,
) -> TaskCreateOut:
    """创建任务：持久化后立即返回，规划在后台执行。"""
    task_id = await execution.submit(current_user.id, payload.to_contract())
    return TaskCreateOut(task_id=task_id, status=TaskStatus.PLANNING.value)


@router.get("", response_model=list[TaskSummaryOut], summary="列出当前用户任务")
async def list_tasks(
    execution: ExecutionDep,
    current_user: CurrentUserDep,
    skip: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[TaskSummaryOut]:
    """分页列出当前用户任务（按创建时间倒序）。"""
    snapshots = await execution.list_tasks(current_user.id, skip=skip, limit=limit)
    return [
        TaskSummaryOut(
            task_id=snapshot.task_id,
            status=snapshot.status,
            title=snapshot.title,
            plan_version=snapshot.plan_version,
            revision=snapshot.revision,
            created_at=snapshot.created_at,
            updated_at=snapshot.updated_at,
        )
        for snapshot in snapshots
    ]


@router.get("/{task_id}", response_model=TaskSnapshot, summary="查询任务快照")
async def get_task(
    task_id: UUID,
    execution: ExecutionDep,
    current_user: CurrentUserDep,
) -> TaskSnapshot:
    """返回任务状态、规划、子任务与复盘报告。"""
    return await execution.get_snapshot(current_user.id, task_id)


@router.post(
    "/{task_id}/plan/approve",
    response_model=TaskSnapshot,
    summary="确认规划并开始执行",
)
async def approve_plan(
    task_id: UUID,
    payload: PlanApproval,
    execution: ExecutionDep,
    current_user: CurrentUserDep,
) -> TaskSnapshot:
    """确认规划：revision 必须等于当前 plan_version。"""
    return await execution.approve_plan(current_user.id, task_id, payload.revision)


@router.post("/{task_id}/control", response_model=TaskSnapshot, summary="人工控制任务")
async def control_task(
    task_id: UUID,
    payload: TaskControl,
    execution: ExecutionDep,
    current_user: CurrentUserDep,
) -> TaskSnapshot:
    """pause / resume / cancel / retry_node / supply_input。"""
    return await execution.control(current_user.id, task_id, payload.to_contract())


@router.get("/{task_id}/events", response_model=TaskEventPage, summary="回放任务事件")
async def list_events(
    task_id: UUID,
    execution: ExecutionDep,
    current_user: CurrentUserDep,
    after: Annotated[int, Query(ge=0)] = 0,
) -> TaskEventPage:
    """返回 after 之后的递增事件，用于断线重连补拉。"""
    events = await execution.events_after(current_user.id, task_id, after)
    next_event_id = events[-1].event_id if events else after
    return TaskEventPage(events=events, next_event_id=next_event_id)


@router.get(
    "/{task_id}/artifacts/{artifact_id}",
    summary="下载任务导出产物（Markdown/DOCX）",
    response_class=FileResponse,
)
async def download_artifact(
    task_id: UUID,
    artifact_id: str,
    execution: ExecutionDep,
    current_user: CurrentUserDep,
) -> FileResponse:
    """owner-scoped 下载：路径由服务端 artifact_id 解析，下载名固定。"""
    path, download_name = await execution.resolve_artifact(current_user.id, task_id, artifact_id)
    media_type = (
        "text/markdown"
        if path.suffix == ".md"
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    return FileResponse(path, media_type=media_type, filename=download_name)
