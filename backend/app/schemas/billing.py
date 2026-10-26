"""计费相关 Pydantic 契约（Phase 2 · 计费套餐）。

## 关键字段语义

| 字段 | 说明 |
| --- | --- |
| `monthly_reviews` | `-1` = 不限（机构版） |
| `reviews_remaining` | `null` = 不限；否则 `max(0, limit - used)` |
| `price_cents` | 分；0 = 免费。**前端除以 100 显示元** |

**为什么用「分」而不是「元」**：浮点金额会出现 `19.99 * 100 = 1998.9999` 这类
精度问题。整型分是支付行业标准做法。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PlanOut(BaseModel):
    """套餐（公开信息，不含任何用户数据）。"""

    model_config = ConfigDict(from_attributes=True)

    plan_id: str
    name: str
    monthly_reviews: int = Field(description="每月篇数；-1 = 不限")
    max_workspaces: int
    max_seats_per_workspace: int
    allow_api_access: bool
    allow_team: bool
    price_cents: int = Field(description="月费（分）；0 = 免费")
    description: str | None = None


class UsageOut(BaseModel):
    """我的用量 + **当前套餐权益**（2026-10-26 套餐页重设计扩充）。

    前端进度条直接用这三个字段，**不要自己算** —— 口径（自然月、UTC）
    在服务端定义，前端重算必然漂移。

    ## `period_key` 与 `period_end` 不是一回事（别混用）

    | 字段 | 含义 | 例子 |
    | --- | --- | --- |
    | `period_key` | **用量**周期（自然月，篇数按月重置） | `2026-10` |
    | `period_end` | **订阅**到期日（购买日 + N 天） | `2026-11-22` |

    只给 `period_key` 会让用户以为「10 月底才续费」，而订阅可能 11 月就到期。
    免费档没有订阅行 → `period_end` 为 `null`（不是编一个假日期）。
    """

    period_key: str = Field(description="用量周期 YYYY-MM（UTC 年月）")
    reviews_used: int
    monthly_reviews: int = Field(description="本月额度；-1 = 不限")
    reviews_remaining: int | None = Field(default=None, description="剩余篇数；null = 不限")
    plan_id: str
    plan_name: str

    # ===== 当前套餐权益（「当前套餐」卡直接渲染，前端不再自己拼第二份配置）=====
    allow_team: bool = Field(default=False, description="是否含团队协作")
    allow_api_access: bool = Field(default=False, description="是否含开放 API")
    max_workspaces: int = Field(default=0, description="可创建工作区上限")
    max_seats_per_workspace: int = Field(default=0, description="单工作区席位上限")
    price_cents: int = Field(default=0, description="月费（分）；0 = 免费")

    # ===== 订阅信息（与用量周期区分）=====
    period_end: datetime | None = Field(default=None, description="订阅到期时间；免费档为 null")
    seats_purchased: int | None = Field(default=None, description="已购席位数；免费档为 null")


class SubscribeRequest(BaseModel):
    """手工开通/变更套餐（仅管理员）。"""

    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID = Field(description="目标用户")
    plan_id: str = Field(min_length=1, max_length=32, description="套餐标识")
    note: str | None = Field(default=None, max_length=500, description="开通原因（对账依据）")
    days: int | None = Field(default=None, ge=1, le=3650, description="周期天数；默认 30")
    seats: int = Field(default=1, ge=1, le=1000, description="购买席位数")


class SubscriptionOut(BaseModel):
    """订阅信息。"""

    model_config = ConfigDict(from_attributes=True)

    subscription_id: uuid.UUID
    user_id: uuid.UUID
    plan_id: str
    status: str
    period_start: datetime
    period_end: datetime
    seats_purchased: int
    source: str = Field(description="manual / payment")
    note: str | None = None
