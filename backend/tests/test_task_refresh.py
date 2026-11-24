"""增量刷新与定时重跑（Issue #27）。

锁定的口径：
1. 检索式优先取任务计划（用户确认过的那份），缺失才退回 config；
2. 去重是「DOI → source_id → 归一化题名」多键任一命中，且跨检索式也去重；
3. 摘要的编号必须能被引用一致性闸门接受；模型给的编号越界要回落，而不是原样落库；
4. 刷新只把**新增**记录写进 screening（否则筛选工作台每周被同一批文献刷屏）；
5. 排期按 weekly/monthly 推进，月度要夹到当月最后一天；
6. 刷新不扣「篇数」，但状态/冷却/归属校验不能松。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.db.models  # noqa: F401  确保 metadata 完整
from app.db.base import Base
from app.db.models.routine import TaskRefresh, TaskRoutine
from app.db.models.screening import ScreeningRecord
from app.db.models.task import Task
from app.orchestration.services.routine_scheduler import RoutineScheduler, advance
from app.orchestration.services.task_refresh import (
    SUMMARY_SYSTEM,
    RefreshRunner,
    audit_bullets,
    collect_queries,
    compose_summary,
    is_relevant,
    normalize_title,
    record_keys,
    relevance_terms,
)
from app.tools.literature.models import LiteratureRecord

CFG = SimpleNamespace(
    max_queries=5,
    max_results_per_query=20,
    max_new_per_run=30,
    overlap_days=7,
    manual_cooldown_seconds=600,
    batch_size=5,
    tick_seconds=60,
    enabled=True,
    default_cadence="weekly",
)


@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


def record(
    n: int,
    *,
    title: str | None = None,
    doi: str | None = None,
    source: str = "arxiv",
) -> LiteratureRecord:
    return LiteratureRecord(
        source_id=f"{source}:{n}",
        title=title or f"GNN paper {n}",
        authors=["张三", "李四"],
        abstract=f"graph neural network abstract {n}",
        published_at=datetime(2026, 1, n, tzinfo=UTC),
        source_url=doi or f"https://arxiv.org/abs/{n}",
        pdf_url=f"https://arxiv.org/pdf/{n}",
    )


async def make_task(
    factory, *, status: str = "completed", plan=None, config=None, review_report=None
) -> Task:
    async with factory() as session:
        task = Task(
            user_id=uuid4(),
            title="图神经网络综述",
            description="d",
            task_type="literature_review",
            status=status,
            config=config or {"topic": "图神经网络"},
            plan=plan,
            review_report=review_report,
            completed_at=datetime(2026, 11, 1, tzinfo=UTC),
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        return task


async def make_refresh(factory, task: Task, **overrides) -> TaskRefresh:
    async with factory() as session:
        refresh = TaskRefresh(
            task_id=task.id,
            user_id=task.user_id,
            trigger=overrides.pop("trigger", "manual"),
            status=overrides.pop("status", "running"),
            window_from=overrides.pop("window_from", datetime(2026, 10, 1, tzinfo=UTC)),
            window_to=overrides.pop("window_to", datetime(2026, 11, 20, tzinfo=UTC)),
            queries=[],
            started_at=datetime(2026, 11, 20, tzinfo=UTC),
            **overrides,
        )
        session.add(refresh)
        await session.commit()
        await session.refresh(refresh)
        return refresh


class _Source:
    def __init__(self, records: list[LiteratureRecord]) -> None:
        self._records = records
        self.calls: list[str] = []

    async def search(self, search):
        self.calls.append(search.query)
        return list(self._records)

    async def aclose(self) -> None:
        return None


class _Usage:
    total_tokens = 120


class _Response:
    def __init__(self, content: str) -> None:
        self.content = content
        self.usage = _Usage()


# ===== 纯函数 =====


def test_collect_queries_prefers_plan_and_dedupes() -> None:
    task = SimpleNamespace(
        plan={"search_queries": ["GNN survey", "gnn survey", "graph retrieval"]},
        config={"topic": "图神经网络", "keywords": ["GNN", "retrieval"]},
        title="标题",
    )
    assert collect_queries(task, limit=5) == ["GNN survey", "graph retrieval"]


def test_collect_queries_falls_back_to_config() -> None:
    task = SimpleNamespace(
        plan=None, config={"topic": "图神经网络", "keywords": ["GNN", "RAG"]}, title="t"
    )
    assert collect_queries(task, limit=5) == ["图神经网络", "GNN RAG"]


def test_record_keys_cover_doi_source_id_title() -> None:
    item = record(1, doi="https://doi.org/10.1000/ABC")
    keys = record_keys(item)
    assert "doi:10.1000/abc" in keys
    assert "sid:arxiv:1" in keys
    assert f"title:{normalize_title('GNN paper 1')}" in keys


def test_relevance_terms_drops_stopwords_and_short_tokens() -> None:
    terms = relevance_terms(
        keywords=["检索增强生成"],
        queries=["retrieval augmented generation evaluation", "RAG 评估基准 数据集"],
    )
    assert "检索增强生成" in terms
    assert "retrieval" in terms and "augmented" in terms
    # 虚词与过长检索式里的噪音词不该进词表
    assert "evaluation" not in terms
    assert "the" not in terms


def test_is_relevant_rejects_off_topic_paper() -> None:
    terms = relevance_terms(keywords=["检索增强生成"], queries=["retrieval augmented generation"])
    on_topic = record(1, title="Retrieval augmented generation for evidence tracing")
    off_topic = record(2, title="Carotid plaque assessment with contrast ultrasound")
    assert is_relevant(on_topic, terms) is True
    assert is_relevant(off_topic, terms) is False


def test_compose_summary_cites_every_entry() -> None:
    summary = compose_summary(
        bullets="- 新证据补上了跨域迁移的对照实验 [E1]",
        records=[record(1), record(2)],
        window_from=datetime(2026, 10, 1, tzinfo=UTC),
        window_to=datetime(2026, 11, 20, tzinfo=UTC),
        hits_total=9,
        duplicate_count=7,
    )
    assert "[E1]" in summary and "[E2]" in summary
    assert "新增 2 篇" in summary
    assert "与既有记录重复 7 条" in summary


def test_audit_bullets_rejects_out_of_range_index() -> None:
    ok, reason = audit_bullets("- 结论 [E5]", index_count=2)
    assert ok is False
    assert "E5" in reason or "5" in reason


def test_audit_bullets_accepts_subset() -> None:
    ok, _ = audit_bullets("- 结论 [E1]", index_count=2)
    assert ok is True


def test_advance_monthly_clamps_to_month_end() -> None:
    base = datetime(2026, 1, 31, 9, 0, tzinfo=UTC)
    assert advance("monthly", base) == datetime(2026, 2, 28, 9, 0, tzinfo=UTC)
    assert advance("monthly", datetime(2026, 12, 15, tzinfo=UTC)) == datetime(
        2027, 1, 15, tzinfo=UTC
    )
    assert advance("weekly", base) == datetime(2026, 2, 7, 9, 0, tzinfo=UTC)


# ===== 运行器 =====


def test_refresh_run_dedupes_and_records_only_new(factory) -> None:
    async def scenario():
        task = await make_task(factory, plan={"search_queries": ["GNN survey"]})
        # 已有记录：Paper 2 在库里（题名命中）
        async with factory() as session:
            session.add(
                ScreeningRecord(
                    search_log_id=uuid4(),
                    task_id=task.id,
                    source="arxiv",
                    source_id="arxiv:2",
                    title="Paper 2",
                    authors=[],
                    abstract="",
                )
            )
            await session.commit()
        refresh = await make_refresh(factory, task)
        source = _Source([record(1), record(2), record(3)])
        runner = RefreshRunner(
            session_factory=factory,
            source_factory=lambda _ctx: _as_coroutine(source),
            chat=lambda *a, **k: _as_coroutine(_Response("- 新证据 [E1]\n- 又一个 [E2]")),
            config=CFG,
        )
        outcome = await runner.run(refresh.id)

        assert outcome.status == "completed"
        assert outcome.hits_total == 3
        assert outcome.duplicate_count == 1
        assert outcome.new_count == 2
        assert outcome.citation_ok is True
        assert "新增 2 篇" in (outcome.summary_markdown or "")

        async with factory() as session:
            stored = await session.get(TaskRefresh, refresh.id)
            assert stored.status == "completed"
            assert stored.new_count == 2
            assert stored.tokens_used == 120
            rows = list(
                (
                    await session.execute(
                        select(ScreeningRecord).where(ScreeningRecord.task_id == task.id)
                    )
                ).scalars()
            )
        # 库里原有一条 + 新增两条；Paper 2 不会被重复写入
        assert len(rows) == 3
        assert sorted(row.source_id for row in rows) == ["arxiv:1", "arxiv:2", "arxiv:3"]

    asyncio.run(scenario())


def test_refresh_filters_off_topic_new_records(factory) -> None:
    """窗口内的「新」不等于「相关」：跑题的不能进摘要。"""

    async def scenario():
        task = await make_task(factory, plan={"search_queries": ["GNN survey"]})
        refresh = await make_refresh(factory, task)
        runner = RefreshRunner(
            session_factory=factory,
            source_factory=lambda _ctx: _as_coroutine(
                _Source([record(1), record(2, title="Carotid plaque ultrasound scoring")])
            ),
            chat=lambda *a, **k: _as_coroutine(_Response("- 新证据 [E1]")),
            config=CFG,
        )
        outcome = await runner.run(refresh.id)

        assert outcome.new_count == 1
        assert outcome.filtered_count == 1
        assert "与主题无关已过滤" in (outcome.summary_markdown or "")

    asyncio.run(scenario())


def test_refresh_falls_back_when_model_cites_unknown_index(factory) -> None:
    async def scenario():
        task = await make_task(factory, plan={"search_queries": ["GNN survey"]})
        refresh = await make_refresh(factory, task)
        runner = RefreshRunner(
            session_factory=factory,
            source_factory=lambda _ctx: _as_coroutine(_Source([record(1)])),
            chat=lambda *a, **k: _as_coroutine(_Response("- 引用了不存在的编号 [E9]")),
            config=CFG,
        )
        outcome = await runner.run(refresh.id)

        assert outcome.status == "completed"
        assert outcome.citation_ok is False
        summary = outcome.summary_markdown or ""
        assert "E9" not in summary
        assert "[E1]" in summary

    asyncio.run(scenario())


def test_refresh_without_new_records_says_so(factory) -> None:
    async def scenario():
        task = await make_task(factory, plan={"search_queries": ["GNN survey"]})
        refresh = await make_refresh(factory, task)
        runner = RefreshRunner(
            session_factory=factory,
            source_factory=lambda _ctx: _as_coroutine(_Source([])),
            chat=lambda *a, **k: _as_coroutine(_Response("- 不应被调用")),
            config=CFG,
        )
        outcome = await runner.run(refresh.id)
        assert outcome.new_count == 0
        assert "没有检索到新文献" in (outcome.summary_markdown or "")

    asyncio.run(scenario())


def test_refresh_retries_once_when_all_queries_fail(factory) -> None:
    """上游限流（arXiv 429）导致全失败时，等一会儿重试一次再判定。"""

    class _FlakySource:
        def __init__(self) -> None:
            self.calls = 0

        async def search(self, search):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("arXiv 限流（429），请稍后重试")
            return [record(1)]

        async def aclose(self) -> None:
            return None

    async def scenario():
        task = await make_task(factory, plan={"search_queries": ["GNN survey"]})
        refresh = await make_refresh(factory, task)
        source = _FlakySource()
        runner = RefreshRunner(
            session_factory=factory,
            source_factory=lambda _ctx: _as_coroutine(source),
            chat=lambda *a, **k: _as_coroutine(_Response("- 新证据 [E1]")),
            config=CFG,
            retry_delay=0,
        )
        outcome = await runner.run(refresh.id)
        assert outcome.status == "completed"
        assert outcome.new_count == 1
        assert source.calls == 2

    asyncio.run(scenario())


def test_refresh_loads_and_offloads_routine_memory_brief(factory) -> None:
    """Issue #42：LOAD 不改 SUMMARY_SYSTEM（保前缀缓存），user 含记忆，结束落库更新 memory_brief。"""

    async def scenario():
        task = await make_task(
            factory,
            plan={"search_queries": ["GNN survey", "empty gnn query"]},
            review_report={
                "final_markdown": "# 图神经网络综述\n\n原综述总结了静态图基准的表达能力。",
                "evidence_matrix": {
                    "questions": ["静态图表现如何？", "动态图长时延是否缺少评估？"],
                    "cells": [
                        {"question_index": 0, "bucket": "strong", "claim_ids": ["c1"]},
                        {"question_index": 1, "bucket": "gap", "claim_ids": []},
                    ],
                },
            },
        )
        async with factory() as session:
            session.add(
                TaskRoutine(
                    task_id=task.id,
                    user_id=task.user_id,
                    enabled=True,
                    cadence="weekly",
                    notify="email",
                    next_run_at=datetime(2026, 11, 27, tzinfo=UTC),
                )
            )
            await session.commit()

        captured_messages: list[list] = []

        async def fake_chat(messages, **_kwargs):
            captured_messages.append(messages)
            return _Response("- 新工作补齐了动态图长时延对照实验 [E1]")

        class _PerQuerySource:
            async def search(self, search):
                if search.query == "GNN survey":
                    return [record(1)]
                return []

            async def aclose(self) -> None:
                return None

        refresh1 = await make_refresh(factory, task)
        runner = RefreshRunner(
            session_factory=factory,
            source_factory=lambda _ctx: _as_coroutine(_PerQuerySource()),
            chat=fake_chat,
            config=CFG,
        )
        outcome1 = await runner.run(refresh1.id)
        assert outcome1.status == "completed"
        assert len(captured_messages) == 1
        # 严格保住前缀缓存：SUMMARY_SYSTEM 原样不变
        assert captured_messages[0][0].content == SUMMARY_SYSTEM
        user_prompt = captured_messages[0][1].content
        assert "已有研究状态" in user_prompt
        assert "动态图长时延是否缺少评估？" in user_prompt

        async with factory() as session:
            routine = (
                await session.execute(select(TaskRoutine).where(TaskRoutine.task_id == task.id))
            ).scalar_one()
            brief1 = routine.memory_brief
            assert brief1 is not None
            assert brief1["open_gaps"] == ["动态图长时延是否缺少评估？"]
            assert brief1["recent_updates"] == ["新工作补齐了动态图长时延对照实验 [E1]"]
            assert brief1["query_empty_streaks"]["GNN survey"] == 0
            assert brief1["query_empty_streaks"]["empty gnn query"] == 1
            assert brief1["stale_queries"] == []

        # 再跑两期空命中，确认 empty gnn query 连续 3 期 0 新增后进入 stale_queries
        for _ in range(2):
            r_next = await make_refresh(factory, task)
            await runner.run(r_next.id)

        async with factory() as session:
            routine = (
                await session.execute(select(TaskRoutine).where(TaskRoutine.task_id == task.id))
            ).scalar_one()
            assert routine.memory_brief is not None
            assert "empty gnn query" in routine.memory_brief["stale_queries"]

    asyncio.run(scenario())


