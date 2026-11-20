"""后台管理控制台契约（Issue #12）。

## 与用户侧契约的分工

- 用户侧 `/tasks` 只返回自己的任务，用 `X-Total-Count` 头传总数；
- 管理端要跨用户聚合，**总数直接进 body**（`total` + `items`）——
  管理页同时要看「筛出来多少条」和「全站多少条」，头部传参在这里反而累赘。

## 口径必须写进字段名

管理看板最容易骗人的是「分子分母不一致」：失败率的分母是**已结束**任务
（completed/failed/cancelled），不是全部任务（含在跑的）。所以字段名直接
带口径（`failed_in_window` / `finished_in_window`），前端提示文案与之对齐。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AdminUserStats(BaseModel):
    """用户维度（窗口内活跃 = 在窗口内提交过任务的去重用户数）。"""

    model_config = ConfigDict(extra="forbid")

    total: int
    new_in_window: int
    active_in_window: int


class AdminTaskStats(BaseModel):
    """任务维度。`by_status` 是**全站当前分布**（不限窗口）。"""

    model_config = ConfigDict(extra="forbid")

    total: int
    created_in_window: int
    by_status: dict[str, int] = Field(default_factory=dict)
    failed_in_window: int
    finished_in_window: int
    failure_rate: float = Field(ge=0.0, le=1.0)
    pending_action: int


class AdminOverviewOut(BaseModel):
    """后台首页 KPI（一次请求拿全，避免首屏 6 个并发）。"""

    model_config = ConfigDict(extra="forbid")

    days: int
    users: AdminUserStats
    tasks: AdminTaskStats
    tokens_used_in_window: int
    byok_tasks_in_window: int
    byok_ratio: float = Field(ge=0.0, le=1.0)
    active_credentials: int


class AdminTaskRow(BaseModel):
    """跨用户任务行（只读，管理端不提供代操作）。"""

    model_config = ConfigDict(extra="forbid")

    task_id: uuid.UUID
    title: str
    status: str
    owner_id: uuid.UUID
    owner_username: str
    is_byok: bool = False
    model_label: str | None = Field(default=None, description="模型快照的可读标签；平台任务为 None")
    used_tokens: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AdminTaskPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int
    items: list[AdminTaskRow] = Field(default_factory=list)


class AdminUserRow(BaseModel):
    """用户行：含套餐与用量计数（管理端最常问的三个数）。"""

    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID
    username: str
    email: str
    role: str
    is_active: bool
    created_at: datetime | None = None
    plan_name: str | None = None
    task_count: int = 0
    credential_count: int = 0


class AdminUserPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int
    items: list[AdminUserRow] = Field(default_factory=list)
