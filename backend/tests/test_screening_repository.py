"""筛选 Repository 测试（Phase 2 · PRISMA Task 2）。

覆盖：
1. **追加式决策**：同一记录同一阶段多次决策是**多行**（审计轨迹），
   当前状态由最新决策派生；
2. **双人分歧识别**：同一记录同一阶段不同筛选者结论不同 → 必须被 `find_conflicts` 抓到；
3. **排除必须有理由**：服务层与 DB CheckConstraint 双重保证；
4. 检索日志与候选记录的查询口径。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models.screening import (
    DecisionStage,
    ScreeningDecision,
    ScreeningRecord,
    ScreeningVerdict,
    SearchLog,
)
from app.db.repositories.screening import ScreeningRepository


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


async def _seed(
    session: AsyncSession, *, titles: tuple[str, ...] = ("T1",)
) -> tuple[uuid.UUID, list[uuid.UUID], uuid.UUID, uuid.UUID]:
    """插入 1 条检索日志 + N 条候选记录；返回 (task_id, record_ids, reviewer_a, reviewer_b)。"""
    task_id = uuid.uuid4()
    reviewer_a, reviewer_b = uuid.uuid4(), uuid.uuid4()
    log = SearchLog(
        task_id=task_id,
        query="retrieval augmented generation",
        sources=["arxiv"],
        hits_total=len(titles),
        hits_by_source={"arxiv": len(titles)},
        hits_deduped=len(titles),
        executed_at=datetime.now(UTC),
    )
    session.add(log)
    await session.flush()

    record_ids: list[uuid.UUID] = []
    for index, title in enumerate(titles):
        record = ScreeningRecord(
            search_log_id=log.id,
            task_id=task_id,
            source="arxiv",
            source_id=f"2401.0000{index}v1",
            title=title,
            authors=["A"],
            abstract="a",
            fulltext_status="unavailable",
        )
        session.add(record)
        await session.flush()
        record_ids.append(record.id)
    return task_id, record_ids, reviewer_a, reviewer_b


class TestSearchLogRepository:
    async def test_add_and_list_search_logs(self, session: AsyncSession) -> None:
        task_id, _, _, _ = await _seed(session)
        repo = ScreeningRepository(session)

        logs = await repo.list_search_logs(task_id)

        assert len(logs) == 1
        assert logs[0].hits_by_source == {"arxiv": 1}

    async def test_list_search_logs_is_empty_for_other_task(self, session: AsyncSession) -> None:
        await _seed(session)
        repo = ScreeningRepository(session)

        assert await repo.list_search_logs(uuid.uuid4()) == []


class TestAppendDecision:
    async def test_decisions_are_appended_not_overwritten(self, session: AsyncSession) -> None:
        """追加式：两次决策必须是两行（审计轨迹）。"""
        _, record_ids, reviewer_a, _ = await _seed(session)
        repo = ScreeningRepository(session)

        await repo.append_decision(
            record_ids[0],
            stage=DecisionStage.TITLE_ABSTRACT,
            verdict=ScreeningVerdict.MAYBE,
            reviewer_id=reviewer_a,
        )
        await repo.append_decision(
            record_ids[0],
            stage=DecisionStage.TITLE_ABSTRACT,
            verdict=ScreeningVerdict.EXCLUDE,
            exclusion_reason="not relevant",
            reviewer_id=reviewer_a,
        )
        await session.commit()

        rows = await repo.list_decisions(record_ids[0], DecisionStage.TITLE_ABSTRACT)
        assert len(rows) == 2

    async def test_latest_decision_is_the_newest_row(self, session: AsyncSession) -> None:
        _, record_ids, reviewer_a, _ = await _seed(session)
        repo = ScreeningRepository(session)

        await repo.append_decision(
            record_ids[0],
            stage=DecisionStage.TITLE_ABSTRACT,
            verdict=ScreeningVerdict.MAYBE,
            reviewer_id=reviewer_a,
        )
        await repo.append_decision(
            record_ids[0],
            stage=DecisionStage.TITLE_ABSTRACT,
            verdict=ScreeningVerdict.INCLUDE,
            reviewer_id=reviewer_a,
        )
        await session.commit()

        latest = await repo.latest_decision(record_ids[0], DecisionStage.TITLE_ABSTRACT, reviewer_a)
        assert latest is not None
        assert latest.verdict == ScreeningVerdict.INCLUDE.value

    async def test_latest_decision_isolated_per_reviewer(self, session: AsyncSession) -> None:
        """`latest_decision` 必须按筛选者隔离（双人独立筛选的前提）。"""
        _, record_ids, reviewer_a, reviewer_b = await _seed(session)
        repo = ScreeningRepository(session)

        await repo.append_decision(
            record_ids[0],
            stage=DecisionStage.TITLE_ABSTRACT,
            verdict=ScreeningVerdict.INCLUDE,
            reviewer_id=reviewer_a,
        )
        await repo.append_decision(
            record_ids[0],
            stage=DecisionStage.TITLE_ABSTRACT,
            verdict=ScreeningVerdict.EXCLUDE,
            exclusion_reason="off topic",
            reviewer_id=reviewer_b,
        )
        await session.commit()

        latest_a = await repo.latest_decision(
            record_ids[0], DecisionStage.TITLE_ABSTRACT, reviewer_a
        )
        latest_b = await repo.latest_decision(
            record_ids[0], DecisionStage.TITLE_ABSTRACT, reviewer_b
        )

        assert latest_a is not None and latest_a.verdict == ScreeningVerdict.INCLUDE.value
        assert latest_b is not None and latest_b.verdict == ScreeningVerdict.EXCLUDE.value

    async def test_append_decision_rejects_exclude_without_reason(
        self, session: AsyncSession
    ) -> None:
        """排除必须给理由（服务层与数据层双重保证）。"""
        _, record_ids, reviewer_a, _ = await _seed(session)
        repo = ScreeningRepository(session)

        with pytest.raises(ValueError, match="排除"):
            await repo.append_decision(
                record_ids[0],
                stage=DecisionStage.TITLE_ABSTRACT,
                verdict=ScreeningVerdict.EXCLUDE,
                reviewer_id=reviewer_a,
            )

    async def test_append_decision_accepts_include_without_reason(
        self, session: AsyncSession
    ) -> None:
        """纳入不需要理由（约束只针对 exclude）。"""
        _, record_ids, reviewer_a, _ = await _seed(session)
        repo = ScreeningRepository(session)

        row = await repo.append_decision(
            record_ids[0],
            stage=DecisionStage.TITLE_ABSTRACT,
            verdict=ScreeningVerdict.INCLUDE,
            reviewer_id=reviewer_a,
        )

        assert row.verdict == ScreeningVerdict.INCLUDE.value
        assert row.exclusion_reason is None


class TestFindConflicts:
    async def test_disagreeing_reviewers_are_conflicts(self, session: AsyncSession) -> None:
        """双人独立筛选：结论不同的记录必须被识别为冲突。"""
        _, record_ids, reviewer_a, reviewer_b = await _seed(session)
        repo = ScreeningRepository(session)

        await repo.append_decision(
            record_ids[0],
            stage=DecisionStage.TITLE_ABSTRACT,
            verdict=ScreeningVerdict.INCLUDE,
            reviewer_id=reviewer_a,
        )
        await repo.append_decision(
            record_ids[0],
            stage=DecisionStage.TITLE_ABSTRACT,
            verdict=ScreeningVerdict.EXCLUDE,
            exclusion_reason="off topic",
            reviewer_id=reviewer_b,
        )
        await session.commit()

        task_id = (await session.execute(select(ScreeningRecord.task_id))).scalar_one()
        conflicts = await repo.find_conflicts(task_id, DecisionStage.TITLE_ABSTRACT)

        assert [conflict.record_id for conflict in conflicts] == [record_ids[0]]
        assert set(conflicts[0].verdicts.values()) == {
            ScreeningVerdict.INCLUDE.value,
            ScreeningVerdict.EXCLUDE.value,
        }

    async def test_agreeing_reviewers_are_not_conflicts(self, session: AsyncSession) -> None:
        _, record_ids, reviewer_a, reviewer_b = await _seed(session)
        repo = ScreeningRepository(session)

        for reviewer in (reviewer_a, reviewer_b):
            await repo.append_decision(
                record_ids[0],
                stage=DecisionStage.TITLE_ABSTRACT,
                verdict=ScreeningVerdict.INCLUDE,
                reviewer_id=reviewer,
            )
        await session.commit()

        task_id = (await session.execute(select(ScreeningRecord.task_id))).scalar_one()
        assert await repo.find_conflicts(task_id, DecisionStage.TITLE_ABSTRACT) == []

    async def test_single_reviewer_is_not_a_conflict(self, session: AsyncSession) -> None:
        """只有一个人筛过的记录不算冲突（还没到分歧那一步）。"""
        _, record_ids, reviewer_a, _ = await _seed(session)
        repo = ScreeningRepository(session)

        await repo.append_decision(
            record_ids[0],
            stage=DecisionStage.TITLE_ABSTRACT,
            verdict=ScreeningVerdict.INCLUDE,
            reviewer_id=reviewer_a,
        )
        await session.commit()

        task_id = (await session.execute(select(ScreeningRecord.task_id))).scalar_one()
        assert await repo.find_conflicts(task_id, DecisionStage.TITLE_ABSTRACT) == []

    async def test_conflict_uses_latest_decision_per_reviewer(self, session: AsyncSession) -> None:
        """冲突判定用**每人最新**决策：改主意后一致了就不算冲突。"""
        _, record_ids, reviewer_a, reviewer_b = await _seed(session)
        repo = ScreeningRepository(session)

        await repo.append_decision(
            record_ids[0],
            stage=DecisionStage.TITLE_ABSTRACT,
            verdict=ScreeningVerdict.EXCLUDE,
            exclusion_reason="first thought",
            reviewer_id=reviewer_a,
        )
        await repo.append_decision(
            record_ids[0],
            stage=DecisionStage.TITLE_ABSTRACT,
            verdict=ScreeningVerdict.EXCLUDE,
            exclusion_reason="off topic",
            reviewer_id=reviewer_b,
        )
        # A 改主意 → 与 B 一致（都是 exclude）
        await repo.append_decision(
            record_ids[0],
            stage=DecisionStage.TITLE_ABSTRACT,
            verdict=ScreeningVerdict.EXCLUDE,
            exclusion_reason="changed mind",
            reviewer_id=reviewer_a,
        )
        await session.commit()

        task_id = (await session.execute(select(ScreeningRecord.task_id))).scalar_one()
        assert await repo.find_conflicts(task_id, DecisionStage.TITLE_ABSTRACT) == []


class TestRecordQueries:
    async def test_list_records_filters_by_stage(self, session: AsyncSession) -> None:
        task_id, record_ids, _, _ = await _seed(session, titles=("T1", "T2"))
        repo = ScreeningRepository(session)

        # 把第二条推进到全文阶段
        record = await repo.get(record_ids[1])
        assert record is not None
        record.current_stage = DecisionStage.FULLTEXT.value
        await session.commit()

        title_stage = await repo.list_records(task_id, stage=DecisionStage.TITLE_ABSTRACT)
        fulltext_stage = await repo.list_records(task_id, stage=DecisionStage.FULLTEXT)

        assert [row.id for row in title_stage] == [record_ids[0]]
        assert [row.id for row in fulltext_stage] == [record_ids[1]]

    async def test_list_records_respects_limit(self, session: AsyncSession) -> None:
        task_id, _, _, _ = await _seed(session, titles=tuple(f"T{i}" for i in range(5)))
        repo = ScreeningRepository(session)

        assert len(await repo.list_records(task_id, limit=2)) == 2

    async def test_list_records_scoped_to_task(self, session: AsyncSession) -> None:
        await _seed(session)
        repo = ScreeningRepository(session)

        assert await repo.list_records(uuid.uuid4()) == []

    async def test_decision_count_matches_appends(self, session: AsyncSession) -> None:
        """确认确实是追加而非覆盖（直接数行数）。"""
        _, record_ids, reviewer_a, _ = await _seed(session)
        repo = ScreeningRepository(session)

        for _ in range(3):
            await repo.append_decision(
                record_ids[0],
                stage=DecisionStage.TITLE_ABSTRACT,
                verdict=ScreeningVerdict.MAYBE,
                reviewer_id=reviewer_a,
            )
        await session.commit()

        total = (
            await session.execute(select(func.count()).select_from(ScreeningDecision))
        ).scalar_one()
        assert total == 3

    async def test_list_records_is_stable_when_created_at_ties(self, session: AsyncSession) -> None:
        """`created_at` 并列时，列表顺序必须**稳定**（同一次检索的多条记录）。

        真实缺陷（Task 7 浏览器验收暴露）：一次检索把 3 条记录写在**同一个事务**里，
        `created_at` 由 `server_default now()` 生成 → 三条时间戳完全相同。
        而 `list_records` 只按 `created_at` 排序，PostgreSQL 对并列行返回**任意顺序**：
        用户每次刷新看到的候选顺序都可能变，正在筛选的人会找不到刚才那一条；
        浏览器 E2E 也因「按位置取第 N 行」而在刷新后指到别的记录。

        这与 Task 2 给 `screening_decision` 加 `seq` 是同一类问题。

        **为什么用 SQL 结构断言而不是只比两次查询结果**：SQLite 对并列行恰好按
        rowid（插入顺序）返回，两次查询必然一致 —— 行为断言在这里是**假测试**，
        把排序键改回去也照样绿（AGENTS.md #13）。真正能抓住回归的是「排序键必须
        含唯一列」这一结构事实。
        """
        task_id, record_ids, _, _ = await _seed(session, titles=("T1", "T2", "T3"))

        repo = ScreeningRepository(session)
        stmt = repo.list_records_stmt(task_id)
        ordered = list(stmt._order_by_clauses)

        # 1) 排序键必须显式含 id（唯一列），并列时才不会退化成「数据库随便给」
        rendered = " ".join(str(clause) for clause in ordered)
        assert "screening_record.id" in rendered, f"排序键缺少唯一列 id：{rendered}"

        # 2) created_at 仍是主排序键（按入库先后展示）
        assert "screening_record.created_at" in rendered

        # 3) 行为兜底：强制并列后重复查询结果一致
        tied = datetime(2026, 10, 21, 3, 0, 0, tzinfo=UTC)
        for record_id in record_ids:
            record = await session.get(ScreeningRecord, record_id)
            assert record is not None
            record.created_at = tied
        await session.commit()

        first = [row.id for row in await repo.list_records(task_id)]
        second = [row.id for row in await repo.list_records(task_id)]
        assert first == second