# ===== 调度器 =====


def test_scheduler_claims_due_routine_and_advances(factory) -> None:
    async def scenario():
        task = await make_task(factory)
        async with factory() as session:
            routine = TaskRoutine(
                task_id=task.id,
                user_id=task.user_id,
                enabled=True,
                cadence="weekly",
                notify="email",
                next_run_at=datetime(2026, 11, 1, tzinfo=UTC),
            )
            session.add(routine)
            await session.commit()

        started: list[tuple] = []

        async def starter(task_id, user_id):
            started.append((task_id, user_id))

        scheduler = RoutineScheduler(session_factory=factory, starter=starter, config=CFG)
        claimed = await scheduler.tick()
        assert claimed == [task.id]

        async with factory() as session:
            row = (
                await session.execute(select(TaskRoutine).where(TaskRoutine.task_id == task.id))
            ).scalar_one()
            assert row.last_run_at is not None
            assert row.next_run_at > row.last_run_at

        # 下一次扫描：还没到期，不应再启动
        assert await scheduler.tick() == []
        assert len(started) == 1

    asyncio.run(scenario())


def test_scheduler_skips_disabled_routine(factory) -> None:
    async def scenario():
        task = await make_task(factory)
        async with factory() as session:
            session.add(
                TaskRoutine(
                    task_id=task.id,
                    user_id=task.user_id,
                    enabled=False,
                    cadence="weekly",
                    notify="none",
                    next_run_at=datetime(2026, 11, 1, tzinfo=UTC),
                )
            )
            await session.commit()

        scheduler = RoutineScheduler(session_factory=factory, starter=_never, config=CFG)
        assert await scheduler.tick() == []

    asyncio.run(scenario())


async def _never(*_args) -> None:
    raise AssertionError("不应启动刷新")


def _as_coroutine(value):
    async def _inner():
        return value

    return _inner()
