"""历史校验结论迁移：把 `subtask.output_data.validation` 回填到 `validation_result`。

## 背景

`validation_result` 表从建库起就存在，但直到 2026-10-27 才接上写入方
（`90dfd6a`）。在此之前，校验结论只存在 `subtask.output_data` 的 JSON 里。

服务器实测（2026-10-27）：

- **188 个 subtask** 的 `output_data.validation` 非空；
- `validation_result` 表 **0 行**。

修好写入只对**新任务**生效。不迁移的话，`validation_result` 永远缺这段历史，
「校验通过率」「按失败原因聚合」这些统计仍然只能覆盖新任务。

## 语义约束

- **只迁移 `validation_result` 里还没有的 subtask**（幂等，可重跑）；
- **`score` 必须归一化到 0–1**：历史载荷里是 **80.0~100.0**（契约 0–100），
  而列语义是 0–1 —— 与 `90dfd6a` 的 `normalize_score` 同一套规则；
- **不删除、不改写 `output_data`**：只新增表行；
- **缺字段的载荷按从严处理**（`passed` 非 true 即 False、`score` 缺失即 0.0）。

## 为什么用脚本而不是 Alembic 迁移

与 `backfill_used_tokens.py` 同一理由：迁移里做业务计算（解析 JSON、归一化分数）
会把业务逻辑塞进 schema 版本链；脚本可 `--dry-run` 先看规模、可幂等重跑、可回查。
"""

from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.db.models  # noqa: F401  确保全部 ORM 注册到 Base.metadata
from app.db.base import Base
from app.db.models.log import ValidationResult
from app.db.models.task import Subtask, Task
from scripts.migrate_validation_results import (
    collect_candidates,
    migrate,
    payload_to_row_values,
)


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _seed_subtask(
    factory: Any,
    *,
    validation: dict[str, Any] | None,
    node_id: str = "n3",
) -> Any:
    import uuid

    task_id = uuid.uuid4()
    subtask_id = uuid.uuid4()
    async with factory() as session:
        session.add(
            Task(
                id=task_id,
                user_id=uuid.uuid4(),
                title="迁移测试",
                description="",
                status="completed",
            )
        )
        session.add(
            Subtask(
                id=subtask_id,
                task_id=task_id,
                node_id=node_id,
                title="节点",
                description="",
                status="completed",
                output_data={"validation": validation} if validation is not None else {},
            )
        )
        await session.commit()
    return subtask_id


def _historical_payload(
    *,
    passed: bool = True,
    score: float = 100.0,
    checks: list[dict[str, Any]] | None = None,
    feedback: str = "全部通过",
) -> dict[str, Any]:
    """**照抄真实历史载荷形状**（AGENTS §5 #33）。

    服务器 188 条实测：`score`/`passed` 全有；`checks`/`feedback`/`retryable`
    有 172 条；`semantic_check`/`rule_check`/`issues`/`suggestions` 有 41 条。
    """
    return {
        "score": score,
        "passed": passed,
        "checks": (
            checks
            if checks is not None
            else [{"name": "rules", "passed": passed, "message": feedback, "advisory": False}]
        ),
        "feedback": feedback,
        "retryable": False,
    }


class TestPayloadToRowValues:
    """纯函数：历史载荷 → 表列值。"""

    def test_score_is_normalized_from_contract_range(self) -> None:
        """历史载荷 score 是 0–100，列语义 0–1 → 必须除以 100。"""
        values = payload_to_row_values(_historical_payload(score=100.0))
        assert values["score"] == pytest.approx(1.0)

        values = payload_to_row_values(_historical_payload(score=85.5))
        assert values["score"] == pytest.approx(0.855)

    def test_score_out_of_range_is_clamped(self) -> None:
        assert payload_to_row_values(_historical_payload(score=250.0))["score"] == 1.0
        assert payload_to_row_values(_historical_payload(score=-5.0))["score"] == 0.0

    def test_passed_only_true_counts_as_pass(self) -> None:
        assert payload_to_row_values(_historical_payload(passed=True))["passed"] is True
        assert payload_to_row_values(_historical_payload(passed=False))["passed"] is False

    def test_missing_passed_defaults_to_failed(self) -> None:
        """缺字段从严：不伪造「通过」。"""
        payload: dict[str, Any] = {"score": 90.0}
        assert payload_to_row_values(payload)["passed"] is False

    def test_checks_become_rule_results(self) -> None:
        values = payload_to_row_values(_historical_payload())
        assert isinstance(values["rule_results"], list)
        assert values["rule_results"][0]["name"] == "rules"

    def test_missing_checks_yields_empty_list(self) -> None:
        """188 条里有 16 条没有 checks（只有 score/passed）→ 不能崩。"""
        payload = {"score": 100.0, "passed": True}
        assert payload_to_row_values(payload)["rule_results"] == []

    def test_feedback_becomes_report(self) -> None:
        assert (
            payload_to_row_values(_historical_payload(feedback="长度超限"))["report"] == "长度超限"
        )

    def test_semantic_check_becomes_llm_report(self) -> None:
        payload = _historical_payload()
        payload["semantic_check"] = {"score": 80, "issues": [], "suggestions": []}
        values = payload_to_row_values(payload)
        assert values["llm_report"] == {"score": 80, "issues": [], "suggestions": []}

    def test_no_semantic_check_means_none(self) -> None:
        assert payload_to_row_values(_historical_payload())["llm_report"] is None

    def test_empty_payload_does_not_crash(self) -> None:
        values = payload_to_row_values({})
        assert values["passed"] is False
        assert values["score"] == 0.0
        assert values["rule_results"] == []


