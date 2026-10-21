"""工作区端点（Phase 2 · 团队工作区 Task 2/5）。

## 错误映射

| 服务层异常 | HTTP | 说明 |
| --- | --- | --- |
| `WorkspaceError("工作区不存在")` | **404** | 非成员一律「不存在」，不泄漏存在性 |
| `WorkspaceError(其他)` | **400/409** | slug 冲突、席位已满、owner 保护等 |
| `PermissionError_` | **403** | 成员但角色不足 |
| `InviteError` | **400** | 邀请码无效/过期/已使用 |

## 为什么席位相关错误用 409

「席位已满」是**资源状态冲突**，不是请求格式错误。409 能让前端区分
「改个参数就能成功」（400）与「需要先扩容」（409），提示文案完全不同。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user
from app.collaboration.services.workspace_service import (
    InviteError,
    PermissionError_,
    WorkspaceError,
    WorkspaceService,
)
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.workspace import (
    InviteAccept,
    InviteCreate,
    InviteOut,
    MemberOut,
    RoleChange,
    WorkspaceCreate,
    WorkspaceOut,
    WorkspaceRename,
)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])

CurrentUserDep = Annotated[User, Depends(get_current_active_user)]
DbDep = Annotated[AsyncSession, Depends(get_db)]


def _service(db: AsyncSession) -> WorkspaceService:
    """用请求级 session 适配 `WorkspaceService` 需要的 session_factory。"""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _factory():  # type: ignore[no-untyped-def]
        yield db

    return WorkspaceService(_factory)  # type: ignore[arg-type]


def _map_error(exc: Exception) -> HTTPException:
    """服务层异常 → HTTP（统一口径，避免每个端点重复判断）。"""
    if isinstance(exc, PermissionError_):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, InviteError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, WorkspaceError):
        message = str(exc)
        if "不存在" in message:
            return HTTPException(status_code=404, detail=message)
        if "席位已满" in message or "已被占用" in message:
            return HTTPException(status_code=409, detail=message)
        return HTTPException(status_code=400, detail=message)
    return HTTPException(status_code=400, detail=str(exc))


async def _to_out(service: WorkspaceService, user_id: UUID, workspace) -> WorkspaceOut:  # type: ignore[no-untyped-def]
    used, limit = await service.seat_usage(workspace.id)
    role = await service.get_role(user_id, workspace.id)
    return WorkspaceOut(
        workspace_id=workspace.id,
        name=workspace.name,
        slug=workspace.slug,
        owner_id=workspace.owner_id,
        seat_limit=limit,
        seat_used=used,
        my_role=role,
        created_at=workspace.created_at,
    )


# ===== 工作区 =====


@router.post("", response_model=WorkspaceOut, status_code=201, summary="创建工作区")
async def create_workspace(
    payload: WorkspaceCreate, current_user: CurrentUserDep, db: DbDep
) -> WorkspaceOut:
    """创建工作区；创建者自动成为 owner（占一个席位）。"""
    service = _service(db)
    try:
        workspace = await service.create(
            current_user.id,
            name=payload.name,
            slug=payload.slug,
            seat_limit=payload.seat_limit,
        )
        return await _to_out(service, current_user.id, workspace)
    except (WorkspaceError, PermissionError_) as exc:
        raise _map_error(exc) from exc


@router.get("", response_model=list[WorkspaceOut], summary="我参与的工作区")
async def list_workspaces(current_user: CurrentUserDep, db: DbDep) -> list[WorkspaceOut]:
    service = _service(db)
    workspaces = await service.list_for_user(current_user.id)
    return [await _to_out(service, current_user.id, workspace) for workspace in workspaces]


@router.get("/{workspace_id}", response_model=WorkspaceOut, summary="工作区详情")
async def get_workspace(
    workspace_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> WorkspaceOut:
    """非成员返回 404（不泄漏存在性）。"""
    service = _service(db)
    workspace = await service.get_for_user(current_user.id, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="工作区不存在")
    return await _to_out(service, current_user.id, workspace)


@router.patch("/{workspace_id}", response_model=WorkspaceOut, summary="改名")
async def rename_workspace(
    workspace_id: UUID, payload: WorkspaceRename, current_user: CurrentUserDep, db: DbDep
) -> WorkspaceOut:
    """改名：**仅 owner**（团队名是对外标识）。"""
    service = _service(db)
    try:
        workspace = await service.rename(current_user.id, workspace_id, name=payload.name)
        return await _to_out(service, current_user.id, workspace)
    except (WorkspaceError, PermissionError_) as exc:
        raise _map_error(exc) from exc


# ===== 成员（席位） =====


@router.get("/{workspace_id}/members", response_model=list[MemberOut], summary="成员列表")
async def list_members(
    workspace_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> list[MemberOut]:
    service = _service(db)
    profiles = await service.member_profiles(current_user.id, workspace_id)
    if profiles is None:
        raise HTTPException(status_code=404, detail="工作区不存在")
    return [MemberOut(**profile) for profile in profiles]  # type: ignore[arg-type]


@router.delete("/{workspace_id}/members/{user_id}", status_code=204, summary="移除成员")
async def remove_member(
    workspace_id: UUID, user_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> None:
    """移除成员（admin+；不能移除 owner，不能操作同级或更高角色）。"""
    service = _service(db)
    try:
        await service.remove_member(current_user.id, workspace_id, user_id)
    except (WorkspaceError, PermissionError_) as exc:
        raise _map_error(exc) from exc


@router.patch("/{workspace_id}/members/{user_id}", response_model=MemberOut, summary="改成员角色")
async def change_role(
    workspace_id: UUID,
    user_id: UUID,
    payload: RoleChange,
    current_user: CurrentUserDep,
    db: DbDep,
) -> MemberOut:
    """改角色：**仅 owner**（不能改 owner 自己）。"""
    service = _service(db)
    try:
        member = await service.change_role(
            current_user.id, workspace_id, user_id, role=payload.role
        )
    except (WorkspaceError, PermissionError_) as exc:
        raise _map_error(exc) from exc
    profiles = await service.member_profiles(current_user.id, workspace_id) or []
    for profile in profiles:
        if profile["user_id"] == member.user_id:
            return MemberOut(**profile)  # type: ignore[arg-type]
    raise HTTPException(status_code=404, detail="成员不存在")


@router.post("/{workspace_id}/leave", status_code=204, summary="退出工作区")
async def leave_workspace(workspace_id: UUID, current_user: CurrentUserDep, db: DbDep) -> None:
    """主动退出（owner 不能退出，需先转让或解散）。"""
    service = _service(db)
    try:
        await service.leave(current_user.id, workspace_id)
    except (WorkspaceError, PermissionError_) as exc:
        raise _map_error(exc) from exc


# ===== 邀请 =====


@router.post(
    "/{workspace_id}/invites", response_model=InviteOut, status_code=201, summary="生成邀请码"
)
async def create_invite(
    workspace_id: UUID, payload: InviteCreate, current_user: CurrentUserDep, db: DbDep
) -> InviteOut:
    """生成一次性邀请码（admin+）。

    邀请码**只在这里返回一次**（列表接口不返回）：等同临时凭证，
    能重复读取会让「一次性」形同虚设。
    """
    service = _service(db)
    try:
        invite = await service.create_invite(current_user.id, workspace_id, role=payload.role)
    except (WorkspaceError, PermissionError_) as exc:
        raise _map_error(exc) from exc
    return InviteOut(
        invite_id=invite.id,
        code=invite.code,
        role=invite.role,
        expires_at=invite.expires_at,
        workspace_id=invite.workspace_id,
    )


@router.post("/accept", response_model=WorkspaceOut, summary="接受邀请码加入")
async def accept_invite(
    payload: InviteAccept, current_user: CurrentUserDep, db: DbDep
) -> WorkspaceOut:
    """接受邀请码。

    席位满 → **409**（需要管理员扩容，不是改参数能解决的）；
    码无效/过期/已用 → **400**。
    """
    service = _service(db)
    try:
        member = await service.accept_invite(current_user.id, payload.code)
    except (WorkspaceError, InviteError) as exc:
        raise _map_error(exc) from exc
    workspace = await service.get_for_user(current_user.id, member.workspace_id)
    if workspace is None:  # pragma: no cover - 刚加入必然可见
        raise HTTPException(status_code=404, detail="工作区不存在")
    return await _to_out(service, current_user.id, workspace)
