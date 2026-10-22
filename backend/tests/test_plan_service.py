"""套餐服务测试（Phase 2 · 计费套餐 Task 2）。

## 本文件锁定

1. **默认免费档**：无订阅用户回落到 `free`，**不建订阅行**；
2. **篇数原子扣减**：并发建任务不超发（这是收钱依据）；
3. **跨月自动重置**：新月份计数从 0 开始；
4. **订阅/升级/降级**：同一用户只有一条 active；升级不产生两条 active；
5. **不限篇数**：`monthly_reviews=-1` 时不限制；
6. **功能门禁**：`allow_team` / `allow_api_access` / `max_workspaces`。

## 为什么篇数扣减必须是数据库原子操作

「先 SELECT 查已用，再 UPDATE +1」在并发下必然超发：两个请求同时读到 9/10，
都通过 → 11 篇。篇数是**收钱依据**，超发就是直接少收钱。
所以用 `UPDATE ... WHERE reviews_used < limit` 的受影响行数判断。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.db.models  # noqa: F401
from app.billing.services.plan_service import (
    FeatureNotAvailable,
    PlanQuotaExceeded,
    PlanService,
)
from app.db.base import Base
from app.db.models.billing import SEED_PLANS, Plan, Subscription, UsageCounter
from app.db.models.user import User


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # 种入套餐
    async with factory() as session:
        for spec in SEED_PLANS:
            session.add(Plan(**spec, is_active=True))
        await session.commit()

    yield factory
    await engine.dispose()


async def _user(factory, name: str) -> User:
    async with factory() as session:
        user = User(username=name, email=f"{name}@example.com", password_hash="x", is_active=True)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


class TestDefaultFreePlan:
    @pytest.mark.asyncio
    async def test_no_subscription_falls_back_to_free(self, factory) -> None:
        user = await _user(factory, "free_user")
        service = PlanService(factory)

        plan = await service.get_active_plan(user.id)

        assert plan.id == "free"
        assert plan.monthly_reviews == 1

    @pytest.mark.asyncio
    async def test_free_plan_does_not_create_subscription_row(self, factory) -> None:
        """**不建订阅行**：新用户注册就写订阅会让「付费用户数」统计被污染。"""
        user = await _user(factory, "free_no_row")
        service = PlanService(factory)
        await service.get_active_plan(user.id)

        async with factory() as session:
            rows = (await session.execute(select(Subscription))).scalars().all()
            assert rows == []

    @pytest.mark.asyncio
    async def test_free_usage_starts_at_zero(self, factory) -> None:
        user = await _user(factory, "free_usage")
        service = PlanService(factory)

        usage = await service.get_usage(user.id)

        assert usage["reviews_used"] == 0
        assert usage["monthly_reviews"] == 1
        assert usage["reviews_remaining"] == 1


class TestConsumeReview:
    @pytest.mark.asyncio
    async def test_first_review_allowed(self, factory) -> None:
        user = await _user(factory, "c1")
        service = PlanService(factory)

        await service.check_and_consume_review(user.id)

        usage = await service.get_usage(user.id)
        assert usage["reviews_used"] == 1

    @pytest.mark.asyncio
    async def test_second_review_rejected_on_free(self, factory) -> None:
        """免费档 1 篇/月：第 2 篇必须 429。"""
        user = await _user(factory, "c2")
        service = PlanService(factory)
        await service.check_and_consume_review(user.id)

        with pytest.raises(PlanQuotaExceeded) as exc_info:
            await service.check_and_consume_review(user.id)

        assert exc_info.value.status_code == 429
        assert exc_info.value.error_code == "PLAN_QUOTA_EXCEEDED"
        assert exc_info.value.used == 1
        assert exc_info.value.limit == 1

    @pytest.mark.asyncio
    async def test_rejected_request_does_not_consume(self, factory) -> None:
        """被拒绝的请求**不扣减**（否则用户白丢额度）。"""
        user = await _user(factory, "c3")
        service = PlanService(factory)
        await service.check_and_consume_review(user.id)

        with pytest.raises(PlanQuotaExceeded):
            await service.check_and_consume_review(user.id)

        usage = await service.get_usage(user.id)
        assert usage["reviews_used"] == 1, "拒绝后计数不应增加"

    @pytest.mark.asyncio
    async def test_student_plan_allows_ten(self, factory) -> None:
        user = await _user(factory, "c4")
        service = PlanService(factory)
        await service.subscribe(user.id, "student", note="测试")

        for _ in range(10):
            await service.check_and_consume_review(user.id)

        with pytest.raises(PlanQuotaExceeded):
            await service.check_and_consume_review(user.id)

    @pytest.mark.asyncio
    async def test_unlimited_plan_never_rejects(self, factory) -> None:
        """机构版 `monthly_reviews=-1`：不限制。"""
        user = await _user(factory, "c5")
        service = PlanService(factory)
        await service.subscribe(user.id, "institution", note="测试")

        for _ in range(30):
            await service.check_and_consume_review(user.id)

        usage = await service.get_usage(user.id)
        assert usage["reviews_used"] == 30
        assert usage["reviews_remaining"] is None, "不限篇数时剩余为 None"


class TestMonthlyReset:
    @pytest.mark.asyncio
    async def test_new_month_starts_fresh(self, factory) -> None:
        user = await _user(factory, "m1")
        service = PlanService(factory)
        await service.check_and_consume_review(user.id)

        # 模拟下个月
        next_month = datetime.now(UTC) + timedelta(days=32)
        usage = await service.get_usage(user.id, moment=next_month)

        assert usage["period_key"] != datetime.now(UTC).strftime("%Y-%m")
        assert usage["reviews_used"] == 0

    @pytest.mark.asyncio
    async def test_counters_are_per_period_rows(self, factory) -> None:
        """不同月份是**不同行**（便于 SQL 审计）。"""
        user = await _user(factory, "m2")
        service = PlanService(factory)
        await service.check_and_consume_review(user.id)

        next_month = datetime.now(UTC) + timedelta(days=32)
        await service.check_and_consume_review(user.id, moment=next_month)

        async with factory() as session:
            rows = (await session.execute(select(UsageCounter))).scalars().all()
            assert len(rows) == 2
            assert {row.reviews_used for row in rows} == {1}


class TestSubscription:
    @pytest.mark.asyncio
    async def test_subscribe_creates_active(self, factory) -> None:
        user = await _user(factory, "s1")
        service = PlanService(factory)

        sub = await service.subscribe(user.id, "pro", note="管理员开通")

        assert sub.status == "active"
        assert sub.plan_id == "pro"
        assert sub.source == "manual"
        assert sub.note == "管理员开通"

    @pytest.mark.asyncio
    async def test_upgrade_cancels_previous(self, factory) -> None:
        """升级后**只有一条 active**（否则「本月还能跑几篇」无法判定）。"""
        user = await _user(factory, "s2")
        service = PlanService(factory)
        await service.subscribe(user.id, "student", note="首购")
        await service.subscribe(user.id, "pro", note="升级")

        async with factory() as session:
            active = (
                (await session.execute(select(Subscription).where(Subscription.status == "active")))
                .scalars()
                .all()
            )
            cancelled = (
                (
                    await session.execute(
                        select(Subscription).where(Subscription.status == "cancelled")
                    )
                )
                .scalars()
                .all()
            )

        assert len(active) == 1
        assert active[0].plan_id == "pro"
        assert len(cancelled) == 1
        assert cancelled[0].plan_id == "student"

    @pytest.mark.asyncio
    async def test_subscribe_unknown_plan_rejected(self, factory) -> None:
        user = await _user(factory, "s3")
        service = PlanService(factory)

        with pytest.raises(ValueError, match="套餐不存在"):
            await service.subscribe(user.id, "nonexistent", note="x")

    @pytest.mark.asyncio
    async def test_cancel_returns_to_free(self, factory) -> None:
        user = await _user(factory, "s4")
        service = PlanService(factory)
        await service.subscribe(user.id, "pro", note="x")

        await service.cancel(user.id)

        plan = await service.get_active_plan(user.id)
        assert plan.id == "free"

    @pytest.mark.asyncio
    async def test_expired_subscription_ignored(self, factory) -> None:
        """过期订阅不再生效（回落免费档）。"""
        user = await _user(factory, "s5")
        service = PlanService(factory)
        sub = await service.subscribe(user.id, "pro", note="x")

        async with factory() as session:
            stored = await session.get(Subscription, sub.id)
            assert stored is not None
            stored.status = "expired"
            await session.commit()

        plan = await service.get_active_plan(user.id)
        assert plan.id == "free"

    @pytest.mark.asyncio
    async def test_period_end_after_period_start(self, factory) -> None:
        user = await _user(factory, "s6")
        service = PlanService(factory)

        sub = await service.subscribe(user.id, "student", note="x", days=30)

        assert sub.period_end > sub.period_start
        delta = sub.period_end - sub.period_start
        assert timedelta(days=29) < delta < timedelta(days=31)


class TestFeatureGates:
    @pytest.mark.asyncio
    async def test_free_cannot_use_team(self, factory) -> None:
        user = await _user(factory, "g1")
        service = PlanService(factory)

        with pytest.raises(FeatureNotAvailable, match="团队"):
            await service.require_feature(user.id, "team")

    @pytest.mark.asyncio
    async def test_student_can_use_team(self, factory) -> None:
        user = await _user(factory, "g2")
        service = PlanService(factory)
        await service.subscribe(user.id, "student", note="x")

        await service.require_feature(user.id, "team")  # 不抛异常

    @pytest.mark.asyncio
    async def test_free_cannot_use_api(self, factory) -> None:
        user = await _user(factory, "g3")
        service = PlanService(factory)

        with pytest.raises(FeatureNotAvailable, match="API"):
            await service.require_feature(user.id, "api")

    @pytest.mark.asyncio
    async def test_student_cannot_use_api(self, factory) -> None:
        """API 是专业档卖点，学生档不给。"""
        user = await _user(factory, "g4")
        service = PlanService(factory)
        await service.subscribe(user.id, "student", note="x")

        with pytest.raises(FeatureNotAvailable, match="API"):
            await service.require_feature(user.id, "api")

    @pytest.mark.asyncio
    async def test_pro_can_use_api(self, factory) -> None:
        user = await _user(factory, "g5")
        service = PlanService(factory)
        await service.subscribe(user.id, "pro", note="x")

        await service.require_feature(user.id, "api")

    @pytest.mark.asyncio
    async def test_unknown_feature_rejected(self, factory) -> None:
        user = await _user(factory, "g6")
        service = PlanService(factory)

        with pytest.raises(ValueError, match="未知功能"):
            await service.require_feature(user.id, "nonsense")


class TestWorkspaceLimits:
    @pytest.mark.asyncio
    async def test_free_cannot_create_workspace(self, factory) -> None:
        user = await _user(factory, "w1")
        service = PlanService(factory)

        allowed = await service.can_create_workspace(user.id, current_count=0)

        assert allowed is False

    @pytest.mark.asyncio
    async def test_student_allows_one_workspace(self, factory) -> None:
        user = await _user(factory, "w2")
        service = PlanService(factory)
        await service.subscribe(user.id, "student", note="x")

        assert await service.can_create_workspace(user.id, current_count=0) is True
        assert await service.can_create_workspace(user.id, current_count=1) is False

    @pytest.mark.asyncio
    async def test_seat_limit_capped_by_plan(self, factory) -> None:
        """席位上上限由套餐约束（学生档最多 3 席）。"""
        user = await _user(factory, "w3")
        service = PlanService(factory)
        await service.subscribe(user.id, "student", note="x")

        assert await service.max_seats_for(user.id) == 3

    @pytest.mark.asyncio
    async def test_free_has_no_seats(self, factory) -> None:
        user = await _user(factory, "w4")
        service = PlanService(factory)

        assert await service.max_seats_for(user.id) == 0


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_consume_does_not_exceed_limit(self, tmp_path) -> None:
        """**并发扣篇数不超发**（收钱依据）。

        用文件型 SQLite + 独立 engine 模拟多连接（`:memory:` 库不跨连接共享）。
        """
        from sqlalchemy.ext.asyncio import AsyncSession as _Session
        from sqlalchemy.ext.asyncio import async_sessionmaker as _factory
        from sqlalchemy.ext.asyncio import create_async_engine as _engine

        db_path = tmp_path / "billing.db"
        url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
        # WAL + busy_timeout：并发写必需。默认 rollback journal 下多个连接同时写
        # 会直接报 `database is locked`（本地满负载跑全量时实际踩到过，
        # 单跑这条用例却通过 —— 典型的负载相关 flaky）。
        setup_engine = _engine(url, connect_args={"timeout": 30})
        async with setup_engine.begin() as connection:
            await connection.exec_driver_sql("PRAGMA journal_mode=WAL")
            await connection.run_sync(Base.metadata.create_all)
        setup_factory = _factory(setup_engine, class_=_Session, expire_on_commit=False)
        async with setup_factory() as session:
            for spec in SEED_PLANS:
                session.add(Plan(**spec, is_active=True))
            user = User(
                username="conc", email="conc@example.com", password_hash="x", is_active=True
            )
            session.add(user)
            await session.commit()
            user_id = user.id
        await setup_engine.dispose()

        service = PlanService(setup_factory)
        # 学生档 10 篇/月，同时发起 20 个请求
        await service.subscribe(user_id, "student", note="x")

        async def attempt() -> bool:
            engine = _engine(url, connect_args={"timeout": 30})
            factory = _factory(engine, class_=_Session, expire_on_commit=False)
            try:
                await PlanService(factory).check_and_consume_review(user_id)
                return True
            except PlanQuotaExceeded:
                return False
            finally:
                await engine.dispose()

        results = await asyncio.gather(*(attempt() for _ in range(20)))

        usage = await service.get_usage(user_id)
        assert usage["reviews_used"] <= 10, f"超发：{usage['reviews_used']} > 10"
        assert sum(results) <= 10