class TestCollectCandidates:
    async def test_finds_subtasks_with_validation(self, session_factory) -> None:
        subtask_id = await _seed_subtask(session_factory, validation=_historical_payload())
        async with session_factory() as session:
            candidates = await collect_candidates(session)
        assert [c.subtask_id for c in candidates] == [subtask_id]

    async def test_skips_subtasks_without_validation(self, session_factory) -> None:
        await _seed_subtask(session_factory, validation=None)
        async with session_factory() as session:
            assert await collect_candidates(session) == []

    async def test_skips_already_migrated(self, session_factory) -> None:
        """幂等：已有表行的 subtask 不再重复迁移。"""
        subtask_id = await _seed_subtask(session_factory, validation=_historical_payload())
        async with session_factory() as session:
            from app.db.repositories.validation import ValidationRepository

            await ValidationRepository(session).save(subtask_id, _historical_payload())
            await session.commit()
        async with session_factory() as session:
            assert await collect_candidates(session) == []


class TestMigrate:
    async def test_writes_rows(self, session_factory) -> None:
        await _seed_subtask(session_factory, validation=_historical_payload())
        async with session_factory() as session:
            written = await migrate(session, await collect_candidates(session))
            await session.commit()
        assert written == 1
        async with session_factory() as session:
            rows = (await session.execute(select(ValidationResult))).scalars().all()
            assert len(rows) == 1
            assert rows[0].score == pytest.approx(1.0)

    async def test_is_idempotent(self, session_factory) -> None:
        """连跑两次只应写一次 —— 重跑不会产生重复行。"""
        await _seed_subtask(session_factory, validation=_historical_payload())

        async with session_factory() as session:
            written1 = await migrate(session, await collect_candidates(session))
            await session.commit()
        async with session_factory() as session:
            written2 = await migrate(session, await collect_candidates(session))
            await session.commit()

        assert written1 == 1
        assert written2 == 0
        async with session_factory() as session:
            rows = (await session.execute(select(ValidationResult))).scalars().all()
            assert len(rows) == 1

    async def test_migrates_failed_validations(self, session_factory) -> None:
        """失败的校验**更要**迁移 —— 排查转人工全靠它。"""
        await _seed_subtask(
            session_factory,
            validation=_historical_payload(passed=False, score=80.0, feedback="长度超限"),
        )
        async with session_factory() as session:
            await migrate(session, await collect_candidates(session))
            await session.commit()
        async with session_factory() as session:
            row = (await session.execute(select(ValidationResult))).scalar_one()
            assert row.passed is False
            assert row.score == pytest.approx(0.8)
            assert row.report == "长度超限"

    async def test_does_not_touch_output_data(self, session_factory) -> None:
        """迁移只新增表行，不改 `output_data`（向后兼容）。"""
        subtask_id = await _seed_subtask(session_factory, validation=_historical_payload())
        async with session_factory() as session:
            before = await session.get(Subtask, subtask_id)
            assert before is not None
            snapshot = dict(before.output_data)
        async with session_factory() as session:
            await migrate(session, await collect_candidates(session))
            await session.commit()
        async with session_factory() as session:
            after = await session.get(Subtask, subtask_id)
            assert after is not None
            assert after.output_data == snapshot
