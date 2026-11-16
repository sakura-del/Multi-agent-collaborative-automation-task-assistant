"""管理员侧 BYOK 渗透率看板的数据契约（Issue #15 P2）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ByokTotals(BaseModel):
    tasks: int
    tokens: int
    byok_tasks: int
    byok_tokens: int
    byok_task_share: float = Field(description="BYOK 任务 / 全部任务，0~1")
    byok_token_share: float = Field(description="BYOK token / 全部 token，0~1")


class ByokUsers(BaseModel):
    active: int = Field(description="窗口内跑过任务的用户数")
    byok_active: int = Field(description="窗口内跑过 BYOK 任务的用户数")
    with_credentials: int = Field(description="有任意凭据的用户数（与窗口无关）")
    with_active_credentials: int = Field(description="有 active 凭据的用户数")
    byok_penetration: float = Field(description="byok_active / active")
    credential_penetration: float = Field(description="with_credentials / active")


class ByokCredentials(BaseModel):
    total: int
    active: int
    invalid: int
    disabled: int


class ByokTrendPoint(BaseModel):
    date: str = Field(description="YYYY-MM-DD（UTC）")
    tasks: int
    tokens: int
    byok_tasks: int
    byok_tokens: int


class ByokProviderRow(BaseModel):
    provider: str
    model: str
    tasks: int
    tokens: int


class ByokTopUser(BaseModel):
    user_id: str
    username: str
    tasks: int
    tokens: int
    byok_tasks: int
    byok_tokens: int
    byok_token_share: float


class ByokOverviewOut(BaseModel):
    window_days: int
    period_start: datetime
    period_end: datetime
    totals: ByokTotals
    users: ByokUsers
    credentials: ByokCredentials
    trend: list[ByokTrendPoint]
    providers: list[ByokProviderRow]
    top_users: list[ByokTopUser]
