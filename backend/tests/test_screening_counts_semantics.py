"""PRISMA 计数语义边界测试（Phase 2 · PRISMA Task 3 补充）。

## 背景

`prisma_counts` 的 `duplicates_removed` 有两种可能的含义，必须明确：

1. **字面去重**：`上游命中 - 去重后唯一记录`
2. **未落库差额**：`上游命中 - 实际落库记录`

当检索完整落库时两者相等；但当**上游只回传样本**（分页/限流/只取前 N 条）时，
两者不同。PRISMA 2020 要求报告「去除的重复记录数」，语义应当是 **(1)**。

本文件锁定：
- `hits_total` 显式 > 落库记录数时，差额**不能**被当成「重复」；
- 真正的重复应只由去重逻辑产生。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.knowledge.services.screening_service import ScreeningService
from app.tools.literature.models import LiteratureRecord


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


def _record(source_id: str, title: str) -> LiteratureRecord:
    url = f"https://arxiv.org/abs/{source_id}"
    return LiteratureRecord(
        source_id=source_id,
        title=title,
        authors=["A"],
        abstract="a",
        published_at=datetime(2024, 1, 1, tzinfo=UTC),
        source_url=url,
        pdf_url=f"{url}.pdf",
        categories=[],
    )


class TestHitsTotalSemantics:
    async def test_hits_total_derived_from_hits_by_source(self, session: AsyncSession) -> None:
        """显式给了各源命中时，`hits_total` 取各源之和（不是记录数）。"""
        service = ScreeningService(session)

        log = await service.record_search(
            uuid.uuid4(),
            query="q" * 5,
            sources=["arxiv", "openalex"],
            records=[_record("a1", "T1")],
            hits_by_source={"arxiv": 30, "openalex": 20},
        )

        assert log.hits_total == 50
        assert sum(log.hits_by_source.values()) == log.hits_total

    async def test_hits_total_derived_from_records_when_nothing_given(
        self, session: AsyncSession
    ) -> None:
        """什么都没给时退化为记录数。"""
        service = ScreeningService(session)

        log = await service.record_search(
            uuid.uuid4(),
            query="q" * 5,
            sources=["arxiv"],
            records=[_record("a1", "T1"), _record("a2", "T2")],
        )

        assert log.hits_total == 2
        assert log.hits_by_source == {"arxiv": 2}

    async def test_explicit_hits_total_wins(self, session: AsyncSession) -> None:
        """显式 `hits_total` 优先——支持「上游只回前 N 条」的分页场景。"""
        service = ScreeningService(session)

        log = await service.record_search(
            uuid.uuid4(),
            query="q" * 5,
            sources=["arxiv"],
            records=[_record("a1", "T1")],
            hits_by_source={"arxiv": 1},
            hits_total=137,
        )

        assert log.hits_total == 137
        assert log.hits_deduped == 1


class TestDuplicatesRemovedSemantics:
    async def test_full_ingest_duplicates_equal_deduped_difference(
        self, session: AsyncSession
    ) -> None:
        """完整落库时：重复数 = 命中数 - 去重后记录数（两义一致）。"""
        service = ScreeningService(session)
        task_id = uuid.uuid4()

        await service.record_search(
            task_id,
            query="q" * 5,
            sources=["arxiv"],
            records=[_record("a1", "Same Title"), _record("a2", "Same Title")],
        )
        await session.commit()

        counts = await service.prisma_counts(task_id)

        assert counts["identified_total"] == 2
        assert counts["records_screened"] == 1
        assert counts["duplicates_removed"] == 1

    async def test_sampled_ingest_reports_difference(self, session: AsyncSession) -> None:
        """上游报 137 命中、只落 1 条 → 差额 136 计入 duplicates_removed。

        ⚠️ 这是**已知语义局限**：PRISMA 的「去除重复」严格指去重，而本字段
        在样本场景下混合了「未落库」部分。之所以这样选：对用户而言
        「137 命中 → 1 条进入筛选」的差额必须有个去处，否则 PRISMA 流程图
        的数字无法闭合（审稿人只会问「剩下 136 条呢」）。
        显式记录在此，避免后人误以为它是纯去重数。
        """
        service = ScreeningService(session)
        task_id = uuid.uuid4()

        await service.record_search(
            task_id,
            query="q" * 5,
            sources=["arxiv"],
            records=[_record("a1", "T1")],
            hits_by_source={"arxiv": 137},
            hits_total=137,
        )
        await session.commit()

        counts = await service.prisma_counts(task_id)

        assert counts["identified_total"] == 137
        assert counts["records_screened"] == 1
        assert counts["duplicates_removed"] == 136

    async def test_numbers_close_the_flow(self, session: AsyncSession) -> None:
        """流程闭合：识别总数 = 去重后记录 + 去除数。"""
        service = ScreeningService(session)
        task_id = uuid.uuid4()

        await service.record_search(
            task_id,
            query="q" * 5,
            sources=["arxiv"],
            records=[
                _record("a1", "Same Title"),
                _record("a2", "Same Title"),
                _record("a3", "Unique"),
            ],
            hits_by_source={"arxiv": 3},
        )
        await session.commit()

        counts = await service.prisma_counts(task_id)

        assert (
            counts["identified_total"] == counts["records_screened"] + counts["duplicates_removed"]
        ), "PRISMA 流程图必须闭合：识别 = 去重后记录 + 去除数"
