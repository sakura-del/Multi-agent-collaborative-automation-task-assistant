"""迁移与 ORM 模型一致性回归（Phase 1 P1-C 暴露的真实缺陷）。

## 缺陷

`EmailToken` 继承 `Base`，因此 ORM 元数据里有 `created_at` **和 `updated_at`** 两列
（`Base` 提供自动时间戳）。但迁移 `e3b8c4d9a2f1` 只建了 `created_at`。

SQLite 上测试没暴露（测试用 `Base.metadata.create_all` 直接按 ORM 建表，不经迁移），
**只有真实 PostgreSQL 走 Alembic 时才炸**：

```
sqlalchemy.exc.ProgrammingError: column "updated_at" of relation "email_token" does not exist
[SQL: UPDATE email_token SET used_at=..., updated_at=now() WHERE ...]
```

后果：生产上「注册」「重发验证邮件」「密码重置申请」全部 500（服务端验收实测）。

## 本文件锁定

**迁移建出来的表结构必须与 ORM 模型一致**——这是所有迁移的通用不变量，
不只针对 email_token。用「跑迁移 → 反射表结构 → 与 `Base.metadata` 比对」来验证，
不依赖任何真实数据库服务（SQLite 即可）。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect

import app.db.models  # noqa: F401  确保全部 ORM 注册到 metadata
from app.db.base import Base

VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
EMAIL_MIGRATION = VERSIONS_DIR / "e3b8c4d9a2f1_add_email_lifecycle.py"


def _load(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"migration_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def migrated_engine() -> sa.Engine:
    """在 SQLite 上只跑 email_token 相关迁移（先建最小 user 表满足 FK）。"""
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        # 迁移只加列/建表，前置的 user 表用最小 DDL 代替（FK 目标存在即可）
        connection.exec_driver_sql(
            "CREATE TABLE user (id CHAR(32) PRIMARY KEY, username VARCHAR(64), "
            "email VARCHAR(128), password_hash VARCHAR(128))"
        )
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            _load(EMAIL_MIGRATION).upgrade()
    return engine


class TestEmailTokenMigrationMatchesOrm:
    def test_migrated_table_has_every_orm_column(self, migrated_engine: sa.Engine) -> None:
        """迁移建出的列必须覆盖 ORM 模型的全部列（漏列会导致运行时 500）。"""
        orm_columns = {column.name for column in Base.metadata.tables["email_token"].columns}
        migrated_columns = {
            column["name"] for column in inspect(migrated_engine).get_columns("email_token")
        }

        missing = orm_columns - migrated_columns
        assert not missing, f"迁移缺少 ORM 列（生产会 500）：{sorted(missing)}"

    def test_updated_at_column_exists(self, migrated_engine: sa.Engine) -> None:
        """`Base` 的 updated_at 是具体触发点，单独锁定（回归时定位更快）。"""
        columns = {c["name"] for c in inspect(migrated_engine).get_columns("email_token")}

        assert "updated_at" in columns, "email_token 缺少 updated_at（Base 提供该列）"

    def test_updated_at_is_writable(self, migrated_engine: sa.Engine) -> None:
        """真实验证：UPDATE 带 updated_at 能执行（复现生产失败的那条 SQL 形态）。"""
        import uuid

        table = Base.metadata.tables["email_token"]
        token_hash = "a" * 64
        with migrated_engine.begin() as connection:
            connection.execute(
                table.insert().values(
                    id=uuid.uuid4(),
                    user_id=uuid.uuid4(),
                    purpose="verify",
                    token_hash=token_hash,
                    expires_at=sa.func.now(),
                )
            )
            # 这条 UPDATE 的形态与生产失败时一致（SET used_at, updated_at=now()）
            connection.execute(
                table.update()
                .where(table.c.token_hash == token_hash)
                .values(used_at=sa.func.now(), updated_at=sa.func.now())
            )
            count = connection.execute(
                sa.select(sa.func.count()).select_from(table).where(table.c.used_at.is_not(None))
            ).scalar_one()
        assert count == 1, "UPDATE 未生效"


class TestAllMigrationsMatchOrmTables:
    """通用不变量：所有 ORM 表的列都必须在某个迁移里被建出来。"""

    def test_every_orm_table_is_created_by_a_migration(self) -> None:
        """ORM 表必须在迁移里出现（否则全新部署会缺表）。"""
        migration_text = "\n".join(
            path.read_text(encoding="utf-8") for path in VERSIONS_DIR.glob("*.py")
        )
        missing = [
            name
            for name in Base.metadata.tables
            if f'"{name}"' not in migration_text and f"'{name}'" not in migration_text
        ]
        assert not missing, f"以下 ORM 表未被任何迁移创建：{sorted(missing)}"


class TestFixMigrationIsIdempotent:
    """修复类迁移必须对「全新部署」与「已部署库」同时成立。

    ## 缺陷

    `f5c2a8d1b3e7` 是为**已部署库**补 `email_token.updated_at` 的修复迁移；同时
    `e3b8c4d9a2f1` 也被直接修正（已含该列）。于是全新部署时：
    `e3b8c4d9a2f1` 建出该列 → `f5c2a8d1b3e7` 再无条件 ADD → 失败：

        ProgrammingError: column "updated_at" of relation "email_token" already exists

    CI 的 compose smoke 用的是全新库，因此暴露；本地 SQLite 测试与已部署生产库都碰不到。

    ## 要求

    修复迁移的 `upgrade()` / `downgrade()` 必须**幂等**：列已存在（或已不存在）时跳过。
    """

    FIX_MIGRATION = VERSIONS_DIR / "f5c2a8d1b3e7_add_email_token_updated_at.py"

    def test_upgrade_is_idempotent_when_column_exists(self) -> None:
        """在**已含 updated_at** 的表上跑修复迁移，必须不报错（全新部署路径）。"""
        engine = create_engine("sqlite://")
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE email_token ("
                "id CHAR(32) PRIMARY KEY, token_hash VARCHAR(64) NOT NULL, "
                "created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, "
                "updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL)"
            )
            context = MigrationContext.configure(connection)
            # 关键断言：不抛 ProgrammingError
            with Operations.context(context):
                _load(self.FIX_MIGRATION).upgrade()

            columns = {c["name"] for c in inspect(connection).get_columns("email_token")}
        engine.dispose()

        assert "updated_at" in columns

    def test_upgrade_adds_column_when_missing(self) -> None:
        """在**缺列**的旧库上跑修复迁移，必须真的补上（已部署库路径）。"""
        engine = create_engine("sqlite://")
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE email_token ("
                "id CHAR(32) PRIMARY KEY, token_hash VARCHAR(64) NOT NULL, "
                "created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL)"
            )
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                _load(self.FIX_MIGRATION).upgrade()

            columns = {c["name"] for c in inspect(connection).get_columns("email_token")}
        engine.dispose()

        assert "updated_at" in columns

    def test_downgrade_is_idempotent_when_column_missing(self) -> None:
        """列已不存在时回滚也必须不报错。"""
        engine = create_engine("sqlite://")
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE email_token (id CHAR(32) PRIMARY KEY, token_hash VARCHAR(64))"
            )
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                _load(self.FIX_MIGRATION).downgrade()
        engine.dispose()


class TestTaskQuotaSettlementMigration:
    """`d4b1e7f2a8c3`（task 配额结算列）的行为与幂等。

    这个迁移有两个**必须在生产上成立**的性质：

    1. **幂等**：全新部署时列已由 ORM 建好；已部署库缺列。两条路径都要能跑通
       （Phase 1 `email_token` 非幂等迁移导致 compose smoke 失败的教训）。
    2. **存量 `completed` 任务置 `quota_settled=true`**：这些任务的预扣已在完成时
       按实际用量校正过，若不标记，上线后任何重放路径都会**再退一次**
       —— 额度被重复退还，等于可被刷。
    """

    MIGRATION = VERSIONS_DIR / "d4b1e7f2a8c3_add_task_quota_settlement.py"

    @staticmethod
    def _task_ddl() -> str:
        return (
            "CREATE TABLE task ("
            "id CHAR(32) PRIMARY KEY, status VARCHAR(16) NOT NULL DEFAULT 'pending', "
            "user_id CHAR(32), title VARCHAR(256), description TEXT, "
            "created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, "
            "updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL)"
        )

    def test_adds_columns_and_backfills_completed(self) -> None:
        engine = create_engine("sqlite://")
        with engine.begin() as connection:
            connection.exec_driver_sql(self._task_ddl())
            connection.exec_driver_sql(
                "INSERT INTO task (id, status) VALUES ('a', 'completed'), ('b', 'running')"
            )
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                _load(self.MIGRATION).upgrade()

            columns = {c["name"] for c in inspect(connection).get_columns("task")}
            assert {"used_tokens", "quota_settled"} <= columns

            rows = dict(
                connection.exec_driver_sql(
                    "SELECT id, quota_settled FROM task ORDER BY id"
                ).fetchall()
            )
            used = dict(
                connection.exec_driver_sql(
                    "SELECT id, used_tokens FROM task ORDER BY id"
                ).fetchall()
            )
        engine.dispose()

        assert rows["a"] in (True, 1), "已完成任务必须标记为已结算（否则会重复退款）"
        assert rows["b"] in (False, 0), "未完成任务必须保持未结算"
        assert used == {"a": 0, "b": 0}

    def test_upgrade_is_idempotent_when_columns_exist(self) -> None:
        """列已存在（全新部署路径）时不得报错。"""
        engine = create_engine("sqlite://")
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE task ("
                "id CHAR(32) PRIMARY KEY, status VARCHAR(16), "
                "used_tokens INTEGER NOT NULL DEFAULT 0, "
                "quota_settled BOOLEAN NOT NULL DEFAULT 0)"
            )
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                _load(self.MIGRATION).upgrade()
        engine.dispose()

    def test_downgrade_removes_columns(self) -> None:
        engine = create_engine("sqlite://")
        with engine.begin() as connection:
            connection.exec_driver_sql(self._task_ddl())
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                module = _load(self.MIGRATION)
                module.upgrade()
                module.downgrade()

            columns = {c["name"] for c in inspect(connection).get_columns("task")}
        engine.dispose()

        assert "used_tokens" not in columns
        assert "quota_settled" not in columns
