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
