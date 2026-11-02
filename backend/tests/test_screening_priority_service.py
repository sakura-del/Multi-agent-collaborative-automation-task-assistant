"""筛选优先级：Repository 查询与 Service 标签聚合测试。"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.knowledge.services.screening_service as screening_service_module
from app.db.base import Base
from app.db.models.screening import DecisionStage, ScreeningVerdict
from app.db.repositories.screening import ScreeningRepository
from app.knowledge.services.screening_priority import PriorityUnavailableError
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


def _record(source_id: str, title: str, abstract: str = "") -> LiteratureRecord:
    return LiteratureRecord(
        source_id=f"arxiv:{source_id}",
        title=title,
        authors=["A"],
        abstract=abstract,
        published_at=datetime(2024, 1, 1, tzinfo=UTC),
        source_url=f"https://arxiv.org/abs/{source_id}",
        pdf_url=f"https://arxiv.org/abs/{source_id}.pdf",
        categories=["cs.CL"],
    )


async def _seed(session: AsyncSession, task_id: uuid.UUID) -> list:
    service = ScreeningService(session)
    await service.record_search(
        task_id,
        query="rag screening",
        sources=["arxiv"],
        records=[
            _record("pos", "retrieval augmented generation evaluation", "evidence benchmark"),
            _record("neg", "clinical cancer trial patient outcome", "medical treatment"),
            _record("maybe", "uncertain review", "needs manual judgment"),
            _record("c1", "retrieval augmented generation evidence", "benchmark evaluation"),
            _record("c2", "clinical trial patient treatment", "cancer outcome"),
        ],
        hits_by_source={"arxiv": 5},
    )
    return await service.list_records(task_id)


async def test_repository_lists_task_decisions_in_seq_order(session: AsyncSession) -> None:
    task_id = uuid.uuid4()
    records = await _seed(session, task_id)
    reviewer = uuid.uuid4()
    repo = ScreeningRepository(session)
    await repo.append_decision(
        records[0].id,
        stage=DecisionStage.TITLE_ABSTRACT,
        verdict=ScreeningVerdict.INCLUDE,
        reviewer_id=reviewer,
    )
    await repo.append_decision(
        records[1].id,
        stage=DecisionStage.TITLE_ABSTRACT,
        verdict=ScreeningVerdict.EXCLUDE,
        reviewer_id=reviewer,
        exclusion_reason="not relevant",
    )
    await session.commit()

    decisions = await repo.list_decisions_for_task(task_id, DecisionStage.TITLE_ABSTRACT)

    assert {row.record_id for row in decisions} == {records[0].id, records[1].id}
    assert all(row.stage == DecisionStage.TITLE_ABSTRACT.value for row in decisions)


async def test_service_aggregates_task_wide_labels_and_excludes_decided_records(
    session: AsyncSession,
) -> None:
    task_id = uuid.uuid4()
    records = await _seed(session, task_id)
    reviewer = uuid.uuid4()
    service = ScreeningService(session)
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
    await service.decide(
        records[2].id,
        stage=DecisionStage.TITLE_ABSTRACT,
        verdict=ScreeningVerdict.MAYBE,
        reviewer_id=reviewer,
    )

    ranking = await service.priority(task_id)

    assert ranking.status == "ready"
    assert ranking.positive == 1
    assert ranking.negative == 1
    assert ranking.ignored == 1
    assert ranking.candidates == 2
    assert {item.record_id for item in ranking.items} == {str(records[3].id), str(records[4].id)}


async def test_priority_is_task_scoped(session: AsyncSession) -> None:
    task_a = uuid.uuid4()
    task_b = uuid.uuid4()
    records_a = await _seed(session, task_a)
    records_b = await _seed(session, task_b)
    reviewer = uuid.uuid4()
    service = ScreeningService(session)
    await service.decide(
        records_a[0].id,
        stage=DecisionStage.TITLE_ABSTRACT,
        verdict=ScreeningVerdict.INCLUDE,
        reviewer_id=reviewer,
    )
    await service.decide(
        records_a[1].id,
        stage=DecisionStage.TITLE_ABSTRACT,
        verdict=ScreeningVerdict.EXCLUDE,
        reviewer_id=reviewer,
        exclusion_reason="x",
    )
    await service.decide(
        records_b[1].id,
        stage=DecisionStage.TITLE_ABSTRACT,
        verdict=ScreeningVerdict.EXCLUDE,
        reviewer_id=reviewer,
        exclusion_reason="x",
    )

    ranking = await service.priority(task_a)

    assert ranking.positive == 1
    assert ranking.negative == 1


async def test_missing_labels_returns_insufficient(session: AsyncSession) -> None:
    task_id = uuid.uuid4()
    await _seed(session, task_id)
    service = ScreeningService(session)

    ranking = await service.priority(task_id)

    assert ranking.status == "insufficient_labels"
    assert ranking.positive == 0
    assert ranking.negative == 0
    assert ranking.candidates == 5


async def test_model_failure_becomes_priority_unavailable(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    task_id = uuid.uuid4()
    await _seed(session, task_id)
    service = ScreeningService(session)

    def boom(*args: object, **kwargs: object) -> None:
        raise ValueError("model failed")

    monkeypatch.setattr(screening_service_module, "rank_candidates", boom)

    with pytest.raises(PriorityUnavailableError):
        await service.priority(task_id)
