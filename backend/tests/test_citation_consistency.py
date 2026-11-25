"""引用一致性闸门（Issue #25 沉淀项 1，关联 #17）。

## 锁定的行为

1. 正文 `[E#]` ↔ 参考文献 `index` **双向**对齐；缺条目、孤儿条目、条目缺 index
   都判不通过；
2. 正文与参考文献同时为空 → 通过（纯说明性输出合法）；
3. 闸门失败：任务 REVIEWING → FAILED（可 `replan` 重试），**不写** review_report /
   artifacts，并留一条 `task_failed` 事件说明原因；
4. 闸门通过：任务状态不变，由调用方继续导出。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.db.models  # noqa: F401
from app.db.base import Base
from app.db.models.log import ExecutionLog
from app.db.models.task import Task
from app.orchestration.services.task_execution import TaskExecution
from app.orchestration.validation import audit_citation_alignment

# ===== 纯函数：审计语义 =====


def _entry(index: int, title: str = "t") -> dict[str, object]:
    return {"index": index, "title": title, "authors": ["a"], "year": 2024}


def test_aligned_passes() -> None:
    audit = audit_citation_alignment("正文 [E1] 与 [E2]。", [_entry(1), _entry(2)])
    assert audit.ok and audit.cited == (1, 2) and audit.indexed == (1, 2)


def test_missing_entry_fails() -> None:
    audit = audit_citation_alignment("正文 [E1] 与 [E3]。", [_entry(1), _entry(2)])
    assert not audit.ok
    assert audit.missing_entries == (3,)
    assert "不存在" in (audit.reason or "")


def test_orphan_entry_fails() -> None:
    audit = audit_citation_alignment("正文只引用 [E1]。", [_entry(1), _entry(2)])
    assert not audit.ok
    assert audit.orphan_entries == (2,)
    assert "未被正文引用" in (audit.reason or "")


def test_entry_without_index_fails() -> None:
    audit = audit_citation_alignment("正文 [E1]。", [{"title": "缺编号"}])
    assert not audit.ok
    assert "index" in (audit.reason or "")


def test_both_empty_passes() -> None:
    assert audit_citation_alignment("没有引用的说明文。", []).ok
    assert audit_citation_alignment("", []).ok


def test_citations_without_bibliography_fails() -> None:
    audit = audit_citation_alignment("正文 [E1] [E2]。", [])
    assert not audit.ok
    assert audit.missing_entries == (1, 2)


def test_bibliography_without_citations_fails() -> None:
    audit = audit_citation_alignment("正文没有引用。", [_entry(1)])
    assert not audit.ok
    assert audit.orphan_entries == (1,)


def test_duplicate_citations_are_deduped() -> None:
    audit = audit_citation_alignment("[E1] 与再引 [E1]。", [_entry(1)])
    assert audit.ok and audit.cited == (1,)


# ===== 闸门接线：状态迁移与事件 =====


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


class _Harness(TaskExecution):
    """只测闸门：结算/指标打点用桩，避免依赖配额与观测容器。"""

    def __init__(self, session_factory) -> None:
        super().__init__(SimpleNamespace(), session_factory=session_factory, quota_service=None)
        self.settled: list[uuid.UUID] = []
        self.published = 0

    async def settle_quota(self, task_id: uuid.UUID) -> None:  # type: ignore[override]
        self.settled.append(task_id)

    async def _record_task_terminal(self, status, value) -> None:  # type: ignore[override]
        return None

    async def _publish(self, event) -> None:  # type: ignore[override]
        self.published += 1


async def _reviewing_task(factory) -> Task:
    async with factory() as session:
        task = Task(
            user_id=uuid.uuid4(),
            title="闸门用例",
            description="d",
            task_type="literature_review",
            status="reviewing",
            config={},
            created_at=datetime.now(UTC),
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        return task


async def test_guard_fails_task_and_writes_no_output(factory) -> None:
    task = await _reviewing_task(factory)
    harness = _Harness(factory)

    ok = await harness._guard_citation_consistency(
        task.id,
        {"final_markdown": "正文 [E1] [E9]。", "bibliography": [_entry(1)]},
    )
    assert ok is False

    async with factory() as session:
        row = await session.get(Task, task.id)
        assert row is not None
        assert row.status == "failed"
        assert row.review_report is None, "校验不过绝不能落库复盘报告"
        assert list(row.artifacts or []) == [], "校验不过绝不能落库产物"
        events = (
            (await session.execute(select(ExecutionLog).where(ExecutionLog.task_id == task.id)))
            .scalars()
            .all()
        )
        failed_events = [e for e in events if e.event_type == "task_failed"]
        assert failed_events, "必须留一条 task_failed 事件"
        assert "引用一致性" in str(failed_events[0].context)
    assert harness.settled == [task.id]


async def test_guard_passes_and_leaves_task_untouched(factory) -> None:
    task = await _reviewing_task(factory)
    harness = _Harness(factory)

    ok = await harness._guard_citation_consistency(
        task.id,
        {"final_markdown": "正文 [E1] [E2]。", "bibliography": [_entry(1), _entry(2)]},
    )
    assert ok is True

    async with factory() as session:
        row = await session.get(Task, task.id)
        assert row is not None and row.status == "reviewing"
        assert harness.settled == []


async def test_guard_fails_task_populates_snapshot_error_and_replan_clears_it(factory) -> None:
    from app.orchestration.contracts import TaskCommand, TaskControlRequest

    task = await _reviewing_task(factory)
    harness = _Harness(factory)

    ok = await harness._guard_citation_consistency(
        task.id,
        {"final_markdown": "正文 [E1] [E9]。", "bibliography": [_entry(1)]},
    )
    assert ok is False

    snap = await harness.get_snapshot(task.user_id, task.id)
    assert snap.status == "failed"
    assert snap.error is not None and "引用一致性" in snap.error
    assert snap.waiting_reason is None

    # replan 必须清空旧 error（防止新规划期间误显示旧失败原因）
    harness.container.track = lambda _tid, coro, **_kwargs: coro.close()
    replan_snap = await harness.control(
        task.user_id, task.id, TaskControlRequest(command=TaskCommand.REPLAN)
    )
    assert replan_snap.status == "planning"
    assert replan_snap.error is None
    assert replan_snap.waiting_reason is None
