"""BYOK / 平台模型用量分账（Issue #15 P2）。

## 为什么要分账

用户自费 token 跑的任务，如果和平台模型混在一个数字里：

1. 用户看不出「我的 key 到底省了多少 / 用了多少」；
2. 运营看不出 BYOK 渗透率，也就无法判断 P0-5 的免配额策略是否有效；
3. 将来按 token 收费时，没有可对账的口径。

## 判定口径

`task.is_byok` 是**创建时写死**的布尔值，不从凭据表现场推导：

- 凭据可能被删（外键 SET NULL），现场推导会把历史 BYOK 任务算成平台；
- 凭据可以停用/换 key，但任务当时用的就是自带模型，这一点不会变。

迁移会按 `llm_credential_id` 和 `model_snapshot.is_byok` 回填历史数据。
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.db.models  # noqa: F401
from app.billing.services.plan_service import PlanService
from app.db.base import Base
from app.db.models.billing import SEED_PLANS, Plan
from app.db.models.task import Task
from app.db.models.user import User
from app.schemas.billing import UsageOut


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
    tokens: int,
    is_byok: bool,
    created_at: datetime,
    credential_id=None,
    snapshot: dict | None = None,
) -> None:
    async with factory() as session:
        session.add(
            Task(
                user_id=user_id,
                title="分账用例",
                description="d",
                task_type="literature_review",
                status="completed",
                config={},
                used_tokens=tokens,
                quota_settled=True,
                is_byok=is_byok,
                llm_credential_id=credential_id,
                model_snapshot=snapshot,
                created_at=created_at,
            )
        )
        await session.commit()


class TestModelUsageSplit:
    @pytest.mark.asyncio
    async def test_splits_platform_and_byok_within_period(self, factory) -> None:
        user = await _user(factory, "split_user")
        other = await _user(factory, "split_other")
        now = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
        last_month = datetime(2026, 10, 31, 23, 59, tzinfo=UTC)

        await _task(factory, user.id, tokens=100, is_byok=False, created_at=now)
        await _task(factory, user.id, tokens=30, is_byok=False, created_at=now)
        # 凭据已删（外键 NULL），但标记仍是 BYOK —— 这是最容易算错的一类
        await _task(
            factory,
            user.id,
            tokens=50,
            is_byok=True,
            credential_id=None,
            snapshot={"is_byok": True, "model": "deepseek-chat"},
            created_at=now,
        )
        # 上个月：不得计入本月
        await _task(factory, user.id, tokens=999, is_byok=False, created_at=last_month)
        # 别人的任务：不得计入
        await _task(factory, other.id, tokens=777, is_byok=True, created_at=now)

        usage = await PlanService(factory).get_usage(user.id, moment=now)

        assert usage["model_usage"] == {
            "period_key": "2026-11",
            "platform": {"tasks": 2, "tokens": 130},
            "byok": {"tasks": 1, "tokens": 50},
            "total": {"tasks": 3, "tokens": 180},
        }

    @pytest.mark.asyncio
    async def test_empty_period_returns_zeros(self, factory) -> None:
        user = await _user(factory, "split_empty")

        usage = await PlanService(factory).get_usage(
            user.id, moment=datetime(2026, 11, 1, tzinfo=UTC)
        )

        assert usage["model_usage"] == {
            "period_key": "2026-11",
            "platform": {"tasks": 0, "tokens": 0},
            "byok": {"tasks": 0, "tokens": 0},
            "total": {"tasks": 0, "tokens": 0},
        }

    @pytest.mark.asyncio
    async def test_month_boundary_is_utc(self, factory) -> None:
        """UTC 月初 1 秒与上月最后一秒分别落两个月；本地时区不能影响口径。"""
        user = await _user(factory, "split_boundary")
        await _task(
            factory,
            user.id,
            tokens=10,
            is_byok=False,
            created_at=datetime(2026, 10, 31, 23, 59, 59, tzinfo=UTC),
        )
        await _task(
            factory,
            user.id,
            tokens=20,
            is_byok=True,
            created_at=datetime(2026, 11, 1, 0, 0, 1, tzinfo=UTC),
        )

        usage = await PlanService(factory).get_usage(
            user.id, moment=datetime(2026, 11, 15, tzinfo=UTC)
        )

        assert usage["model_usage"]["platform"] == {"tasks": 0, "tokens": 0}  # type: ignore[index]
        assert usage["model_usage"]["byok"] == {"tasks": 1, "tokens": 20}  # type: ignore[index]
        assert usage["model_usage"]["total"] == {"tasks": 1, "tokens": 20}  # type: ignore[index]

    @pytest.mark.asyncio
    async def test_usage_schema_accepts_split(self, factory) -> None:
        """`UsageOut(**usage)` 必须能直接吃下新字段（端点就是这么用的）。"""
        user = await _user(factory, "split_schema")
        usage = await PlanService(factory).get_usage(user.id)

        out = UsageOut(**usage)  # type: ignore[arg-type]

        assert out.model_usage.byok.tasks == 0
        assert out.model_usage.platform.tokens == 0


class TestTaskMark:
    @pytest.mark.asyncio
    async def test_task_defaults_to_platform(self, factory) -> None:
        user = await _user(factory, "split_default")
        async with factory() as session:
            task = Task(
                user_id=user.id,
                title="默认",
                description="d",
                task_type="literature_review",
                status="created",
                config={},
            )
            session.add(task)
            await session.commit()
            await session.refresh(task)

        assert task.is_byok is False


class TestUsageExport:
    @pytest.mark.asyncio
    async def test_export_includes_summary_and_rows(self, factory) -> None:
        user = await _user(factory, "export_user")
        now = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
        await _task(factory, user.id, tokens=100, is_byok=False, created_at=now)
        await _task(
            factory,
            user.id,
            tokens=50,
            is_byok=True,
            credential_id=None,
            snapshot={
                "is_byok": True,
                "provider": "deepseek",
                "model": "deepseek-chat",
                "name": "我的 DeepSeek",
            },
            created_at=now + timedelta(minutes=1),
        )

        data = await PlanService(factory).model_usage_export(user.id, period_key="2026-11")

        assert data["period_key"] == "2026-11"
        assert data["total"] == {"tasks": 2, "tokens": 150}
        rows = data["rows"]
        assert len(rows) == 2
        assert rows[0]["is_byok"] is False
        assert rows[0]["tokens"] == 100
        assert rows[1]["is_byok"] is True
        assert rows[1]["provider"] == "deepseek"
        assert rows[1]["model"] == "deepseek-chat"
        assert rows[1]["credential_name"] == "我的 DeepSeek"

    @pytest.mark.asyncio
    async def test_export_is_scoped_to_user_and_period(self, factory) -> None:
        user = await _user(factory, "export_scope")
        other = await _user(factory, "export_scope_other")
        inside = datetime(2026, 11, 2, tzinfo=UTC)
        outside = datetime(2026, 10, 31, 23, 59, tzinfo=UTC)
        await _task(factory, user.id, tokens=10, is_byok=False, created_at=inside)
        await _task(factory, user.id, tokens=20, is_byok=False, created_at=outside)
        await _task(factory, other.id, tokens=30, is_byok=True, created_at=inside)

        data = await PlanService(factory).model_usage_export(user.id, period_key="2026-11")

        assert data["total"] == {"tasks": 1, "tokens": 10}
        assert len(data["rows"]) == 1


class TestCsv:
    def _summary(self) -> dict:
        return {
            "platform": {"tasks": 1, "tokens": 100},
            "byok": {"tasks": 1, "tokens": 50},
            "total": {"tasks": 2, "tokens": 150},
        }

    def test_csv_has_bom_headers_summary_and_detail(self) -> None:
        from app.billing.export import build_usage_csv

        rows = [
            {
                "task_id": "t1",
                "created_at": datetime(2026, 11, 1, tzinfo=UTC),
                "title": "综述 A",
                "status": "completed",
                "is_byok": False,
                "provider": "",
                "model": "",
                "credential_name": "",
                "tokens": 100,
            },
            {
                "task_id": "t2",
                "created_at": datetime(2026, 11, 2, tzinfo=UTC),
                "title": "综述 B",
                "status": "failed",
                "is_byok": True,
                "provider": "deepseek",
                "model": "deepseek-chat",
                "credential_name": "我的 DeepSeek",
                "tokens": 50,
            },
        ]

        text = build_usage_csv("2026-11", self._summary(), rows)

        assert text.startswith("\ufeff")
        parsed = list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))
        assert parsed[0][:5] == ["类型", "周期", "模型来源", "任务数", "Token数"]
        assert parsed[1][:5] == ["汇总", "2026-11", "平台模型", "1", "100"]
        assert parsed[2][:5] == ["汇总", "2026-11", "自带模型", "1", "50"]
        assert parsed[3][:5] == ["汇总", "2026-11", "合计", "2", "150"]
        assert parsed[4][0] == "明细"
        assert parsed[4][2] == "平台模型"
        assert parsed[4][7] == "综述 A"
        assert parsed[5][9:12] == ["deepseek", "deepseek-chat", "我的 DeepSeek"]

    def test_csv_formula_injection_is_neutralized(self) -> None:
        from app.billing.export import build_usage_csv

        rows = [
            {
                "task_id": "t1",
                "created_at": datetime(2026, 11, 1, tzinfo=UTC),
                "title": "=cmd|' /C calc'!A0",
                "status": "completed",
                "is_byok": False,
                "provider": '=HYPERLINK("http://evil")',
                "model": "@SUM(1+1)",
                "credential_name": "+1",
                "tokens": 1,
            }
        ]

        text = build_usage_csv("2026-11", self._summary(), rows)
        parsed = list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))
        detail = parsed[4]

        assert detail[7].startswith("'=")
        assert detail[9].startswith("'=")
        assert detail[10].startswith("'@")
        assert detail[11].startswith("'+")

    def test_safe_cell_leaves_normal_text(self) -> None:
        from app.billing.export import safe_cell

        assert safe_cell("综述 A") == "综述 A"
        assert safe_cell(None) == ""
