"""筛选数据层测试（Phase 2 · PRISMA Task 1）。

覆盖：
1. ORM 约束：检索日志持久化、候选记录去重唯一键、决策追加式、排除必须有理由；
2. **迁移与模型一致**：迁移建出的列必须覆盖 ORM 全部列
   （Phase 1 `email_token` 漏建 `updated_at` 导致生产 500 的教训）。
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models.screening import (
    DecisionStage,
    FulltextStatus,
    ScreeningDecision,
    ScreeningRecord,
    ScreeningVerdict,
    SearchLog,
)

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "b8e5f1c3d9a2_add_screening.py"
)


def _engine():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return engine


def _search_log(task_id: uuid.UUID, **overrides: object) -> SearchLog:
    defaults: dict[str, object] = {
        "task_id": task_id,
        "query": "retrieval augmented generation",
        "sources": ["arxiv"],
        "hits_total": 1,
        "hits_by_source": {"arxiv": 1},
        "hits_deduped": 1,
    }
    defaults.update(overrides)
    return SearchLog(**defaults)  # type: ignore[arg-type]


def _record(log_id: uuid.UUID, task_id: uuid.UUID, **overrides: object) -> ScreeningRecord:
    defaults: dict[str, object] = {
        "search_log_id": log_id,
        "task_id": task_id,
        "source": "arxiv",
        "source_id": "2401.00001v1",
        "title": "T",
        "fulltext_status": FulltextStatus.UNAVAILABLE.value,
    }
    defaults.update(overrides)
    return ScreeningRecord(**defaults)  # type: ignore[arg-type]


class TestSearchLog:
    def test_persists_query_and_counts(self) -> None:
        engine = _engine()
        with Session(engine) as session:
            log = _search_log(uuid.uuid4(), hits_total=42, hits_by_source={"arxiv": 42})
            session.add(log)
            session.commit()
            assert log.id is not None
            assert log.hits_total == 42

    def test_hits_by_source_is_the_raw_count_source(self) -> None:
        """PRISMA「识别」用**去重前**各源命中数，必须能原样存回。"""
        engine = _engine()
        with Session(engine) as session:
            log = _search_log(
                uuid.uuid4(),
                hits_total=50,
                hits_by_source={"arxiv": 30, "openalex": 20},
                hits_deduped=35,
            )
            session.add(log)
            session.commit()
            session.refresh(log)

            assert log.hits_by_source == {"arxiv": 30, "openalex": 20}
            assert sum(log.hits_by_source.values()) == log.hits_total

    def test_executed_at_is_a_datetime(self) -> None:
        """`executed_at` 必须是真正的时间列（计划自审时修正过 `Mapped[object]` 的错误）。"""
        from datetime import UTC, datetime

        engine = _engine()
        with Session(engine) as session:
            moment = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
            log = _search_log(uuid.uuid4(), executed_at=moment)
            session.add(log)
            session.commit()
            session.refresh(log)

            assert log.executed_at is not None
            assert log.executed_at.year == 2026
            assert log.executed_at.hour == 9


class TestScreeningRecord:
    def test_unique_per_log_and_source_id(self) -> None:
        """同一检索日志下同一来源记录不得重复（去重的数据层保证）。"""
        engine = _engine()
        task_id = uuid.uuid4()
        with Session(engine) as session:
            log = _search_log(task_id)
            session.add(log)
            session.flush()
            session.add(_record(log.id, task_id))
            session.commit()

            session.add(_record(log.id, task_id, title="T again"))
            with pytest.raises(IntegrityError):
                session.commit()

    def test_default_stage_is_title_abstract(self) -> None:
        engine = _engine()
        task_id = uuid.uuid4()
        with Session(engine) as session:
            log = _search_log(task_id)
            session.add(log)
            session.flush()
            record = _record(log.id, task_id)
            session.add(record)
            session.commit()
            session.refresh(record)

            assert record.current_stage == DecisionStage.TITLE_ABSTRACT.value


class TestScreeningDecision:
    def test_stage_and_verdict_enum_values(self) -> None:
        assert DecisionStage.TITLE_ABSTRACT.value == "title_abstract"
        assert DecisionStage.FULLTEXT.value == "fulltext"
        assert DecisionStage.INCLUDED.value == "included"
        assert ScreeningVerdict.INCLUDE.value == "include"
        assert ScreeningVerdict.EXCLUDE.value == "exclude"
        assert ScreeningVerdict.MAYBE.value == "maybe"
        assert FulltextStatus.UNAVAILABLE.value == "unavailable"

    def test_exclude_without_reason_is_rejected(self) -> None:
        """排除必须给理由（PRISMA 2020 要求报告排除理由分类）。"""
        engine = _engine()
        task_id = uuid.uuid4()
        with Session(engine) as session:
            log = _search_log(task_id)
            session.add(log)
            session.flush()
            record = _record(log.id, task_id)
            session.add(record)
            session.flush()

            session.add(
                ScreeningDecision(
                    record_id=record.id,
                    stage=DecisionStage.TITLE_ABSTRACT.value,
                    verdict=ScreeningVerdict.EXCLUDE.value,
                    reviewer_id=uuid.uuid4(),
                    exclusion_reason=None,
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()

    def test_exclude_with_reason_is_accepted(self) -> None:
        engine = _engine()
        task_id = uuid.uuid4()
        with Session(engine) as session:
            log = _search_log(task_id)
            session.add(log)
            session.flush()
            record = _record(log.id, task_id)
            session.add(record)
            session.flush()
            session.add(
                ScreeningDecision(
                    record_id=record.id,
                    stage=DecisionStage.TITLE_ABSTRACT.value,
                    verdict=ScreeningVerdict.EXCLUDE.value,
                    reviewer_id=uuid.uuid4(),
                    exclusion_reason="not relevant",
                )
            )
            session.commit()

    def test_decisions_are_appended(self) -> None:
        """追加式：同一记录同一阶段可有多条决策（审计轨迹）。"""
        engine = _engine()
        task_id = uuid.uuid4()
        reviewer = uuid.uuid4()
        with Session(engine) as session:
            log = _search_log(task_id)
            session.add(log)
            session.flush()
            record = _record(log.id, task_id)
            session.add(record)
            session.flush()
            for verdict in (ScreeningVerdict.MAYBE, ScreeningVerdict.INCLUDE):
                session.add(
                    ScreeningDecision(
                        record_id=record.id,
                        stage=DecisionStage.TITLE_ABSTRACT.value,
                        verdict=verdict.value,
                        reviewer_id=reviewer,
                    )
                )
            session.commit()

            from sqlalchemy import func, select

            count = session.execute(
                select(func.count()).select_from(ScreeningDecision)
            ).scalar_one()
            assert count == 2


class TestMigrationMatchesOrm:
    """迁移建出的表结构必须覆盖 ORM 全部列。"""

    def _load_migration(self):
        spec = importlib.util.spec_from_file_location("screening_migration", MIGRATION_PATH)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_migration_creates_all_orm_columns(self) -> None:
        from alembic.migration import MigrationContext
        from alembic.operations import Operations

        engine = create_engine("sqlite://")
        with engine.begin() as connection:
            # 前置表（FK 目标）用最小 DDL 代替
            connection.exec_driver_sql("CREATE TABLE task (id CHAR(32) PRIMARY KEY)")
            connection.exec_driver_sql("CREATE TABLE user (id CHAR(32) PRIMARY KEY)")
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                self._load_migration().upgrade()

            for table in ("search_log", "screening_record", "screening_decision"):
                orm_columns = {column.name for column in Base.metadata.tables[table].columns}
                migrated = {column["name"] for column in inspect(connection).get_columns(table)}
                missing = orm_columns - migrated
                assert not missing, f"{table} 迁移缺列：{sorted(missing)}"

    def test_migration_downgrade_removes_tables(self) -> None:
        from alembic.migration import MigrationContext
        from alembic.operations import Operations

        engine = create_engine("sqlite://")
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE task (id CHAR(32) PRIMARY KEY)")
            connection.exec_driver_sql("CREATE TABLE user (id CHAR(32) PRIMARY KEY)")
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                migration = self._load_migration()
                migration.upgrade()
                migration.downgrade()

            tables = set(inspect(connection).get_table_names())
        engine.dispose()

        assert not ({"search_log", "screening_record", "screening_decision"} & tables)

    def test_revision_follows_previous_head(self) -> None:
        migration = self._load_migration()

        assert migration.revision == "b8e5f1c3d9a2"
        assert migration.down_revision == "a7d4e2f9c1b8"
