"""任务事件 WebSocket 与连接票据端点（Plan 2 Task 6）。

    POST /api/v1/ws-ticket                  申请一次性连接票据（需任务归属）
    WS   /api/v1/tasks/{task_id}/ws         快照 + 事件回放 + 实时事件

连接顺序（断线重连协议）：
1. 消费 ticket（单次、60 秒有效，未通过则不 accept）；
2. 读取 owner-scoped 快照；
3. accept 并发送 `{"type": "snapshot", "data": ...}`；
4. 补拉 `after_event_id` 之后的事件；
5. 转发 Redis/内存总线的实时事件（按 event_id 去重）；
6. 断开时停止订阅。

安全约定：不记录 ticket、access token 与完整事件 payload。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, WebSocket, WebSocketDisconnect

from app.api.deps import get_current_active_user
from app.api.v1.endpoints.tasks import get_task_execution
from app.core.exceptions import NotFoundException
from app.db.models.user import User
from app.orchestration.events import TICKET_TTL_SECONDS
from app.orchestration.services.task_execution import TaskExecution
from app.schemas.task import WebSocketTicketOut, WebSocketTicketRequest

router = APIRouter(tags=["tasks"])

CurrentUserDep = Annotated[User, Depends(get_current_active_user)]

# WebSocket 关闭码
_WS_BAD_REQUEST = 4400
_WS_UNAUTHORIZED = 4401
_WS_NOT_FOUND = 4404


def _get_container(request: Request):
    container = getattr(request.app.state, "container", None)
    if container is None:
        raise RuntimeError("任务运行时未初始化")
    return container


@router.post(
    "/ws-ticket",
    response_model=WebSocketTicketOut,
    summary="申请任务事件 WebSocket 连接票据",
)
async def issue_ws_ticket(
    payload: WebSocketTicketRequest,
    request: Request,
    current_user: CurrentUserDep,
    execution: Annotated[TaskExecution, Depends(get_task_execution)],
) -> WebSocketTicketOut:
    """先校验任务归属（跨用户 404），再签发 60 秒单次票据。"""
    await execution.get_snapshot(current_user.id, payload.task_id)
    container = _get_container(request)
    ticket = await container.ticket_service.issue(current_user.id, payload.task_id)
    return WebSocketTicketOut(ticket=ticket, expires_in=TICKET_TTL_SECONDS)


@router.websocket("/tasks/{task_id}/ws")
async def task_events_ws(
    websocket: WebSocket,
    task_id: UUID,
    ticket: Annotated[str, Query()] = "",
    after_event_id: Annotated[int, Query(ge=0)] = 0,
) -> None:
    """任务事件流：快照 → 补拉 → 实时转发。"""
    container = getattr(websocket.app.state, "container", None)
    execution = getattr(websocket.app.state, "runtime", None)
    if container is None or not isinstance(execution, TaskExecution):
        await websocket.close(code=_WS_BAD_REQUEST)
        return

    # 1. 先消费票据（未通过不 accept，避免未授权连接占用资源）
    user_id = await container.ticket_service.consume(ticket, task_id)
    if user_id is None:
        await websocket.close(code=_WS_UNAUTHORIZED)
        return

    # 2. owner-scoped 快照
    try:
        snapshot = await execution.get_snapshot(user_id, task_id)
    except NotFoundException:
        await websocket.close(code=_WS_NOT_FOUND)
        return

    # 3. 先建立订阅，避免 accept 与订阅之间的事件丢失
    last_sent = after_event_id
    subscription = container.event_publisher.subscribe(task_id)
    try:
        await websocket.accept()
        await websocket.send_json({"type": "snapshot", "data": snapshot.model_dump(mode="json")})

        # 4. 补拉断线期间遗漏的事件
        for event in await execution.events_after(user_id, task_id, after_event_id):
            await websocket.send_json({"type": "event", "data": event.model_dump(mode="json")})
            last_sent = event.event_id

        # 5. 转发实时事件（按 event_id 去重）
        async for event in subscription:
            if event.event_id <= last_sent:
                continue
            await websocket.send_json({"type": "event", "data": event.model_dump(mode="json")})
            last_sent = event.event_id
    except WebSocketDisconnect:
        return
    finally:
        # 6. 断开时停止订阅
        await subscription.aclose()
