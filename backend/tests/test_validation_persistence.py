"""校验结论必须落库（2026-10-27）。

## 缺陷

`validation_result` 表有 `passed` / `score` / `rule_results` / `llm_report` /
`report` 五列，但**一条记录都没有** —— 全 `app/` 里没有任何代码实例化
`ValidationResult`。校验结论只存在 `subtask.output_data` 的 JSON 里。

## 后果

| 场景 | 影响 |
| --- | --- |
| 排查「为什么转人工」 | 只能逐个任务拉 API 快照，无法按失败原因聚合 |
| 统计校验通过率 | 做不到（`passed` 列形同虚设） |
| 量化缺陷影响面 | 做不到（本轮 review 长度上限就是因此无法量化） |

## 设计要点

- 校验报告 `ValidationReport.score` 是 **0–100**，而表里 `score` 列注释是
  **0–1** → 落库时必须归一化，否则「95 分」存成 95.0 会超出语义。
- `rule_results` 列默认 `list`，存 `checks` 明细。
- `llm_report` 存语义自检；无 LLM 时为空。
- 只 flush 不 commit（与 `EventRepository` 一致，事务由调用方控制）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.db.models  # noqa: F401  确保全部 ORM 注册到 Base.metadata
from app.db.base import Base
from app.db.models.log import ValidationResult
from app.db.models.task import Subtask, Task
from app.db.repositories.validation import ValidationRepository


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
                title="校验落库任务",
                description="测试",
                status="running",
            )
        )
        session.add(
            Subtask(
                id=subtask_id,
                task_id=task_id,
                node_id="n3",
                title="校验节点",
                description="描述",
                status="running",
            )
        )
        await session.commit()
    return engine, session_factory, task_id, subtask_id


def _report(
    *,
    passed: bool = True,
    score: float = 85.5,
    retryable: bool = False,
    feedback: str = "全部通过",
) -> dict[str, Any]:
    """生产形状的校验报告（`ValidationReport.model_dump(mode="json")`）。

    ⚠️ 照抄 `contracts.ValidationReport`：`passed/score/checks/retryable/feedback`。
    AGENTS §5 #33：夹具必须照抄真实载荷，不要自己发明形状。
    """
    return {
        "passed": passed,
        "score": score,
        "checks": [
            {"name": "rules", "passed": passed, "message": feedback, "advisory": False},
        ],
        "retryable": retryable,
        "feedback": feedback,
    }


class TestValidationRepositorySave:
    async def test_saves_one_row(self) -> None:
        engine, session_factory, _task_id, subtask_id = await _setup()
        try:
            async with session_factory() as session:
                row = await ValidationRepository(session).save(subtask_id, _report())
                await session.commit()
                assert row.id is not None

            async with session_factory() as session:
                rows = (await session.execute(select(ValidationResult))).scalars().all()
                assert len(rows) == 1
                assert rows[0].subtask_id == subtask_id
                assert rows[0].passed is True
        finally:
            await engine.dispose()

    async def test_score_is_normalized_to_zero_one(self) -> None:
        """契约是 0–100，列语义是 0–1 → 落库必须归一化。"""
        engine, session_factory, _task_id, subtask_id = await _setup()
        try:
            async with session_factory() as session:
                await ValidationRepository(session).save(subtask_id, _report(score=85.5))
                await session.commit()

            async with session_factory() as session:
                row = (await session.execute(select(ValidationResult))).scalar_one()
                assert row.score == pytest.approx(0.855)
        finally:
            await engine.dispose()

    async def test_checks_become_rule_results(self) -> None:
        engine, session_factory, _task_id, subtask_id = await _setup()
        try:
            report = _report()
            async with session_factory() as session:
                await ValidationRepository(session).save(subtask_id, report)
                await session.commit()

            async with session_factory() as session:
                row = (await session.execute(select(ValidationResult))).scalar_one()
                assert isinstance(row.rule_results, list)
                assert row.rule_results[0]["name"] == "rules"
        finally:
            await engine.dispose()

    async def test_feedback_goes_to_report_text(self) -> None:
        engine, session_factory, _task_id, subtask_id = await _setup()
        try:
            async with session_factory() as session:
                await ValidationRepository(session).save(
                    subtask_id, _report(feedback="长度超限：10230 > 10000")
                )
                await session.commit()

            async with session_factory() as session:
                row = (await session.execute(select(ValidationResult))).scalar_one()
                assert row.report == "长度超限：10230 > 10000"
        finally:
            await engine.dispose()

    async def test_validated_at_is_set(self) -> None:
        engine, session_factory, _task_id, subtask_id = await _setup()
        try:
            async with session_factory() as session:
                await ValidationRepository(session).save(subtask_id, _report())
                await session.commit()

            async with session_factory() as session:
                row = (await session.execute(select(ValidationResult))).scalar_one()
                assert row.validated_at is not None
                # SQLite 的 DateTime 不保留时区，读回是 naive；PostgreSQL 保留。
                # 这里只断言「写入了时间且不是未来」，不断言 tzinfo。
                stamp = row.validated_at
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=UTC)
                assert stamp <= datetime.now(UTC)
        finally:
            await engine.dispose()

    async def test_failed_validation_is_recorded(self) -> None:
        """失败的校验**更要**落库 —— 排查转人工全靠它。"""
        engine, session_factory, _task_id, subtask_id = await _setup()
        try:
            async with session_factory() as session:
                await ValidationRepository(session).save(
                    subtask_id,
                    _report(passed=False, score=0.0, retryable=True, feedback="长度超限"),
                )
                await session.commit()

            async with session_factory() as session:
                row = (await session.execute(select(ValidationResult))).scalar_one()
                assert row.passed is False
                assert row.score == 0.0
        finally:
            await engine.dispose()

    async def test_llm_report_is_stored_when_present(self) -> None:
        engine, session_factory, _task_id, subtask_id = await _setup()
        try:
            report = _report()
            report["semantic_check"] = {"score": 80, "issues": [], "suggestions": []}
            async with session_factory() as session:
                await ValidationRepository(session).save(subtask_id, report)
                await session.commit()

            async with session_factory() as session:
                row = (await session.execute(select(ValidationResult))).scalar_one()
                assert row.llm_report == {"score": 80, "issues": [], "suggestions": []}
        finally:
            await engine.dispose()

    async def test_llm_report_is_none_without_semantic_check(self) -> None:
        engine, session_factory, _task_id, subtask_id = await _setup()
        try:
            async with session_factory() as session:
                await ValidationRepository(session).save(subtask_id, _report())
                await session.commit()

            async with session_factory() as session:
                row = (await session.execute(select(ValidationResult))).scalar_one()
                assert row.llm_report is None
        finally:
            await engine.dispose()


class TestValidationRepositoryRobustness:
    async def test_empty_report_does_not_crash(self) -> None:
        """形状不对的报告不能炸掉持久化（持久化失败不该中断执行）。"""
        engine, session_factory, _task_id, subtask_id = await _setup()
        try:
            async with session_factory() as session:
                row = await ValidationRepository(session).save(subtask_id, {})
                await session.commit()
                assert row.passed is False  # 缺省从严
                assert row.score == 0.0
        finally:
            await engine.dispose()

    async def test_unknown_subtask_id_does_not_crash(self) -> None:
        """subtask 不存在时返回 None，不抛异常。"""
        engine, session_factory, _task_id, _subtask_id = await _setup()
        try:
            async with session_factory() as session:
                row = await ValidationRepository(session).save(uuid.uuid4(), _report())
                await session.commit()
                assert row is None
        finally:
            await engine.dispose()

    async def test_score_out_of_range_is_clamped(self) -> None:
        engine, session_factory, _task_id, subtask_id = await _setup()
        try:
            async with session_factory() as session:
                await ValidationRepository(session).save(subtask_id, _report(score=250.0))
                await session.commit()

            async with session_factory() as session:
                row = (await session.execute(select(ValidationResult))).scalar_one()
                assert row.score == 1.0
        finally:
            await engine.dispose()
