"""定时重跑 / 增量刷新 的 HTTP 契约（Issue #27）。

字段与 `app/db/models/routine.py` 一一对应；这里只做**出参形状**，不承载业务规则
（频率白名单、冷却、状态限制都在 `TaskExecution` 里，端点保持薄）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RoutineIn(BaseModel):
    """保存排期的入参。"""

    enabled: bool = Field(default=True, description="是否启用定时重跑")
    cadence: Literal["weekly", "monthly"] = Field(default="weekly", description="重跑频率")
    notify: Literal["none", "email"] = Field(default="email", description="汇报方式")


class RoutineOut(BaseModel):
    """排期出参。"""

    model_config = ConfigDict(from_attributes=True)

    task_id: UUID
    enabled: bool
    cadence: str
    notify: str
    next_run_at: datetime
    last_run_at: datetime | None = None
    memory_brief: dict[str, Any] | None = None


class RefreshOut(BaseModel):
    """一次刷新的运行记录。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    trigger: str
    status: str
    window_from: datetime | None = None
    window_to: datetime | None = None
    hits_total: int = 0
    new_count: int = 0
    duplicate_count: int = 0
    sources: dict = Field(default_factory=dict)
    summary_markdown: str | None = None
    citation_ok: bool = True
    tokens_used: int = 0
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime | None = None
