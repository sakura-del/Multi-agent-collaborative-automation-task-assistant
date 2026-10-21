"""工作区相关 Pydantic 契约（Phase 2 · 团队工作区）。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

WorkspaceRole = Literal["owner", "admin", "member", "viewer"]


class WorkspaceCreate(BaseModel):
    """创建工作区。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128, description="团队名")
    slug: str = Field(
        min_length=2,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9-]*[a-z0-9]$",
        description="URL 友好标识（小写字母/数字/连字符）",
    )
    seat_limit: int = Field(default=5, ge=1, le=500, description="席位上限")


class WorkspaceRename(BaseModel):
    """改名。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)


class WorkspaceOut(BaseModel):
    """工作区响应。"""

    model_config = ConfigDict(from_attributes=True)

    workspace_id: uuid.UUID
    name: str
    slug: str
    owner_id: uuid.UUID
    seat_limit: int
    seat_used: int = 0
    my_role: str | None = None
    created_at: datetime


class MemberOut(BaseModel):
    """成员响应（= 一个席位）。"""

    user_id: uuid.UUID
    username: str
    email: str
    role: str
    joined_at: datetime


class InviteCreate(BaseModel):
    """生成邀请码。"""

    model_config = ConfigDict(extra="forbid")

    role: Literal["admin", "member", "viewer"] = Field(
        default="member", description="受邀者加入后的角色（不能授予 owner）"
    )


class InviteOut(BaseModel):
    """邀请码响应。

    `code` 只在**创建时**返回一次（列表接口不返回）：邀请码等同临时凭证，
    能重复读取会让「一次性」形同虚设。
    """

    invite_id: uuid.UUID
    code: str
    role: str
    expires_at: datetime
    workspace_id: uuid.UUID


class InviteAccept(BaseModel):
    """接受邀请。"""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=8, max_length=64, description="邀请码")


class RoleChange(BaseModel):
    """改成员角色。"""

    model_config = ConfigDict(extra="forbid")

    role: Literal["admin", "member", "viewer"] = Field(description="新角色（不能设为 owner）")
