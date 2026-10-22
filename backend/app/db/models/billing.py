"""PLAN / SUBSCRIPTION / USAGE_COUNTER：计费套餐（Phase 2 · 最后一项）。

## 核心设计：篇数为主，token 为辅

现有配额是「按 **UTC 日** 扣 token」，套餐是「按 **月** 篇数」。两者并存会
**双轨打架**（用户买了 100 篇/月，某天跑 3 篇却撞日 token 上限）。所以：

| 维度 | 角色 | 存储 | 面向用户 |
| --- | --- | --- | --- |
| **篇数（月）** | **准入控制（主）** | 数据库（`usage_counter`） | ✅ 是销售单位 |
| **token（日）** | **成本护栏（辅）** | Redis（既有 `QuotaService`） | ❌ 不宣传 |

**为什么篇数用数据库而 token 用 Redis**：

- 篇数是**收钱依据**，必须持久、可审计、可与 `task` 表对账。
  Redis 丢了就是「用户说 3 篇、系统说 5 篇」的纠纷；
- token 是**成本护栏**，精度要求低（分钟级滞后可接受），Redis 的原子计数更快。

## 为什么套餐是表而不是代码常量

定价会变、会有促销档、机构版要定制。写死在代码里每次改价都要发版，
且**历史订阅无法回溯「当时买的是什么」**。

`is_active=false` 表示下架，但**不删行** —— 删掉会让历史订阅的 `plan_id`
指向不存在的套餐。

## 为什么一个用户只能有一条 active 订阅

多条 active 会让「这个月还能跑几篇」无法判定。
升级 = 旧订阅 `cancelled` + 新订阅 `active`（同一事务，由服务层保证）。

数据库层用**部分唯一索引**（`WHERE status='active'`）兜底；
SQLite 不支持部分索引语法差异，服务层 + 测试保证。

## 自然月边界

`period_key` 是 `YYYY-MM`（**UTC** 年月）。选自然月而非订阅周期：
边界简单、可预测、与用户认知一致。订阅周期跨月时**不做按比例折算**
（10-15 订阅则 10 月享全月额度）—— 这是有意的简化，已写进 runbook。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

#: 无订阅用户回落的套餐（**不建订阅行**：避免统计被免费行污染）。
DEFAULT_PLAN_ID = "free"

#: 合法订阅状态。
SUBSCRIPTION_STATUSES: tuple[str, ...] = ("active", "expired", "cancelled")

#: 合法订阅来源（`payment` 为未来接支付网关预留）。
SUBSCRIPTION_SOURCES: tuple[str, ...] = ("manual", "payment")

#: 篇数「不限」的哨兵值（与 `quota.daily_token_limit=-1` 同风格）。
UNLIMITED_REVIEWS = -1


def period_key_for(moment: datetime | None = None) -> str:
    """返回 `YYYY-MM`（**UTC** 年月）。

    **必须按 UTC**：本地时区会让月初/月末边界不一致
    （UTC+8 的 11-01 07:30 在 UTC 还是 10-31，应算 10 月）。
    """
    current = moment or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(UTC).strftime("%Y-%m")


#: 四个套餐的种子数据（对齐路线图 §7「建议档位」）。
#:
#: | 套餐 | 篇数/月 | 团队 | API | 价格 |
#: | --- | --- | --- | --- | --- |
#: | free | 1 | ❌ | ❌ | ¥0 |
#: | student | 10 | ✅ | ❌ | ¥19 |
#: | pro | 50 | ✅ | ✅ | ¥69 |
#: | institution | 不限 | ✅ | ✅ | ¥1999 |
#:
#: **免费档必须严格**：宽松会让付费转化与成本控制同时失效。
#: **API 只给专业档及以上**：脚本刷任务的成本风险远高于网页。
SEED_PLANS: list[dict[str, Any]] = [
    {
        "id": "free",
        "name": "免费版",
        "monthly_reviews": 1,
        "max_workspaces": 0,
        "max_seats_per_workspace": 0,
        "allow_api_access": False,
        "allow_team": False,
        "price_cents": 0,
        "description": "每月 1 篇综述，含 GB/T 7714—2025 引用与 Markdown/DOCX 导出",
    },
    {
        "id": "student",
        "name": "学生版",
        "monthly_reviews": 10,
        "max_workspaces": 1,
        "max_seats_per_workspace": 3,
        "allow_api_access": False,
        "allow_team": True,
        "price_cents": 1900,
        "description": "每月 10 篇，支持团队协作与导师批注（3 席位）",
    },
    {
        "id": "pro",
        "name": "专业版",
        "monthly_reviews": 50,
        "max_workspaces": 5,
        "max_seats_per_workspace": 10,
        "allow_api_access": True,
        "allow_team": True,
        "price_cents": 6900,
        "description": "每月 50 篇，开放 API 与 MCP 接入（10 席位）",
    },
    {
        "id": "institution",
        "name": "机构版",
        "monthly_reviews": UNLIMITED_REVIEWS,
        "max_workspaces": 100,
        "max_seats_per_workspace": 200,
        "allow_api_access": True,
        "allow_team": True,
        "price_cents": 199_900,
        "description": "篇数不限，按席位计价，支持统一身份与审计",
    },
]


class Plan(Base):
    """套餐定义（**配置化**：改价不发版、历史订阅可回溯）。"""

    __tablename__ = "plan"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, comment="套餐标识（语义化字符串，如 free/pro）"
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False, comment="展示名")
    monthly_reviews: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="每月篇数；-1 = 不限"
    )
    max_workspaces: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="可创建工作区数"
    )
    max_seats_per_workspace: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="单工作区席位上限"
    )
    allow_api_access: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="是否可用开放 API / MCP"
    )
    allow_team: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="是否可用团队工作区"
    )
    price_cents: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="月费（分）；0 = 免费"
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="展示用说明")
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, comment="是否上架（下架不删行）"
    )


class Subscription(Base):
    """用户订阅（同一用户同时只有一条 `active`）。"""

    __tablename__ = "subscription"

    __table_args__ = (
        # 部分唯一索引由迁移创建（PostgreSQL `WHERE status='active'`）；
        # 这里声明普通索引以保持 ORM 与迁移一致
        UniqueConstraint("id", name="uq_subscription_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, comment="订阅ID")
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="订阅用户ID",
    )
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("plan.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
        comment="套餐ID（下架套餐仍可解析）",
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", comment="active/expired/cancelled"
    )
    period_start: Mapped[datetime] = mapped_column(nullable=False, comment="计费周期开始")
    period_end: Mapped[datetime] = mapped_column(nullable=False, comment="计费周期结束")
    seats_purchased: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, comment="购买席位数（个人版=1）"
    )
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="manual", comment="manual/payment"
    )
    note: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="手工开通记录（谁开的、为什么）"
    )


class UsageCounter(Base):
    """月度篇数计数（**计费依据**，必须持久可审计）。"""

    __tablename__ = "usage_counter"

    __table_args__ = (
        UniqueConstraint("user_id", "period_key", name="uq_usage_counter_user_period"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, comment="记录ID")
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="用户ID",
    )
    period_key: Mapped[str] = mapped_column(
        String(8), nullable=False, index=True, comment="周期 YYYY-MM（UTC 年月）"
    )
    reviews_used: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="本期已用篇数"
    )
