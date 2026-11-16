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
    with_credentials: int = Field(description="活跃用户中有任意凭据的用户数")
    with_active_credentials: int = Field(description="活跃用户中有 active 凭据的用户数")
    byok_penetration: float = Field(description="byok_active / active")
    credential_penetration: float = Field(description="with_credentials / active，不会超过 1")


class ByokCredentials(BaseModel):
    total: int
    active: int
    invalid: int
    disabled: int


class ByokCost(BaseModel):
    """窗口内的成本估算（USD，公开标价，不是结算账单）。"""

    currency: str = "USD"
    byok: str = Field(description="用户自带模型的估算成本（用户自费）")
    platform: str = Field(description="平台模型的估算成本（平台承担）")
    total: str
    unpriced_byok_tokens: int = Field(description="BYOK 中未收录单价的 token，未计入金额")
    unpriced_platform_tokens: int
    split_estimated_tokens: int = Field(description="历史任务按比例拆分 token 的数量")


class ByokTrendPoint(BaseModel):
    date: str = Field(description="YYYY-MM-DD（UTC）")
    tasks: int
    tokens: int
    byok_tasks: int
    byok_tokens: int
    cost: str = Field(description="当天全部任务的估算成本（USD）")
    byok_cost: str = Field(description="当天 BYOK 任务的估算成本（USD）")


class ByokProviderRow(BaseModel):
    provider: str
    model: str
    tasks: int
    tokens: int
    cost: str | None = Field(description="BYOK 估算成本；null = 全部未收录单价")
    unpriced_tokens: int


class ByokTopUser(BaseModel):
    user_id: str
    username: str
    tasks: int
    tokens: int
    byok_tasks: int
    byok_tokens: int
    byok_token_share: float
    byok_cost: str | None = Field(description="BYOK 估算成本；null = 全部未收录单价")


class ByokDormantUser(BaseModel):
    """配了凭据但从未跑过 BYOK 任务的用户（全站历史口径）。"""

    user_id: str
    username: str
    credentials: int
    active_credentials: int
    latest_credential_at: datetime | None
    days_since_credential: int | None = Field(description="距最近一次配置凭据的天数")
    platform_tasks: int = Field(description="历史平台任务数（全部任务）")
    platform_tokens: int


class ByokOverviewOut(BaseModel):
    window_days: int
    period_start: datetime
    period_end: datetime
    totals: ByokTotals
    users: ByokUsers
    credentials: ByokCredentials
    cost: ByokCost
    trend: list[ByokTrendPoint]
    providers: list[ByokProviderRow]
    top_users: list[ByokTopUser]
    dormant_count: int = Field(description="配了凭据但从未使用 BYOK 的用户总数")
    dormant_users: list[ByokDormantUser]
