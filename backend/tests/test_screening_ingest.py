"""检索结果 → 筛选表（PRISMA「识别」阶段）。

## 锁什么

线上实测：任务跑完检索，筛选页仍是「还没有候选记录」、PRISMA 全 0。
根因是检索结果只落在节点 artifacts 里，`screening_record` 从来没写过 ——
功能代码齐全，但没有任何入口触发它，等于没启用。

这里锁三件事：
1. 检索节点执行后**真的会**调用落库回调；
2. 同一任务同一检索式**只写一次**（重试/重生成不能把 PRISMA 数字翻倍）；
3. 落库失败**不影响检索结果**（筛选是附加能力，不能拖垮主流程）。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.db.models  # noqa: F401  确保全部 ORM 注册到 Base.metadata
from app.db.base import Base
from app.db.models.screening import ScreeningRecord, SearchLog
from app.knowledge.services.screening_service import ScreeningService
from app.orchestration.contracts import ExecutionResult, PlanNode
from app.orchestration.services.task_execution import TaskExecution
from app.orchestration.workflows.literature_review import LiteratureReviewWorkflow
from app.tools.literature.models import LiteratureRecord


@pytest.fixture
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield maker
    await engine.dispose()


def _record(source_id: str, title: str) -> LiteratureRecord:
    return LiteratureRecord(
        source_id=source_id,
        title=title,
        authors=["Ada"],
        abstract="摘要",
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
        source_url="https://example.com/paper",
        pdf_url="https://example.com/paper.pdf",
    )


class _FakeSource:
    """检索源替身：记录调用次数，便于断言「失败也只检索一次」。"""

    def __init__(self, records: list[LiteratureRecord]) -> None:
        self._records = records
        self.calls = 0

    async def search(self, request: Any) -> list[LiteratureRecord]:
        self.calls += 1
        return self._records


def _node() -> PlanNode:
    return PlanNode(
        id="search",
        type="search",
        title="检索",
        input={"query": "RAG 证据核验", "max_results": 5},
    )


async def _search_once(
    recorder: Any,
    records: list[LiteratureRecord],
    task_id: uuid.UUID,
) -> tuple[ExecutionResult, _FakeSource]:
    workflow = LiteratureReviewWorkflow(source=_FakeSource(records), screening_recorder=recorder)
    task = SimpleNamespace(id=task_id, title="文献综述")
    result = await workflow._execute_search(task, _node(), None)
    return result, workflow._source  # type: ignore[return-value]


async def _counts(factory: async_sessionmaker[AsyncSession]) -> tuple[int, int]:
    async with factory() as session:
        logs = (await session.execute(select(func.count()).select_from(SearchLog))).scalar_one()
        items = (
            await session.execute(select(func.count()).select_from(ScreeningRecord))
        ).scalar_one()
    return int(logs), int(items)


class TestSearchWritesScreening:
    async def test_search_node_records_screening(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """检索节点跑完，筛选表里就该有候选记录与检索日志。"""
        task_id = uuid.uuid4()
        host = SimpleNamespace(_session_factory=factory)

        async def recorder(tid: uuid.UUID, query: str, records: list[LiteratureRecord]) -> None:
            await TaskExecution._record_screening(host, tid, query, records)  # type: ignore[arg-type]

        result, source = await _search_once(
            recorder,
            [_record("arxiv:1", "论文一"), _record("arxiv:2", "论文二")],
            task_id,
        )

        assert isinstance(result, ExecutionResult)
        assert source.calls == 1
        assert await _counts(factory) == (1, 2)

        async with factory() as session:
            service = ScreeningService(session)
            assert await service.has_search_log(task_id, "RAG 证据核验")

    async def test_same_query_is_idempotent(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """重跑检索节点不能把 PRISMA 的「识别」翻倍。"""
        task_id = uuid.uuid4()
        host = SimpleNamespace(_session_factory=factory)

        async def recorder(tid: uuid.UUID, query: str, records: list[LiteratureRecord]) -> None:
            await TaskExecution._record_screening(host, tid, query, records)  # type: ignore[arg-type]

        records = [_record("arxiv:1", "论文一")]
        await _search_once(recorder, records, task_id)
        await _search_once(recorder, records, task_id)

        assert await _counts(factory) == (1, 1)

    async def test_different_query_creates_second_log(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """不同检索式是新的「识别」批次，不能被幂等键误吞。"""
        task_id = uuid.uuid4()
        host = SimpleNamespace(_session_factory=factory)

        async def recorder(tid: uuid.UUID, query: str, records: list[LiteratureRecord]) -> None:
            await TaskExecution._record_screening(host, tid, query, records)  # type: ignore[arg-type]

        workflow = LiteratureReviewWorkflow(
            source=_FakeSource([_record("arxiv:1", "论文一")]),
            screening_recorder=recorder,
        )
        task = SimpleNamespace(id=task_id, title="文献综述")
        await workflow._execute_search(task, _node(), None)

        other = PlanNode(
            id="search2",
            type="search",
            title="检索二",
            input={"query": "另一个检索式", "max_results": 5},
        )
        await workflow._execute_search(task, other, None)

        assert await _counts(factory) == (2, 2)


class TestFailureIsolation:
    async def test_recorder_failure_does_not_break_search(self) -> None:
        """落库炸了也要返回检索结果，并且不重试检索源。"""

        async def broken(tid: uuid.UUID, query: str, records: list[LiteratureRecord]) -> None:
            raise RuntimeError("db down")

        result, source = await _search_once(broken, [_record("arxiv:1", "论文一")], uuid.uuid4())

        assert "论文一" in result.content
        assert source.calls == 1

    async def test_no_recorder_still_runs(self) -> None:
        """未注入 recorder（测试/实验）时检索照常。"""
        result, source = await _search_once(None, [_record("arxiv:1", "论文一")], uuid.uuid4())
        assert "论文一" in result.content
        assert source.calls == 1

    async def test_empty_result_skips_recorder(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """空检索不写空日志：否则 PRISMA 会多出一条「识别 0 条」的批次。"""
        called: list[str] = []

        async def recorder(tid: uuid.UUID, query: str, records: list[LiteratureRecord]) -> None:
            called.append(query)

        await _search_once(recorder, [], uuid.uuid4())
        assert called == []
