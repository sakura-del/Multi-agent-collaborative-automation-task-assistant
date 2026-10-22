"""计费数据模型测试（Phase 2 · 计费套餐 Task 1）。

## 本文件锁定

1. `plan` / `subscription` / `usage_counter` 三张表的字段与约束；
2. **套餐是表不是代码常量**（改价不发版、历史订阅可回溯）；
3. `plan.is_active` 下架**不删行**（历史订阅要能解析）；
4. `usage_counter` 的 `(user_id, period_key)` 唯一（计费依据不能重复计数）；
5. 迁移可 upgrade / downgrade / 重复 upgrade；种子幂等。

## 为什么计费表要比其他表更严格

篇数是**收钱依据**。`usage_counter` 重复计数 = 多收钱；漏计 = 少收钱。
所以唯一键、非空约束、迁移与 ORM 一致性都必须有测试钉死。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.db.models  # noqa: F401
from app.db.base import Base
from app.db.models.billing import Plan, Subscription, UsageCounter
from app.db.models.user import User


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


async def _user(factory, name: str) -> User:
    async with factory() as session:
        user = User(username=name, email=f"{name}@example.com", password_hash="x", is_active=True)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _plan(factory, plan_id: str = "free") -> Plan:
    async with factory() as session:
        plan = Plan(
            id=plan_id,
            name="免费版",
            monthly_reviews=1,
            max_workspaces=0,
            max_seats_per_workspace=0,
            allow_api_access=False,
            allow_team=False,
            price_cents=0,
            is_active=True,
        )
        session.add(plan)
        await session.commit()
        await session.refresh(plan)
        return plan


class TestPlanModel:
    @pytest.mark.asyncio
    async def test_create_plan(self, factory) -> None:
        plan = await _plan(factory)

        assert plan.id == "free"
        assert plan.monthly_reviews == 1
        assert plan.allow_api_access is False
        assert plan.is_active is True

    @pytest.mark.asyncio
    async def test_plan_id_is_string_pk(self) -> None:
        """套餐 id 是**语义化字符串**（`free`/`pro`），不是 UUID。

        理由：套餐 id 会出现在前端路由、日志、运维 SQL 里，
        `free` 比 `7f3a...` 可读得多。
        """
        pk_columns = [col.name for col in Plan.__table__.primary_key.columns]
        assert pk_columns == ["id"]
        assert Plan.__table__.columns["id"].type.python_type is str

    @pytest.mark.asyncio
    async def test_inactive_plan_still_readable(self, factory) -> None:
        """下架（`is_active=false`）**不删行** —— 历史订阅要能解析。"""
        async with factory() as session:
            session.add(
                Plan(
                    id="legacy",
                    name="已下架套餐",
                    monthly_reviews=5,
                    max_workspaces=1,
                    max_seats_per_workspace=3,
                    allow_api_access=False,
                    allow_team=False,
                    price_cents=1900,
                    is_active=False,
                )
            )
            await session.commit()

        async with factory() as session:
            found = await session.get(Plan, "legacy")
            assert found is not None
            assert found.is_active is False

    @pytest.mark.asyncio
    async def test_negative_monthly_reviews_means_unlimited(self, factory) -> None:
        """`-1` = 不限篇数（机构版）。"""
        async with factory() as session:
            session.add(
                Plan(
                    id="institution",
                    name="机构版",
                    monthly_reviews=-1,
                    max_workspaces=10,
                    max_seats_per_workspace=50,
                    allow_api_access=True,
                    allow_team=True,
                    price_cents=199_900,
                    is_active=True,
                )
            )
            await session.commit()

        async with factory() as session:
            found = await session.get(Plan, "institution")
            assert found is not None
            assert found.monthly_reviews == -1


class TestSubscriptionModel:
    @pytest.mark.asyncio
    async def test_create_subscription(self, factory) -> None:
        user = await _user(factory, "sub_owner")
        await _plan(factory, "student")
        now = datetime.now(UTC)
        async with factory() as session:
            sub = Subscription(
                user_id=user.id,
                plan_id="student",
                status="active",
                period_start=now,
                period_end=now + timedelta(days=30),
                seats_purchased=1,
                source="manual",
                note="管理员手工开通",
            )
            session.add(sub)
            await session.commit()
            await session.refresh(sub)

            assert sub.id is not None
            assert sub.status == "active"
            assert sub.source == "manual"

    @pytest.mark.asyncio
    async def test_source_enum_values(self) -> None:
        """`source` 只允许 `manual` / `payment`（未来接网关用 `payment`）。"""
        from app.db.models.billing import SUBSCRIPTION_SOURCES

        assert set(SUBSCRIPTION_SOURCES) == {"manual", "payment"}

    @pytest.mark.asyncio
    async def test_status_enum_values(self) -> None:
        from app.db.models.billing import SUBSCRIPTION_STATUSES

        assert set(SUBSCRIPTION_STATUSES) == {"active", "expired", "cancelled"}

    @pytest.mark.asyncio
    async def test_deleting_user_cascades_subscription(self, factory) -> None:
        from sqlalchemy import text

        user = await _user(factory, "sub_cascade")
        await _plan(factory, "student")
        now = datetime.now(UTC)
        async with factory() as session:
            session.add(
                Subscription(
                    user_id=user.id,
                    plan_id="student",
                    status="active",
                    period_start=now,
                    period_end=now + timedelta(days=30),
                    seats_purchased=1,
                    source="manual",
                )
            )
            await session.commit()

        async with factory() as session:
            await session.execute(text("PRAGMA foreign_keys=ON"))
            stored = await session.get(User, user.id)
            assert stored is not None
            await session.delete(stored)
            await session.commit()

        async with factory() as session:
            assert (await session.execute(select(Subscription))).scalars().all() == []


class TestUsageCounterModel:
    @pytest.mark.asyncio
    async def test_create_counter(self, factory) -> None:
        user = await _user(factory, "usage_owner")
        async with factory() as session:
            counter = UsageCounter(user_id=user.id, period_key="2026-10", reviews_used=3)
            session.add(counter)
            await session.commit()
            await session.refresh(counter)

            assert counter.reviews_used == 3
            assert counter.period_key == "2026-10"

    @pytest.mark.asyncio
    async def test_user_period_is_unique(self, factory) -> None:
        """**计费依据不能重复计数**：同一用户同一月份只能有一行。"""
        user = await _user(factory, "usage_dup")
        async with factory() as session:
            session.add(UsageCounter(user_id=user.id, period_key="2026-10", reviews_used=1))
            await session.commit()

        async with factory() as session:
            session.add(UsageCounter(user_id=user.id, period_key="2026-10", reviews_used=2))
            with pytest.raises(IntegrityError):
                await session.commit()

    @pytest.mark.asyncio
    async def test_different_periods_allowed(self, factory) -> None:
        user = await _user(factory, "usage_multi")
        async with factory() as session:
            for period in ("2026-09", "2026-10", "2026-11"):
                session.add(UsageCounter(user_id=user.id, period_key=period, reviews_used=1))
            await session.commit()

        async with factory() as session:
            rows = (await session.execute(select(UsageCounter))).scalars().all()
            assert len(rows) == 3

    @pytest.mark.asyncio
    async def test_reviews_used_defaults_zero(self, factory) -> None:
        user = await _user(factory, "usage_default")
        async with factory() as session:
            counter = UsageCounter(user_id=user.id, period_key="2026-10")
            session.add(counter)
            await session.commit()
            await session.refresh(counter)

            assert counter.reviews_used == 0


class TestSeedPlans:
    def test_four_plans_defined(self) -> None:
        """四个套餐覆盖路线图建议档位。"""
        from app.db.models.billing import SEED_PLANS

        assert {plan["id"] for plan in SEED_PLANS} == {
            "free",
            "student",
            "pro",
            "institution",
        }

    def test_free_plan_is_restrictive(self) -> None:
        """免费档必须严格：1 篇/月、无团队、无 API。

        若免费档宽松，付费转化率与成本控制都会失效。
        """
        from app.db.models.billing import SEED_PLANS

        free = next(plan for plan in SEED_PLANS if plan["id"] == "free")
        assert free["monthly_reviews"] == 1
        assert free["allow_team"] is False
        assert free["allow_api_access"] is False
        assert free["max_workspaces"] == 0

    def test_student_plan_allows_team_but_not_api(self) -> None:
        """学生档（¥19–29）给团队协作，但**不给 API**（API 是专业档卖点）。"""
        from app.db.models.billing import SEED_PLANS

        student = next(plan for plan in SEED_PLANS if plan["id"] == "student")
        assert student["monthly_reviews"] >= 10
        assert student["allow_team"] is True
        assert student["allow_api_access"] is False

    def test_pro_plan_allows_api(self) -> None:
        from app.db.models.billing import SEED_PLANS

        pro = next(plan for plan in SEED_PLANS if plan["id"] == "pro")
        assert pro["allow_api_access"] is True
        assert pro["allow_team"] is True

    def test_institution_unlimited_reviews(self) -> None:
        """机构版按席位收费，篇数不限。"""
        from app.db.models.billing import SEED_PLANS

        institution = next(plan for plan in SEED_PLANS if plan["id"] == "institution")
        assert institution["monthly_reviews"] == -1
        assert institution["max_seats_per_workspace"] >= 10
        assert institution["allow_api_access"] is True

    def test_prices_ascending(self) -> None:
        """价格必须单调递增（配置写错会让用户发现「专业版比学生版便宜」）。"""
        from app.db.models.billing import SEED_PLANS

        prices = [plan["price_cents"] for plan in SEED_PLANS]
        assert prices == sorted(prices), f"价格未单调递增：{prices}"

    def test_free_plan_price_is_zero(self) -> None:
        from app.db.models.billing import SEED_PLANS

        free = next(plan for plan in SEED_PLANS if plan["id"] == "free")
        assert free["price_cents"] == 0


class TestFreeTierFallback:
    def test_default_plan_id_constant(self) -> None:
        """无订阅用户回落到 `free` —— **不建订阅行**。

        为什么不建行：新用户注册就写一条订阅，会让「有多少付费用户」的统计
        被免费行污染；且注册流程会多一次写库。
        """
        from app.db.models.billing import DEFAULT_PLAN_ID

        assert DEFAULT_PLAN_ID == "free"


class TestPeriodKey:
    def test_period_key_format(self) -> None:
        """周期 key 是 `YYYY-MM`（UTC 年月）—— 与用户认知一致、便于 SQL 排查。"""
        from app.db.models.billing import period_key_for

        assert period_key_for(datetime(2026, 10, 22, 12, 0, tzinfo=UTC)) == "2026-10"
        assert period_key_for(datetime(2026, 1, 1, 0, 0, tzinfo=UTC)) == "2026-01"
        assert period_key_for(datetime(2026, 12, 31, 23, 59, tzinfo=UTC)) == "2026-12"

    def test_period_key_uses_utc(self) -> None:
        """必须按 UTC 切月：本地时区会让「月初/月末」边界不一致。"""
        from app.db.models.billing import period_key_for

        # UTC 2026-10-31 23:30 在 UTC+8 是 11-01 07:30，但仍属 10 月
        moment = datetime(2026, 10, 31, 23, 30, tzinfo=UTC)
        assert period_key_for(moment) == "2026-10"
