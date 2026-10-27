"""`on_result` 必须把校验结论写入 `validation_result` 表（2026-10-27）。

这是「假绿」防线（AGENTS §5 #33/#34）：
Repository 单测全绿，但 `DatabaseScheduleObserver.on_result` 不调用它的话，
表照样是空的 —— 缺陷原样存在。

`on_result` 原来只把校验塞进 `subtask.output_data` 的 JSON，没有结构化落库。
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.db.models  # noqa: F401  确保全部 ORM 注册到 Base.metadata
from app.db.base import Base
from app.db.models.log import ValidationResult
from app.db.models.task import Subtask, Task
from app.orchestration.persistence import DatabaseScheduleObserver
from app.orchestration.scheduler import NodeResult
from app.orchestration.task_state import SubtaskStatus


class _RecordingPublisher:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.events.append(event)


def _report(*, passed: bool = False, score: float = 96.0, feedback: str = "长度超限") -> dict:
    """生产形状（照抄 `ValidationReport`，AGENTS §5 #33）。"""
    return {
        "passed": passed,
        "score": score,
        "checks": [{"name": "rules", "passed": passed, "message": feedback, "advisory": False}],
        "retryable": True,
        "feedback": feedback,
    }


async def _setup() -> tuple[Any, async_sessionmaker[Any], uuid.UUID, uuid.UUID]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    task_id = uuid.uuid4()
    subtask_id = uuid.uuid4()
    async with session_factory() as session:
        session.add(
            Task(
                id=task_id,
                user_id=uuid.uuid4(),
                title="落库任务",
                description="测试",
                status="running",
            )
        )
        session.add(
            Subtask(
                id=subtask_id,
                task_id=task_id,
                node_id="n7",
                title="校验节点",
                description="描述",
                status="running",
            )
        )
        await session.commit()
    return engine, session_factory, task_id, subtask_id


class TestOnResultPersistsValidation:
    async def test_validation_report_written_to_table(self) -> None:
        engine, session_factory, task_id, subtask_id = await _setup()
        try:
            observer = DatabaseScheduleObserver(
                session_factory=session_factory, publisher=_RecordingPublisher()
            )
            await observer.on_result(
                str(task_id),
                NodeResult(
                    node_id="n7",
                    status=SubtaskStatus.COMPLETED,
                    output={"result": "ok"},
                    validation=_report(),
                    attempts=1,
                ),
            )

            async with session_factory() as session:
                rows = (await session.execute(select(ValidationResult))).scalars().all()
                assert len(rows) == 1
                assert rows[0].subtask_id == subtask_id
                assert rows[0].passed is False
                assert rows[0].score == pytest.approx(0.96)
                assert rows[0].report == "长度超限"
        finally:
            await engine.dispose()

    async def test_no_validation_means_no_row(self) -> None:
        """没有校验结果的节点（如 search）不该产生空行。"""
        engine, session_factory, task_id, _subtask_id = await _setup()
        try:
            observer = DatabaseScheduleObserver(
                session_factory=session_factory, publisher=_RecordingPublisher()
            )
            await observer.on_result(
                str(task_id),
                NodeResult(
                    node_id="n7",
                    status=SubtaskStatus.COMPLETED,
                    output={"result": "ok"},
                    validation=None,
                    attempts=1,
                ),
            )

            async with session_factory() as session:
                rows = (await session.execute(select(ValidationResult))).scalars().all()
                assert rows == []
        finally:
            await engine.dispose()

    async def test_output_data_still_keeps_validation(self) -> None:
        """落库是**新增**，不能删掉 `output_data` 里原有的校验（向后兼容）。"""
        engine, session_factory, task_id, subtask_id = await _setup()
        try:
            observer = DatabaseScheduleObserver(
                session_factory=session_factory, publisher=_RecordingPublisher()
            )
            report = _report()
            await observer.on_result(
                str(task_id),
                NodeResult(
                    node_id="n7",
                    status=SubtaskStatus.COMPLETED,
                    output={"result": "ok"},
                    validation=report,
                    attempts=1,
                ),
            )

            async with session_factory() as session:
                subtask = await session.get(Subtask, subtask_id)
                assert subtask is not None
                assert subtask.output_data["validation"] == report
        finally:
            await engine.dispose()

    async def test_persist_failure_does_not_break_result(self) -> None:
        """落库失败不能中断执行 —— 与既有失败策略一致。"""
        engine, session_factory, task_id, subtask_id = await _setup()
        try:
            observer = DatabaseScheduleObserver(
                session_factory=session_factory, publisher=_RecordingPublisher()
            )
            # 形状异常的校验报告：不应抛异常
            await observer.on_result(
                str(task_id),
                NodeResult(
                    node_id="n7",
                    status=SubtaskStatus.COMPLETED,
                    output={"result": "ok"},
                    validation={"passed": "not-a-bool", "score": "abc"},  # type: ignore[dict-item]
                    attempts=1,
                ),
            )
            async with session_factory() as session:
                subtask = await session.get(Subtask, subtask_id)
                assert subtask is not None
                assert subtask.output_data is not None
        finally:
            await engine.dispose()
