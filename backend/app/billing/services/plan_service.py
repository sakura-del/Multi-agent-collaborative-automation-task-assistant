"""套餐服务（Phase 2 · 计费套餐 Task 2）。

## 篇数扣减为什么必须是数据库原子操作

「先 `SELECT` 查已用，再 `UPDATE +1`」在并发下必然超发：两个请求同时读到 9/10，
都通过 → 11 篇。篇数是**收钱依据**，超发就是直接少收钱。

所以用一条 SQL 完成判定 + 扣减：

```sql
UPDATE usage_counter
   SET reviews_used = reviews_used + 1
 WHERE user_id = :uid AND period_key = :period
   AND (reviews_used < :limit OR :limit < 0)
```

`rowcount == 0` 说明超限（或行不存在，需先 upsert）。这样数据库行锁保证
同一用户同一月份的扣减是串行的。

## 为什么免费档不建订阅行

新用户注册就写一条订阅，会让「有多少付费用户」的统计被免费行污染，
且注册流程多一次写库。`get_active_plan` 在无 active 订阅时直接返回 `free` 套餐。

## 与 token 配额的分工（**必须记住**）

| 维度 | 谁管 | 触发时的错误码 |
| --- | --- | --- |
| 篇数（月） | **本服务**（数据库） | `PLAN_QUOTA_EXCEEDED` |
| token（日） | `QuotaService`（Redis） | `TOKEN_BUDGET_EXCEEDED` |

**顺序**：建任务时先查篇数（用户可理解的限制），再查 token（内部护栏）。
反过来的话用户会先看到「token 超限」这种他无法理解的错误。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, TypedDict

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.billing.model_prices import estimate_cost, lookup_price
from app.core.config import get_settings
from app.core.exceptions import AppException, ValidationException
from app.core.logging import get_logger
from app.db.models.billing import (
    DEFAULT_PLAN_ID,
    SEED_PLANS,
    UNLIMITED_REVIEWS,
    Plan,
    Subscription,
    UsageCounter,
    period_key_for,
)
from app.db.models.task import Task

logger = get_logger(__name__)


class _CostGroup(TypedDict):
    """成本估算的中间聚合（按 provider/model/来源分组）。"""

    tasks: int
    prompt_tokens: int
    completion_tokens: int
    estimated_tokens: int
    split_estimated: bool


#: 默认订阅周期（天）。
DEFAULT_PERIOD_DAYS = 30

#: 功能名 → (套餐字段, 中文标签)。
#:
#: **契约**：调用方用功能名（`team` / `api`），不直接摸 Plan 字段。
#: 中文标签必须进错误消息 —— 「当前套餐不支持该功能」没说是什么功能，
#: 用户无法判断该不该升级。
_FEATURE_FIELDS: dict[str, tuple[str, str]] = {
    "team": ("allow_team", "团队协作"),
    "api": ("allow_api_access", "开放 API"),
}


class PlanQuotaExceeded(AppException):
    """本月篇数用尽（HTTP 429）。

    **错误码与 token 护栏区分**（`TOKEN_BUDGET_EXCEEDED`）：前者用户可以通过
    升级套餐或等下月解决；后者是内部成本护栏，用户无法自行解决。
    混用会让用户误以为买套餐能解除 token 限制。
    """

    status_code = 429
    default_message = "本月综述篇数已用完"
    error_code = "PLAN_QUOTA_EXCEEDED"

    def __init__(
        self,
        message: str | None = None,
        *,
        used: int = 0,
        limit: int = 0,
        plan_id: str = DEFAULT_PLAN_ID,
    ) -> None:
        self.used = int(used)
        self.limit = int(limit)
        self.plan_id = plan_id
        super().__init__(
            message or f"本月篇数已用完（{self.used}/{self.limit}），可升级套餐或等下一周期",
            details={
                "used": self.used,
                "limit": self.limit,
                "plan_id": plan_id,
                "hint": "升级套餐或等下一自然月重置",
            },
        )


class FeatureNotAvailable(AppException):
    """当前套餐不含该功能（HTTP 403）。"""

    status_code = 403
    default_message = "当前套餐不支持该功能"
    error_code = "PLAN_FEATURE_UNAVAILABLE"

    def __init__(
        self,
        message: str | None = None,
        *,
        feature: str = "",
        plan_id: str = DEFAULT_PLAN_ID,
        label: str = "",
    ) -> None:
        self.feature = feature
        self.plan_id = plan_id
        self.label = label or feature
        super().__init__(
            message or f"当前套餐（{plan_id}）不支持「{self.label}」，请升级套餐",
            details={"feature": feature, "plan_id": plan_id},
        )


class WorkspaceLimitExceeded(AppException):
    """工作区数量已达套餐上限（HTTP 403）。

    **与 `FeatureNotAvailable` 区分**：前者是「完全没有团队功能」（免费档），
    后者是「有团队功能但数量用完了」（学生档建第 2 个）。
    错误码分开，前端才能给出不同引导（升级 vs 删旧建新）。
    """

    status_code = 403
    default_message = "工作区数量已达套餐上限"
    error_code = "PLAN_WORKSPACE_LIMIT"

    def __init__(
        self,
        message: str | None = None,
        *,
        limit: int = 0,
        current: int = 0,
        plan_id: str = DEFAULT_PLAN_ID,
    ) -> None:
        self.limit = int(limit)
        self.current = int(current)
        self.plan_id = plan_id
        super().__init__(
            message or f"当前套餐（{plan_id}）最多 {self.limit} 个工作区，已用 {self.current}",
            details={"limit": self.limit, "current": self.current, "plan_id": plan_id},
        )


class SeatLimitExceeded(AppException):
    """席位数超过套餐上限（HTTP 403）。

    **不静默截断**：用户请求 10 席而套餐只允许 3 席时，若静默建成 3 席，
    用户会以为买到了 10 席。必须显式拒绝并告知真实上限。
    """

    status_code = 403
    default_message = "席位数超过套餐上限"
    error_code = "PLAN_SEAT_LIMIT"

    def __init__(
        self,
        message: str | None = None,
        *,
        requested: int = 0,
        limit: int = 0,
        plan_id: str = DEFAULT_PLAN_ID,
    ) -> None:
        self.requested = int(requested)
        self.limit = int(limit)
        self.plan_id = plan_id
        super().__init__(
            message or f"当前套餐（{plan_id}）席位上限 {self.limit}，请求 {self.requested}",
            details={"requested": self.requested, "limit": self.limit, "plan_id": plan_id},
        )


def _in_memory_free_plan() -> Plan:
    """构造内存版 `free` 套餐（**不落库**）。

    仅用于「`plan` 表没有种子」的降级路径 —— 保证核心业务（建任务）不因
    计费配置缺失而 500。返回的是**未 attach 到 session 的瞬态对象**，
    调用方只读字段，不要修改。
    """
    spec = next(plan for plan in SEED_PLANS if plan["id"] == DEFAULT_PLAN_ID)
    return Plan(**spec, is_active=True)


class PlanService:
    """套餐、订阅与月度用量。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    # ===== 套餐与订阅 =====

    async def get_active_plan(self, user_id: uuid.UUID, *, moment: datetime | None = None) -> Plan:
        """取当前生效套餐；无 active 订阅时回落到 `free`。

        **不建订阅行**（见模块 docstring）。

        ## `free` 套餐缺失时返回内存默认值，不抛异常

        正常情况 `free` 由迁移种子写入。但以下场景会缺失：
        - 测试用 `Base.metadata.create_all` 建表（**不走迁移，没有种子**）；
        - 有人手工删了 `plan` 表的行。

        抛 `RuntimeError` 会让**所有建任务请求 500** —— 计费配置问题不该
        拖垮核心业务。所以降级为内存默认值 + warning，保证业务可用
        （篇数限制仍生效，只是从数据库读不到配置）。
        """
        now = moment or datetime.now(UTC)
        async with self._session_factory() as session:
            subscription = await self._active_subscription(session, user_id, now)
            plan_id = subscription.plan_id if subscription is not None else DEFAULT_PLAN_ID
            plan = await session.get(Plan, plan_id)
            if plan is not None:
                return plan
            # 套餐被硬删（不该发生）→ 回落 free，避免整个请求 500
            fallback = await session.get(Plan, DEFAULT_PLAN_ID)
            if fallback is not None:
                logger.warning("plan_missing_fallback", user_id=str(user_id), missing_plan=plan_id)
                return fallback

        logger.warning(
            "plan_table_empty_fallback",
            user_id=str(user_id),
            hint="plan 表缺少种子数据（是否没跑迁移？）",
        )
        return _in_memory_free_plan()

    @staticmethod
    def max_documents_for_plan(plan_id: str) -> int:
        """各套餐使用平台模型时单次任务文献数上限（Issue #61 P0-3 控成本）。"""
        if plan_id == DEFAULT_PLAN_ID:
            return int(get_settings().billing.free_max_documents)
        return 50

    async def check_document_limit(self, user_id: uuid.UUID, max_documents: int) -> None:
        """校验单次任务的文献数是否超出当前套餐的平台模型上限。"""
        plan = await self.get_active_plan(user_id)
        limit = self.max_documents_for_plan(plan.id)
        if max_documents > limit:
            raise ValidationException(
                f"{plan.name}使用平台模型单次最多支持 {limit} 篇文献（当前选择 {max_documents} 篇）；"
                "请将篇数调至 15 篇以内，或使用自带模型 / 升级套餐",
                details={
                    "plan_id": plan.id,
                    "limit": limit,
                    "requested": max_documents,
                },
            )

    async def _active_subscription(
        self, session: AsyncSession, user_id: uuid.UUID, now: datetime
    ) -> Subscription | None:
        """取 active 且未过期的订阅。"""
        subscription = await session.scalar(
            select(Subscription).where(
                Subscription.user_id == user_id,
                Subscription.status == "active",
            )
        )
        if subscription is None:
            return None
        period_end = subscription.period_end
        if period_end.tzinfo is None:
            period_end = period_end.replace(tzinfo=UTC)
        if period_end <= now:
            return None
        return subscription

    async def subscribe(
        self,
        user_id: uuid.UUID,
        plan_id: str,
        *,
        note: str | None = None,
        days: int = DEFAULT_PERIOD_DAYS,
        seats: int = 1,
        source: str = "manual",
    ) -> Subscription:
        """开通/变更套餐。

        **同一事务内**：把现有 active 订阅置 `cancelled`，再插入新的 `active`。
        保证「一个用户同时只有一条 active」（否则「本月还能跑几篇」无法判定）。
        """
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            plan = await session.get(Plan, plan_id)
            if plan is None:
                raise ValueError(f"套餐不存在：{plan_id}")
            if not plan.is_active:
                raise ValueError(f"套餐已下架：{plan_id}")

            # 取消现有 active（幂等：没有就跳过）
            await session.execute(
                update(Subscription)
                .where(
                    Subscription.user_id == user_id,
                    Subscription.status == "active",
                )
                .values(status="cancelled")
            )

            subscription = Subscription(
                user_id=user_id,
                plan_id=plan_id,
                status="active",
                period_start=now,
                period_end=now + timedelta(days=max(1, int(days))),
                seats_purchased=max(1, int(seats)),
                source=source,
                note=note,
            )
            session.add(subscription)
            try:
                await session.commit()
            except IntegrityError as exc:  # pragma: no cover - 部分唯一索引兜底
                await session.rollback()
                raise ValueError("订阅状态冲突，请重试") from exc
            await session.refresh(subscription)
            logger.info(
                "subscription_created",
                user_id=str(user_id),
                plan_id=plan_id,
                source=source,
                period_end=subscription.period_end.isoformat(),
            )
            return subscription

    async def cancel(self, user_id: uuid.UUID) -> None:
        """取消订阅（回落免费档）。"""
        async with self._session_factory() as session:
            await session.execute(
                update(Subscription)
                .where(
                    Subscription.user_id == user_id,
                    Subscription.status == "active",
                )
                .values(status="cancelled")
            )
            await session.commit()
            logger.info("subscription_cancelled", user_id=str(user_id))

    # ===== 用量 =====

    async def get_usage(
        self, user_id: uuid.UUID, *, moment: datetime | None = None
    ) -> dict[str, object]:
        """当前周期的篇数用量 + **当前套餐权益**（2026-10-26 套餐页重设计新增）。

        `reviews_remaining` 为 `None` 表示不限篇数（机构版）。

        ## 为什么把套餐权益也塞进用量载荷

        套餐页的「当前套餐」卡原先只显示 `机构版 / 2026-10 / 3 篇`，
        **三项权益一条都没有**，而下面「可选套餐」卡每项都列了。
        用户要判断「该不该升级」得自己拿名字去四张卡里对照找 —— 页面上没有答案。

        这些字段 `Plan` 表里本来就有，只是没返回。**不让前端自己猜**：
        前端猜就等于第二份套餐配置，改价/改权益时必然与后端漂移。

        ## `period_end` 与 `period_key` 是两个不同的东西

        - `period_key`（`2026-10`）= **用量**周期，自然月，用于篇数重置；
        - `period_end` = **订阅**到期日（购买日 + N 天）。

        实测缺陷：页面只显示 `2026-10`，用户会以为「10 月底才需要续费」，
        而实际订阅可能是 11 月 22 日到期。免费档没有订阅行 → `period_end` 为 `None`
        （不是编一个假日期）。
        """
        now = moment or datetime.now(UTC)
        period_key = period_key_for(now)
        plan = await self.get_active_plan(user_id, moment=now)
        async with self._session_factory() as session:
            used = await session.scalar(
                select(UsageCounter.reviews_used).where(
                    UsageCounter.user_id == user_id,
                    UsageCounter.period_key == period_key,
                )
            )
            subscription = await self._active_subscription(session, user_id, now)
            model_usage = await self._model_usage(session, user_id, period_key)
        used_count = int(used or 0)
        limit = plan.monthly_reviews
        remaining = None if limit == UNLIMITED_REVIEWS else max(0, limit - used_count)
        return {
            "period_key": period_key,
            "reviews_used": used_count,
            "monthly_reviews": limit,
            "reviews_remaining": remaining,
            "plan_id": plan.id,
            "plan_name": plan.name,
            # ===== 当前套餐权益（前端「当前套餐」卡直接渲染，不再自己拼）=====
            "allow_team": bool(plan.allow_team),
            "allow_api_access": bool(plan.allow_api_access),
            "max_workspaces": int(plan.max_workspaces),
            "max_seats_per_workspace": int(plan.max_seats_per_workspace),
            "price_cents": int(plan.price_cents),
            "max_documents_per_task": self.max_documents_for_plan(plan.id),
            # ===== 订阅信息（与用量周期区分开）=====
            "period_end": subscription.period_end if subscription is not None else None,
            "seats_purchased": (
                int(subscription.seats_purchased) if subscription is not None else None
            ),
            # ===== 模型用量分账（Issue #15 P2）=====
            "model_usage": model_usage,
        }

    @staticmethod
    def _period_bounds(period_key: str) -> tuple[datetime, datetime]:
        """`YYYY-MM` → [月初, 下月初) 的 UTC 区间。"""
        year, month = (int(part) for part in period_key.split("-", 1))
        start = datetime(year, month, 1, tzinfo=UTC)
        end = (
            datetime(year + 1, 1, 1, tzinfo=UTC)
            if month == 12
            else datetime(year, month + 1, 1, tzinfo=UTC)
        )
        return start, end

    async def _model_usage(
        self, session: AsyncSession, user_id: uuid.UUID, period_key: str
    ) -> dict[str, object]:
        """按 `task.is_byok` 分组的 token / 任务数（当月，UTC）。

        `is_byok` 是创建时写死的标记：凭据删除后历史任务仍算 BYOK，
        不会被算成平台成本。
        """
        start, end = self._period_bounds(period_key)
        rows = (
            await session.execute(
                select(
                    Task.is_byok,
                    func.count(Task.id),
                    func.coalesce(func.sum(Task.used_tokens), 0),
                )
                .where(
                    Task.user_id == user_id,
                    Task.created_at >= start,
                    Task.created_at < end,
                )
                .group_by(Task.is_byok)
            )
        ).all()

        buckets = {
            "platform": {"tasks": 0, "tokens": 0},
            "byok": {"tasks": 0, "tokens": 0},
        }
        for is_byok, tasks, tokens in rows:
            key = "byok" if is_byok else "platform"
            buckets[key] = {"tasks": int(tasks or 0), "tokens": int(tokens or 0)}

        return {
            "period_key": period_key,
            "platform": buckets["platform"],
            "byok": buckets["byok"],
            "total": {
                "tasks": buckets["platform"]["tasks"] + buckets["byok"]["tasks"],
                "tokens": buckets["platform"]["tokens"] + buckets["byok"]["tokens"],
            },
        }

    async def model_usage_export(
        self, user_id: uuid.UUID, *, period_key: str | None = None
    ) -> dict[str, object]:
        """账单导出：汇总 + 逐任务明细（当月，UTC 自然月）。

        汇总由明细行现算，**不另跑一次聚合查询**：两套口径迟早会漂移，
        导出文件里的合计必须等于明细之和。
        """
        key = period_key or period_key_for(datetime.now(UTC))
        start, end = self._period_bounds(key)

        async with self._session_factory() as session:
            tasks = (
                (
                    await session.execute(
                        select(Task)
                        .where(
                            Task.user_id == user_id,
                            Task.created_at >= start,
                            Task.created_at < end,
                        )
                        .order_by(Task.created_at.asc(), Task.id.asc())
                    )
                )
                .scalars()
                .all()
            )

        buckets = {
            "platform": {"tasks": 0, "tokens": 0},
            "byok": {"tasks": 0, "tokens": 0},
        }
        rows: list[dict[str, object]] = []
        for task in tasks:
            snapshot = task.model_snapshot if isinstance(task.model_snapshot, dict) else {}
            source = "byok" if task.is_byok else "platform"
            tokens = int(task.used_tokens or 0)
            buckets[source]["tasks"] += 1
            buckets[source]["tokens"] += tokens
            rows.append(
                {
                    "task_id": str(task.id),
                    "created_at": task.created_at,
                    "title": task.title,
                    "status": task.status,
                    "is_byok": bool(task.is_byok),
                    "provider": str(snapshot.get("provider") or ""),
                    "model": str(snapshot.get("model") or ""),
                    "credential_name": str(snapshot.get("name") or ""),
                    "tokens": tokens,
                }
            )

        return {
            "period_key": key,
            "platform": buckets["platform"],
            "byok": buckets["byok"],
            "total": {
                "tasks": buckets["platform"]["tasks"] + buckets["byok"]["tasks"],
                "tokens": buckets["platform"]["tokens"] + buckets["byok"]["tokens"],
            },
            "rows": rows,
        }

    async def model_cost_estimate(
        self, user_id: uuid.UUID, *, period_key: str | None = None
    ) -> dict[str, object]:
        """按模型单价估算当月成本（USD）。

        ## 口径与假设

        - 平台任务没有模型快照（`model_snapshot` 只在 BYOK 时写），
          按 `settings.llm.default_model` 估算，并标记 `model_source=platform_default`；
          实际 fallback 到别的模型时会有偏差，这是**估算**不是账单。
        - 历史任务没有输入/输出拆分（2026-11-16 前只存总量），
          按 `billing.cost_output_ratio` 拆分并标记 `split_estimated=true`。
        - 未收录单价的模型不按 0 计：`unpriced_tokens` 单独返回，前端必须提示。
        """
        key = period_key or period_key_for(datetime.now(UTC))
        start, end = self._period_bounds(key)
        settings = get_settings()
        ratio = Decimal(str(settings.billing.cost_output_ratio))

        async with self._session_factory() as session:
            tasks = (
                (
                    await session.execute(
                        select(Task)
                        .where(
                            Task.user_id == user_id,
                            Task.created_at >= start,
                            Task.created_at < end,
                        )
                        .order_by(Task.created_at.asc(), Task.id.asc())
                    )
                )
                .scalars()
                .all()
            )

        groups: dict[tuple[str, bool, str, str], _CostGroup] = {}
        for task in tasks:
            snapshot = task.model_snapshot if isinstance(task.model_snapshot, dict) else {}
            if task.is_byok:
                provider = str(snapshot.get("provider") or "unknown")
                model = str(snapshot.get("model") or "")
                source = "task_snapshot"
            else:
                provider = settings.llm.default_provider
                model = settings.llm.default_model
                source = "platform_default"

            prompt = int(task.prompt_tokens or 0)
            completion = int(task.completion_tokens or 0)
            split_estimated = False
            if prompt + completion <= 0:
                total = int(task.used_tokens or 0)
                if total > 0:
                    completion = int(Decimal(total) * ratio)
                    prompt = total - completion
                    split_estimated = True

            group_key = (provider, bool(task.is_byok), model, source)
            group = groups.setdefault(
                group_key,
                {
                    "tasks": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "estimated_tokens": 0,
                    "split_estimated": False,
                },
            )
            group["tasks"] += 1
            group["prompt_tokens"] += prompt
            group["completion_tokens"] += completion
            group["split_estimated"] = group["split_estimated"] or split_estimated
            if split_estimated:
                # 只累计**这条任务**的估算 token；不能用整组 tokens，
                # 否则同模型下的精确任务也会被算进「估算」口径。
                group["estimated_tokens"] += prompt + completion

        rows: list[dict[str, Any]] = []
        total_cost = Decimal("0")
        unpriced_tokens = 0
        split_estimated_tokens = 0
        for (provider, is_byok, model, source), group in groups.items():
            prompt = group["prompt_tokens"]
            completion = group["completion_tokens"]
            tokens = prompt + completion
            price = lookup_price(model)
            cost = estimate_cost(model, prompt, completion)
            input_cost = estimate_cost(model, prompt, 0)
            output_cost = estimate_cost(model, 0, completion)
            if cost is None:
                unpriced_tokens += tokens
            else:
                total_cost += cost
            if group["split_estimated"]:
                split_estimated_tokens += group["estimated_tokens"]
            rows.append(
                {
                    "provider": provider,
                    "model": model,
                    "is_byok": is_byok,
                    "model_source": source,
                    "tasks": group["tasks"],
                    "prompt_tokens": prompt,
                    "completion_tokens": completion,
                    "total_tokens": tokens,
                    "input_cost": self._money(input_cost),
                    "output_cost": self._money(output_cost),
                    "total_cost": self._money(cost),
                    "input_price_per_mtok": (
                        str(price.input_per_mtok) if price is not None else None
                    ),
                    "output_price_per_mtok": (
                        str(price.output_per_mtok) if price is not None else None
                    ),
                    "priced": price is not None,
                    "split_estimated": bool(group["split_estimated"]),
                }
            )

        rows.sort(
            key=lambda row: (
                not bool(row["priced"]),
                -float(row["total_cost"] or 0),
                -int(row["total_tokens"]),
            )
        )
        return {
            "period_key": key,
            "currency": "USD",
            "total_cost": self._money(total_cost),
            "unpriced_tokens": unpriced_tokens,
            "split_estimated_tokens": split_estimated_tokens,
            "models": rows,
        }

    @staticmethod
    def _money(value: Decimal | None) -> str | None:
        """金额统一 6 位小数（USD）；`None` 表示未收录单价，不是 0。"""
        if value is None:
            return None
        return f"{value:.6f}"

    async def check_and_consume_review(
        self, user_id: uuid.UUID, *, moment: datetime | None = None
    ) -> None:
        """扣减一篇；超限抛 `PlanQuotaExceeded`（**拒绝时不扣减**）。

        实现：一条 `UPDATE ... WHERE reviews_used < limit`，用 `rowcount` 判定。
        行不存在时先插入（`reviews_used=0`）再重试一次 —— 只在「本月第一篇」发生。
        """
        now = moment or datetime.now(UTC)
        period_key = period_key_for(now)
        plan = await self.get_active_plan(user_id, moment=now)
        limit = plan.monthly_reviews

        # 统一走「先尝试 UPDATE，受影响 0 行则 upsert 后重试」——
        # 不限篇数也必须走这条路：否则「本月第一篇」时行还不存在，
        # UPDATE 影响 0 行、计数永远停在 0（首版就是这样，30 次调用后仍是 0）。
        affected = await self._increment(user_id, period_key, limit=limit)
        if affected > 0:
            return

        # 可能是「本月还没有行」→ upsert 后重试一次
        await self._ensure_row(user_id, period_key)
        affected = await self._increment(user_id, period_key, limit=limit)
        if affected > 0:
            return

        # 不限篇数时理论上不该到这里（limit<0 的 UPDATE 无上限条件）
        if limit == UNLIMITED_REVIEWS:  # pragma: no cover - 防御性兜底
            logger.warning("unlimited_increment_failed", user_id=str(user_id))
            return

        # 确实超限：读出当前用量给错误消息
        async with self._session_factory() as session:
            used = await session.scalar(
                select(UsageCounter.reviews_used).where(
                    UsageCounter.user_id == user_id,
                    UsageCounter.period_key == period_key,
                )
            )
        raise PlanQuotaExceeded(used=int(used or 0), limit=limit, plan_id=plan.id)

    async def _increment(self, user_id: uuid.UUID, period_key: str, *, limit: int) -> int:
        """原子 +1（带上限判定）；返回受影响行数。"""
        async with self._session_factory() as session:
            condition = UsageCounter.reviews_used < limit if limit >= 0 else None
            stmt = (
                update(UsageCounter)
                .where(
                    UsageCounter.user_id == user_id,
                    UsageCounter.period_key == period_key,
                )
                .values(reviews_used=UsageCounter.reviews_used + 1)
            )
            if condition is not None:
                stmt = stmt.where(condition)
            result = await session.execute(stmt)
            await session.commit()
            # `update()` 的 execute 返回 `CursorResult`（有 `rowcount`），但 mypy
            # 把它推成 `Result[Any]`。用 getattr 读，避免为类型标注引入额外 import
            # 与 `type: ignore`（后者会掩盖真正的类型错误）。
            return int(getattr(result, "rowcount", 0) or 0)

    async def _ensure_row(self, user_id: uuid.UUID, period_key: str) -> None:
        """确保本月计数行存在（并发下可能已被别人插入 → 忽略冲突）。"""
        async with self._session_factory() as session:
            session.add(UsageCounter(user_id=user_id, period_key=period_key, reviews_used=0))
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()  # 别人先插入了，正常

    # ===== 功能门禁 =====

    async def require_feature(
        self, user_id: uuid.UUID, feature: str, *, moment: datetime | None = None
    ) -> None:
        """校验套餐是否含某功能；不含抛 `FeatureNotAvailable`（403）。"""
        entry = _FEATURE_FIELDS.get(feature)
        if entry is None:
            raise ValueError(f"未知功能：{feature}（可选：{', '.join(_FEATURE_FIELDS)}）")
        field, label = entry
        plan = await self.get_active_plan(user_id, moment=moment)
        if not bool(getattr(plan, field)):
            raise FeatureNotAvailable(feature=feature, plan_id=plan.id, label=label)

    async def can_create_workspace(
        self, user_id: uuid.UUID, *, current_count: int, moment: datetime | None = None
    ) -> bool:
        """能否再建一个工作区（受 `max_workspaces` 限制）。"""
        plan = await self.get_active_plan(user_id, moment=moment)
        return int(current_count) < int(plan.max_workspaces)

    async def max_seats_for(self, user_id: uuid.UUID, *, moment: datetime | None = None) -> int:
        """单工作区席位上上限（由套餐决定）。"""
        plan = await self.get_active_plan(user_id, moment=moment)
        return int(plan.max_seats_per_workspace)

    async def workspace_limit_for(
        self, user_id: uuid.UUID, *, moment: datetime | None = None
    ) -> int:
        """可创建工作区数上限。"""
        plan = await self.get_active_plan(user_id, moment=moment)
        return int(plan.max_workspaces)

    # ===== 套餐列表（公开） =====

    async def list_active_plans(self) -> list[Plan]:
        """上架套餐列表（按价格升序，前端直接展示）。"""
        async with self._session_factory() as session:
            rows = await session.execute(
                select(Plan)
                .where(Plan.is_active.is_(True))
                .order_by(Plan.price_cents.asc(), Plan.id.asc())
            )
            return list(rows.scalars().all())

    async def count_active_subscriptions(self) -> int:
        """活跃付费订阅数（运营指标；免费档不建行所以这里就是付费用户数）。"""
        async with self._session_factory() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(Subscription)
                .where(Subscription.status == "active")
            )
            return int(count or 0)
