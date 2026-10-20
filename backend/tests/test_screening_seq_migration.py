"""`c9f6a2d4e1b7` 迁移测试（补 screening_decision.seq）。

## 背景

Task 2 的 TDD 发现：不能用 `created_at` 排序决策——同一事务内多行的
`server_default` 时间精度不足会并列，「最新决策」会取错，双人分歧判定随之误报。

修法：加 `seq` 列（稳定排序依据）。但 `b8e5f1c3d9a2` 已在生产执行过（无 `seq`），
所以需要这个**幂等**的补列迁移。

本文件锁定：
1. **幂等**：列已存在时 `upgrade()` 跳过（全新库路径）；
2. **补列**：列缺失时 `upgrade()` 真的加上并回填（已部署库路径）；
3. **回填正确**：按 `created_at, id` 排序，同记录内 `seq` 从 1 递增、无空洞；
4. `downgrade()` 幂等。
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "c9f6a2d4e1b7_add_screening_decision_seq.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("seq_migration", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_table(connection, *, with_seq: bool) -> None:
    """建一个最小 screening_decision 表（可含/不含 seq）。"""
    seq_column = "seq INTEGER NOT NULL, " if with_seq else ""
    connection.exec_driver_sql(
        "CREATE TABLE screening_decision ("
        "id CHAR(32) PRIMARY KEY, "
        f"{seq_column}"
        "record_id CHAR(32) NOT NULL, "
        "stage VARCHAR(16) NOT NULL, "
        "verdict VARCHAR(16) NOT NULL, "
        "created_at DATETIME NOT NULL)"
    )


class TestUpgrade:
    def test_skips_when_column_already_exists(self) -> None:
        """列已存在（全新库走修正后的上一版）→ 不报错、不重复加列。"""
        engine = create_engine("sqlite://")
        with engine.begin() as connection:
            _create_table(connection, with_seq=True)
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                _load().upgrade()

            columns = {c["name"] for c in inspect(connection).get_columns("screening_decision")}
        engine.dispose()

        assert "seq" in columns

    def test_adds_column_and_backfills_when_missing(self) -> None:
        """列缺失（已部署库）→ 加上并回填，且收紧为 NOT NULL。"""
        engine = create_engine("sqlite://")
        record_id = uuid.uuid4().hex
        with engine.begin() as connection:
            _create_table(connection, with_seq=False)
            # 同一记录 3 条决策（模拟历史数据）
            for index in range(3):
                connection.execute(
                    text(
                        "INSERT INTO screening_decision "
                        "(id, record_id, stage, verdict, created_at) "
                        "VALUES (:id, :record_id, :stage, :verdict, :created_at)"
                    ),
                    {
                        "id": uuid.uuid4().hex,
                        "record_id": record_id,
                        "stage": "title_abstract",
                        "verdict": "maybe",
                        "created_at": f"2026-10-20 10:00:0{index}",
                    },
                )

            context = MigrationContext.configure(connection)
            with Operations.context(context):
                _load().upgrade()

            columns = {c["name"] for c in inspect(connection).get_columns("screening_decision")}
            rows = (
                connection.execute(text("SELECT seq FROM screening_decision ORDER BY seq"))
                .scalars()
                .all()
            )
        engine.dispose()

        assert "seq" in columns
        assert list(rows) == [1, 2, 3], "回填应按时间顺序生成 1..N 且无空洞"

    def test_backfill_is_per_record(self) -> None:
        """回填按 `record_id` 分组：不同记录的 seq 各自从 1 开始。"""
        engine = create_engine("sqlite://")
        first, second = uuid.uuid4().hex, uuid.uuid4().hex
        with engine.begin() as connection:
            _create_table(connection, with_seq=False)
            for record_id in (first, second):
                for index in range(2):
                    connection.execute(
                        text(
                            "INSERT INTO screening_decision "
                            "(id, record_id, stage, verdict, created_at) "
                            "VALUES (:id, :record_id, :stage, :verdict, :created_at)"
                        ),
                        {
                            "id": uuid.uuid4().hex,
                            "record_id": record_id,
                            "stage": "title_abstract",
                            "verdict": "maybe",
                            "created_at": f"2026-10-20 10:00:0{index}",
                        },
                    )

            context = MigrationContext.configure(connection)
            with Operations.context(context):
                _load().upgrade()

            grouped = connection.execute(
                text("SELECT record_id, seq FROM screening_decision ORDER BY record_id, seq")
            ).all()
        engine.dispose()

        by_record: dict[str, list[int]] = {}
        for record_id, seq in grouped:
            by_record.setdefault(record_id, []).append(seq)

        assert by_record[first] == [1, 2]
        assert by_record[second] == [1, 2]

    def test_backfill_on_empty_table(self) -> None:
        """空表（当前生产状态）也要能顺利加列。"""
        engine = create_engine("sqlite://")
        with engine.begin() as connection:
            _create_table(connection, with_seq=False)
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                _load().upgrade()

            columns = {c["name"] for c in inspect(connection).get_columns("screening_decision")}
        engine.dispose()

        assert "seq" in columns


class TestDowngrade:
    def test_downgrade_removes_column(self) -> None:
        engine = create_engine("sqlite://")
        with engine.begin() as connection:
            _create_table(connection, with_seq=True)
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                _load().downgrade()

            columns = {c["name"] for c in inspect(connection).get_columns("screening_decision")}
        engine.dispose()

        assert "seq" not in columns

    def test_downgrade_is_idempotent(self) -> None:
        """列已不存在时回滚不报错。"""
        engine = create_engine("sqlite://")
        with engine.begin() as connection:
            _create_table(connection, with_seq=False)
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                _load().downgrade()
        engine.dispose()


class TestRevisionChain:
    def test_revision_follows_screening_migration(self) -> None:
        migration = _load()

        assert migration.revision == "c9f6a2d4e1b7"
        assert migration.down_revision == "b8e5f1c3d9a2"


class TestMigrationChainStillLinear:
    def test_single_head(self) -> None:
        """迁移链仍必须单 head（本迁移追加在 b8e5f1c3d9a2 之后）。"""
        import importlib.util as loader

        versions = Path(__file__).resolve().parents[1] / "alembic" / "versions"
        revisions: list[str] = []
        referenced: set[str] = set()
        for path in versions.glob("*.py"):
            spec = loader.spec_from_file_location(f"m_{path.stem}", path)
            assert spec is not None and spec.loader is not None
            module = loader.module_from_spec(spec)
            spec.loader.exec_module(module)
            revisions.append(module.revision)
            if module.down_revision:
                referenced.add(module.down_revision)

        heads = [revision for revision in revisions if revision not in referenced]
        assert len(heads) == 1, f"迁移链分叉：{heads}"


@pytest.mark.parametrize("column_present", [True, False])
def test_upgrade_is_idempotent_either_way(column_present: bool) -> None:
    """两条路径（列已存在 / 列缺失）都必须成功——这是修复类迁移的硬要求。"""
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        _create_table(connection, with_seq=column_present)
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            _load().upgrade()
            # 再跑一次也必须安全
            _load().upgrade()

        columns = {c["name"] for c in inspect(connection).get_columns("screening_decision")}
    engine.dispose()

    assert "seq" in columns
