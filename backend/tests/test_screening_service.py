"""筛选服务测试（Phase 2 · PRISMA Task 3）。

覆盖：
1. **检索落库**：日志（含 `hits_by_source`）+ 候选快照，跨源去重；
2. **去重口径与 `source_registry` 对齐**：DOI → source_id → 归一化题名
   （计划初稿只做了两级且缺 DOI，这里按既有实现对齐）；
3. **决策推进阶段**：`include` 前进、`exclude`/`maybe` 停留；
4. **PRISMA 计数派生**：全部由明细算，且各源合计与总数自洽。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models.screening import DecisionStage, ScreeningVerdict
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


def _record(
    source_id: str,
    title: str,
    *,
    source_url: str | None = None,
    source: str = "arxiv",
) -> LiteratureRecord:
    """构造一条 `LiteratureRecord`。

    `source_url` 决定 DOI 提取结果（与 `source_registry._record_doi` 同口径：
    只看 `https://doi.org/...` 规范链接）。
    """
    url = source_url or f"https://arxiv.org/abs/{source_id}"
    return LiteratureRecord(
        source_id=f"{source}:{source_id}" if ":" not in source_id else source_id,
        title=title,
        authors=["A"],
        abstract="a",
        published_at=datetime(2024, 1, 1, tzinfo=UTC),
        source_url=url,
        pdf_url=f"{url}.pdf",
        categories=["cs.CL"],
    )


class TestRecordSearch:
    async def test_persists_log_and_snapshot(self, session: AsyncSession) -> None:
        service = ScreeningService(session)
        task_id = uuid.uuid4()

        log = await service.record_search(
            task_id,
            query="rag evaluation",
            sources=["arxiv", "openalex"],
            records=[_record("a1", "T1"), _record("a2", "T2")],
            hits_by_source={"arxiv": 1, "openalex": 1},
        )
        await session.commit()

        assert log.hits_total == 2
        assert log.hits_by_source == {"arxiv": 1, "openalex": 1}
        assert log.hits_deduped == 2
        records = await service.list_records(task_id)
        assert len(records) == 2

    async def test_dedupes_by_doi(self, session: AsyncSession) -> None:
        """同 DOI 不同源 → 只留一条（最高优先级判重键）。"""
        service = ScreeningService(session)
        task_id = uuid.uuid4()

        log = await service.record_search(
            task_id,
            query="rag",
            sources=["arxiv", "openalex"],
            records=[
                _record("a1", "Title A", source_url="https://doi.org/10.1234/ABC"),
                _record("o1", "Different Title", source_url="https://doi.org/10.1234/abc"),
            ],
            hits_by_source={"arxiv": 1, "openalex": 1},
        )
        await session.commit()

        assert log.hits_total == 2
        assert log.hits_deduped == 1
        assert len(await service.list_records(task_id)) == 1

    async def test_dedupes_by_source_id(self, session: AsyncSession) -> None:
        service = ScreeningService(session)
        task_id = uuid.uuid4()

        log = await service.record_search(
            task_id,
            query="rag",
            sources=["arxiv"],
            records=[
                _record("a1", "Title A"),
                _record("a1", "Totally Different Title"),
            ],
            hits_by_source={"arxiv": 2},
        )
        await session.commit()

        assert log.hits_deduped == 1

    async def test_dedupes_by_normalized_title(self, session: AsyncSession) -> None:
        """题名归一（NFKC + casefold + 折叠空白）后相同 → 视为重复。"""
        service = ScreeningService(session)
        task_id = uuid.uuid4()

        log = await service.record_search(
            task_id,
            query="rag",
            sources=["arxiv", "openalex"],
            records=[
                _record("a1", "RAG  Survey"),
                _record("o1", "rag survey"),
            ],
            hits_by_source={"arxiv": 1, "openalex": 1},
        )
        await session.commit()

        assert log.hits_deduped == 1

    async def test_keeps_distinct_records(self, session: AsyncSession) -> None:
        service = ScreeningService(session)
        task_id = uuid.uuid4()

        log = await service.record_search(
            task_id,
            query="rag",
            sources=["arxiv"],
            records=[_record("a1", "Title A"), _record("a2", "Title B")],
            hits_by_source={"arxiv": 2},
        )
        await session.commit()

        assert log.hits_deduped == 2

    async def test_derives_hits_by_source_when_not_given(self, session: AsyncSession) -> None:
        """未显式给 `hits_by_source` 时按记录来源推断（单源场景够用）。"""
        service = ScreeningService(session)
        task_id = uuid.uuid4()

        log = await service.record_search(
            task_id,
            query="rag",
            sources=["arxiv"],
            records=[_record("a1", "T1"), _record("a2", "T2")],
        )
        await session.commit()

        assert log.hits_by_source == {"arxiv": 2}


class TestDecide:
    async def test_include_advances_title_abstract_to_fulltext(self, session: AsyncSession) -> None:
        service = ScreeningService(session)
        task_id = uuid.uuid4()
        reviewer = uuid.uuid4()
        await service.record_search(
            task_id,
            query="rag",
            sources=["arxiv"],
            records=[_record("a1", "T1")],
            hits_by_source={"arxiv": 1},
        )
        await session.commit()
        record = (await service.list_records(task_id))[0]

        await service.decide(
            record.id,
            stage=DecisionStage.TITLE_ABSTRACT,
            verdict=ScreeningVerdict.INCLUDE,
            reviewer_id=reviewer,
        )
        await session.commit()

        updated = await service.get_record(record.id)
        assert updated.current_stage == DecisionStage.FULLTEXT.value

    async def test_include_advances_fulltext_to_included(self, session: AsyncSession) -> None:
        service = ScreeningService(session)
        task_id = uuid.uuid4()
        reviewer = uuid.uuid4()
        await service.record_search(
            task_id,
            query="rag",
            sources=["arxiv"],
            records=[_record("a1", "T1")],
            hits_by_source={"arxiv": 1},
        )
        await session.commit()
        record = (await service.list_records(task_id))[0]

        await service.decide(
            record.id,
            stage=DecisionStage.TITLE_ABSTRACT,
            verdict=ScreeningVerdict.INCLUDE,
            reviewer_id=reviewer,
        )
        await service.decide(
            record.id,
            stage=DecisionStage.FULLTEXT,
            verdict=ScreeningVerdict.INCLUDE,
            reviewer_id=reviewer,
        )
        await session.commit()

        updated = await service.get_record(record.id)
        assert updated.current_stage == DecisionStage.INCLUDED.value

    async def test_excluding_at_title_abstract_keeps_stage(self, session: AsyncSession) -> None:
        """排除后停在原阶段（不再前进，但记录仍在，供 PRISMA 计数）。"""
        service = ScreeningService(session)
        task_id = uuid.uuid4()
        reviewer = uuid.uuid4()
        await service.record_search(
            task_id,
            query="rag",
            sources=["arxiv"],
            records=[_record("a1", "T1")],
            hits_by_source={"arxiv": 1},
        )
        await session.commit()
        record = (await service.list_records(task_id))[0]

        await service.decide(
            record.id,
            stage=DecisionStage.TITLE_ABSTRACT,
            verdict=ScreeningVerdict.EXCLUDE,
            reviewer_id=reviewer,
            exclusion_reason="not relevant",
        )
        await session.commit()

        updated = await service.get_record(record.id)
        assert updated.current_stage == DecisionStage.TITLE_ABSTRACT.value

    async def test_maybe_keeps_stage(self, session: AsyncSession) -> None:
        service = ScreeningService(session)
        task_id = uuid.uuid4()
        reviewer = uuid.uuid4()
        await service.record_search(
            task_id,
            query="rag",
            sources=["arxiv"],
            records=[_record("a1", "T1")],
            hits_by_source={"arxiv": 1},
        )
        await session.commit()
        record = (await service.list_records(task_id))[0]

        await service.decide(
            record.id,
            stage=DecisionStage.TITLE_ABSTRACT,
            verdict=ScreeningVerdict.MAYBE,
            reviewer_id=reviewer,
        )
        await session.commit()

        updated = await service.get_record(record.id)
        assert updated.current_stage == DecisionStage.TITLE_ABSTRACT.value

    async def test_exclude_without_reason_raises(self, session: AsyncSession) -> None:
        service = ScreeningService(session)
        task_id = uuid.uuid4()
        reviewer = uuid.uuid4()
        await service.record_search(
            task_id,
            query="rag",
            sources=["arxiv"],
            records=[_record("a1", "T1")],
            hits_by_source={"arxiv": 1},
        )
        await session.commit()
        record = (await service.list_records(task_id))[0]

        with pytest.raises(ValueError, match="排除"):
            await service.decide(
                record.id,
                stage=DecisionStage.TITLE_ABSTRACT,
                verdict=ScreeningVerdict.EXCLUDE,
                reviewer_id=reviewer,
            )

    async def test_unknown_record_raises_not_found(self, session: AsyncSession) -> None:
        from app.core.exceptions import NotFoundException

        service = ScreeningService(session)

        with pytest.raises(NotFoundException):
            await service.get_record(uuid.uuid4())


class TestExcludedIsTerminal:
    """排除是**终态**：再次决策不能把「已排除」悄悄变回「待定/纳入」。

    为什么重要：PRISMA 的「标题/摘要阶段排除数」与「排除理由分类」都取
    **每位筛选者的最新决策**。若允许覆盖，一次误点「待定」就会让已排除的记录
    从流程图里消失、排除理由一并丢失——数字凭空减少，且没有痕迹提示
    「这条其实被排除过」。真实缺陷由 Task 7 服务器验收暴露。
    """

    async def _excluded(self, session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
        """造一条「已被排除」的记录，返回 (task_id, record_id, reviewer_id)。"""
        service = ScreeningService(session)
        task_id = uuid.uuid4()
        reviewer = uuid.uuid4()
        await service.record_search(
            task_id,
            query="rag",
            sources=["arxiv"],
            records=[_record("a1", "T1")],
            hits_by_source={"arxiv": 1},
        )
        record = (await service.list_records(task_id))[0]
        await service.decide(
            record.id,
            stage=DecisionStage.TITLE_ABSTRACT,
            verdict=ScreeningVerdict.EXCLUDE,
            reviewer_id=reviewer,
            exclusion_reason="not relevant",
        )
        return task_id, record.id, reviewer

    async def test_exclude_then_maybe_is_rejected(self, session: AsyncSession) -> None:
        _, record_id, reviewer = await self._excluded(session)
        service = ScreeningService(session)

        with pytest.raises(ValueError, match="已被你排除"):
            await service.decide(
                record_id,
                stage=DecisionStage.TITLE_ABSTRACT,
                verdict=ScreeningVerdict.MAYBE,
                reviewer_id=reviewer,
            )

    async def test_exclude_then_include_is_rejected(self, session: AsyncSession) -> None:
        _, record_id, reviewer = await self._excluded(session)
        service = ScreeningService(session)

        with pytest.raises(ValueError, match="已被你排除"):
            await service.decide(
                record_id,
                stage=DecisionStage.TITLE_ABSTRACT,
                verdict=ScreeningVerdict.INCLUDE,
                reviewer_id=reviewer,
            )

    async def test_rejected_override_does_not_change_counts(self, session: AsyncSession) -> None:
        """被拒绝的覆盖尝试**不得**改动计数。"""
        task_id, record_id, reviewer = await self._excluded(session)
        service = ScreeningService(session)

        with pytest.raises(ValueError):
            await service.decide(
                record_id,
                stage=DecisionStage.TITLE_ABSTRACT,
                verdict=ScreeningVerdict.MAYBE,
                reviewer_id=reviewer,
            )

        counts = await service.prisma_counts(task_id)
        assert counts["title_abstract"] == {"screened": 1, "excluded": 1}
        assert counts["exclusion_reasons"] == {"not relevant": 1}

    async def test_include_then_maybe_is_allowed(self, session: AsyncSession) -> None:
        """非排除结论仍可追加（include 后改主意为 maybe 是合法的复核动作）。"""
        service = ScreeningService(session)
        task_id = uuid.uuid4()
        reviewer = uuid.uuid4()
        await service.record_search(
            task_id,
            query="rag",
            sources=["arxiv"],
            records=[_record("a1", "T1")],
            hits_by_source={"arxiv": 1},
        )
        record = (await service.list_records(task_id))[0]

        await service.decide(
            record.id,
            stage=DecisionStage.TITLE_ABSTRACT,
            verdict=ScreeningVerdict.INCLUDE,
            reviewer_id=reviewer,
        )
        # 不抛错即可
        await service.decide(
            record.id,
            stage=DecisionStage.TITLE_ABSTRACT,
            verdict=ScreeningVerdict.MAYBE,
            reviewer_id=reviewer,
        )

    async def test_other_reviewer_may_still_decide(self, session: AsyncSession) -> None:
        """排除是**按人**的终态：双人筛选时，另一位筛选者仍可独立判断。"""
        _, record_id, _ = await self._excluded(session)
        service = ScreeningService(session)
        reviewer_b = uuid.uuid4()

        await service.decide(
            record_id,
            stage=DecisionStage.TITLE_ABSTRACT,
            verdict=ScreeningVerdict.INCLUDE,
            reviewer_id=reviewer_b,
        )


class TestPrismaCounts:
    async def _prepare(self, session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
        """两条记录：一条纳入到全文、一条标题阶段排除。

        上游各源共命中 50 条，但只回传了 2 条完整记录（分页/样本场景），
        因此显式传 `hits_total=50`——PRISMA 的识别数与去重数都基于上游报告。
        """
        service = ScreeningService(session)
        task_id = uuid.uuid4()
        reviewer = uuid.uuid4()
        await service.record_search(
            task_id,
            query="rag",
            sources=["arxiv", "openalex"],
            records=[
                _record("a1", "Title A"),
                _record("o1", "Title B"),
            ],
            hits_by_source={"arxiv": 30, "openalex": 20},
            hits_total=50,
        )
        await session.commit()
        records = await service.list_records(task_id)

        await service.decide(
            records[0].id,
            stage=DecisionStage.TITLE_ABSTRACT,
            verdict=ScreeningVerdict.INCLUDE,
            reviewer_id=reviewer,
        )
        await service.decide(
            records[1].id,
            stage=DecisionStage.TITLE_ABSTRACT,
            verdict=ScreeningVerdict.EXCLUDE,
            reviewer_id=reviewer,
            exclusion_reason="not relevant",
        )
        await session.commit()
        return task_id, reviewer

    async def test_counts_use_raw_hits_by_source(self, session: AsyncSession) -> None:
        """识别阶段必须用**去重前**各源命中数，且各源合计与总数自洽。"""
        task_id, _ = await self._prepare(session)
        service = ScreeningService(session)

        counts = await service.prisma_counts(task_id)

        assert counts["identified_by_source"] == {"arxiv": 30, "openalex": 20}
        assert counts["identified_total"] == 50
        assert sum(counts["identified_by_source"].values()) == counts["identified_total"]

    async def test_counts_records_screened_and_duplicates(self, session: AsyncSession) -> None:
        task_id, _ = await self._prepare(session)
        service = ScreeningService(session)

        counts = await service.prisma_counts(task_id)

        assert counts["records_screened"] == 2
        # 上游报 50 命中，实际落库 2 条样本 → 去重/淘汰 48
        assert counts["duplicates_removed"] == 48

    async def test_counts_title_abstract_stage(self, session: AsyncSession) -> None:
        task_id, _ = await self._prepare(session)
        service = ScreeningService(session)

        counts = await service.prisma_counts(task_id)

        assert counts["title_abstract"] == {"screened": 2, "excluded": 1}

    async def test_counts_exclusion_reasons(self, session: AsyncSession) -> None:
        task_id, _ = await self._prepare(session)
        service = ScreeningService(session)

        counts = await service.prisma_counts(task_id)

        assert counts["exclusion_reasons"] == {"not relevant": 1}

    async def test_counts_included(self, session: AsyncSession) -> None:
        task_id, _ = await self._prepare(session)
        service = ScreeningService(session)

        counts = await service.prisma_counts(task_id)

        # 纳入到全文阶段（还不是最终纳入）
        assert counts["included"] == 0

    async def test_counts_are_self_consistent(self, session: AsyncSession) -> None:
        """核心自洽性：筛查数 = 排除数 + 仍在流程中 + 已纳入。"""
        task_id, _ = await self._prepare(session)
        service = ScreeningService(session)

        counts = await service.prisma_counts(task_id)

        screened = counts["records_screened"]
        excluded = counts["title_abstract"]["excluded"] + counts["fulltext"]["excluded"]
        included = counts["included"]
        assert (
            screened == excluded + included + 1
        ), "两条记录里一条排除、一条进入全文阶段：screened=2, excluded=1, included=0, 在流程中=1"

    async def test_empty_task_returns_zero_counts(self, session: AsyncSession) -> None:
        service = ScreeningService(session)

        counts = await service.prisma_counts(uuid.uuid4())

        assert counts["identified_total"] == 0
        assert counts["records_screened"] == 0
        assert counts["included"] == 0
        assert counts["title_abstract"] == {"screened": 0, "excluded": 0}
        assert counts["exclusion_reasons"] == {}
