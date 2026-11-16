"""按模型单价估算成本（Issue #15 P2）。

## 锁定的口径

1. 输入/输出分开计价，未知模型返回 `None`，**绝不按 0 计算**；
2. 历史任务没有拆分时按配置输出占比估算，并标记 `split_estimated`；
3. 平台任务无模型快照，按平台默认模型估算并标记 `platform_default`；
4. 金额全部走 `Decimal`，聚合后统一 6 位小数。
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.db.models  # noqa: F401
from app.billing.model_prices import estimate_cost, lookup_price
from app.billing.services.plan_service import PlanService
from app.core.config import get_settings
from app.db.base import Base
from app.db.models.billing import SEED_PLANS, Plan
from app.db.models.task import Task
from app.db.models.user import User


@pytest.fixture(autouse=True)
def _pin_default_model(monkeypatch) -> None:
    """测试不依赖开发机/服务器 `.env` 里的默认模型。"""
    settings = get_settings()
    monkeypatch.setattr(settings.llm, "default_provider", "openai")
    monkeypatch.setattr(settings.llm, "default_model", "gpt-4o-mini")


class TestPriceCatalog:
    def test_exact_and_prefixed_models(self) -> None:
        mini = lookup_price("gpt-4o-mini")
        base = lookup_price("gpt-4o")
        assert mini is not None and base is not None
        # 长键优先：不能被 gpt-4o 先吃掉
        assert mini.input_per_mtok == Decimal("0.15")
        assert base.input_per_mtok == Decimal("2.50")

    def test_dated_and_provider_prefixed_names(self) -> None:
        price = lookup_price("openai/gpt-4o-2024-08-06")
        assert price is not None
        assert price.output_per_mtok == Decimal("10.00")

    def test_unknown_model_returns_none(self) -> None:
        assert lookup_price("totally-unknown-model") is None
        assert lookup_price(None) is None
        assert estimate_cost("totally-unknown-model", 1000, 1000) is None

    def test_cost_uses_input_output_prices_separately(self) -> None:
        # gpt-4o-mini: 0.15 输入 / 0.60 输出（USD / 1M）
        assert estimate_cost("gpt-4o-mini", 1_000_000, 0) == Decimal("0.15")
        assert estimate_cost("gpt-4o-mini", 0, 1_000_000) == Decimal("0.60")
        assert estimate_cost("gpt-4o-mini", 1_000_000, 1_000_000) == Decimal("0.75")


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
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


async def _task(
    factory,
    user_id,
    *,
    used: int,
    prompt: int = 0,
    completion: int = 0,
    is_byok: bool = False,
    snapshot: dict | None = None,
    created_at: datetime | None = None,
) -> None:
    async with factory() as session:
        session.add(
            Task(
                user_id=user_id,
                title="成本用例",
                description="d",
                task_type="literature_review",
                status="completed",
                config={},
                used_tokens=used,
                prompt_tokens=prompt,
                completion_tokens=completion,
                is_byok=is_byok,
                model_snapshot=snapshot,
                created_at=created_at or datetime(2026, 11, 10, tzinfo=UTC),
            )
        )
        await session.commit()


class TestCostEstimate:
    @pytest.mark.asyncio
    async def test_mixed_models_split_and_legacy(self, factory) -> None:
        user = await _user(factory, "cost_user")
        # 平台任务：默认 gpt-4o-mini，输入 1M
        await _task(factory, user.id, used=1_000_000, prompt=1_000_000)
        # BYOK：deepseek-chat，输出 1M
        await _task(
            factory,
            user.id,
            used=1_000_000,
            completion=1_000_000,
            is_byok=True,
            snapshot={"is_byok": True, "provider": "deepseek", "model": "deepseek-chat"},
        )
        # 历史任务：只有总量，按 30% 输出估算
        await _task(factory, user.id, used=1_000)

        data = await PlanService(factory).model_cost_estimate(user.id, period_key="2026-11")

        assert data["currency"] == "USD"
        # 0.15 + 1.10 + 0.000285
        assert data["total_cost"] == "1.250285"
        assert data["unpriced_tokens"] == 0
        assert data["split_estimated_tokens"] == 1_000

        rows = {row["model"]: row for row in data["models"]}
        assert rows["gpt-4o-mini"]["prompt_tokens"] == 1_000_700
        assert rows["gpt-4o-mini"]["completion_tokens"] == 300
        assert rows["gpt-4o-mini"]["split_estimated"] is True
        assert rows["deepseek-chat"]["total_cost"] == "1.100000"
        assert rows["deepseek-chat"]["model_source"] == "task_snapshot"

    @pytest.mark.asyncio
    async def test_unknown_model_is_not_counted_as_zero(self, factory) -> None:
        user = await _user(factory, "cost_unknown")
        await _task(
            factory,
            user.id,
            used=2_000,
            prompt=1_000,
            completion=1_000,
            is_byok=True,
            snapshot={"is_byok": True, "provider": "custom", "model": "my-private-model"},
        )

        data = await PlanService(factory).model_cost_estimate(user.id, period_key="2026-11")

        assert data["total_cost"] == "0.000000"
        assert data["unpriced_tokens"] == 2_000
        row = data["models"][0]
        assert row["priced"] is False
        assert row["total_cost"] is None

    @pytest.mark.asyncio
    async def test_platform_rows_are_marked_as_default_model(self, factory) -> None:
        user = await _user(factory, "cost_platform")
        await _task(factory, user.id, used=100, prompt=100)

        data = await PlanService(factory).model_cost_estimate(user.id, period_key="2026-11")

        row = data["models"][0]
        assert row["model_source"] == "platform_default"
        assert row["model"] == "gpt-4o-mini"
        assert row["is_byok"] is False
