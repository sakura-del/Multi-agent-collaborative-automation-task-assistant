"""CitationStyle 数据层测试（Issue #4 M2-1，计划 2026-10-19-04b §1.1）。

覆盖：
1. ORM 建表 / 读写 / 字段默认值（SQLite 文件库，约束真实生效）；
2. Repository owner-scoped：`list_for_user` 只含该用户自定义且按 created_at 倒序；
   `get_for_user` 跨用户与不存在同语义；`get_preset` 只认 `user_id IS NULL`；
3. 唯一约束：同用户同 `style_id` 冲突；预置（`user_id IS NULL`）由部分唯一索引兜底；
4. Alembic 迁移在 SQLite 上 upgrade / downgrade 往返，且迁移链唯一 head 指向本迁移。

不依赖真实 PostgreSQL、不产生任何网络请求。
"""

from __future__ import annotations

import importlib.util
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import pytest_asyncio
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import app.db.models  # noqa: F401  确保既有 ORM 全部注册到 Base.metadata
from app.db.base import Base
from app.db.models.citation_style import CitationStyle
from app.db.models.user import User
from app.db.repositories.citation_style import CitationStyleRepository

BACKEND_DIR = Path(__file__).resolve().parent.parent
VERSIONS_DIR = BACKEND_DIR / "alembic" / "versions"
PREVIOUS_HEAD = "8f1c2b3d4e5f"


# ===== 装配与工具 =====


async def _setup_engine(tmp_path: Path) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """文件型 SQLite：每个 session 独立连接，唯一约束与部分唯一索引真实生效。"""
    db_path = tmp_path / "citation-style.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path.as_posix()}",
        connect_args={"timeout": 10},
    )
    async with engine.begin() as connection:
        await connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, factory


@pytest_asyncio.fixture
async def session_factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine, factory = await _setup_engine(tmp_path)
    try:
        yield factory
    finally:
        await engine.dispose()


def _style(
    *,
    user_id: uuid.UUID | None,
    style_id: str = "custom-style",
    title: str = "自定义样式",
    created_at: datetime | None = None,
    **overrides: Any,
) -> CitationStyle:
    """构造 CitationStyle 实例（不传 default_locale 以便断言 ORM 默认值）。"""
    payload: dict[str, Any] = {
        "user_id": user_id,
        "style_id": style_id,
        "title": title,
        "numbering": "citation-number",
        "ordering": "citation-order",
        "filename": f"{style_id}.csl",
        "content_sha256": "a" * 64,
        "size_bytes": 1234,
    }
    payload.update(overrides)
    style = CitationStyle(**payload)
    if created_at is not None:
        style.created_at = created_at
    return style


async def _make_user(factory: async_sessionmaker[AsyncSession], username: str) -> uuid.UUID:
    async with factory() as session:
        user = User(username=username, email=f"{username}@example.com", password_hash="x" * 60)
        session.add(user)
        await session.commit()
        return user.id


async def _add_style(factory: async_sessionmaker[AsyncSession], **kwargs: Any) -> uuid.UUID:
    async with factory() as session:
        repository = CitationStyleRepository(session)
        style = await repository.create(_style(**kwargs))
        await session.commit()
        return style.id


def _load_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"m2_migration_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migration_paths() -> list[Path]:
    return sorted(VERSIONS_DIR.glob("*.py"))


def _citation_style_migration() -> ModuleType:
    matches = sorted(VERSIONS_DIR.glob("*_add_citation_style.py"))
    assert len(matches) == 1, f"期望恰好一个 citation_style 迁移，实际 {matches}"
    return _load_module(matches[0])


# ===== ORM：建表 / 读写 / 默认值 =====


class TestCitationStyleOrm:
    """ORM 字段与默认值。"""

    async def test_create_and_read_round_trip(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = await _make_user(session_factory, "round-trip")
        style_id = await _add_style(
            session_factory,
            user_id=user_id,
            style_id="my-gbt-style",
            title="我的 GB/T 样式",
            numbering="none",
            ordering="author-date",
            content_sha256="b" * 64,
            size_bytes=2048,
        )

        async with session_factory() as session:
            style = await session.get(CitationStyle, style_id)

        assert style is not None
        assert isinstance(style.id, uuid.UUID)
        assert style.user_id == user_id
        assert style.style_id == "my-gbt-style"
        assert style.title == "我的 GB/T 样式"
        assert style.numbering == "none"
        assert style.ordering == "author-date"
        assert style.default_locale == "en-US"
        assert style.filename == "my-gbt-style.csl"
        assert style.content_sha256 == "b" * 64
        assert style.size_bytes == 2048

    async def test_timestamps_filled_by_server_default(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = await _make_user(session_factory, "timestamps")
        style_id = await _add_style(session_factory, user_id=user_id)

        async with session_factory() as session:
            style = await session.get(CitationStyle, style_id)

        assert style is not None
        assert style.created_at is not None
        assert style.updated_at is not None

    async def test_preset_row_has_null_user_id(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        style_id = await _add_style(session_factory, user_id=None, style_id="preset-demo")

        async with session_factory() as session:
            style = await session.get(CitationStyle, style_id)

        assert style is not None
        assert style.user_id is None

    async def test_default_locale_can_be_overridden(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = await _make_user(session_factory, "locale")
        style_id = await _add_style(session_factory, user_id=user_id, default_locale="zh-CN")

        async with session_factory() as session:
            style = await session.get(CitationStyle, style_id)

        assert style is not None
        assert style.default_locale == "zh-CN"


# ===== Repository：owner-scoped 查询 =====


class TestCitationStyleRepository:
    """所有查询必须带 user_id 过滤：跨用户与不存在同语义。"""

    async def test_list_for_user_returns_only_own_styles_in_desc_order(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        owner = await _make_user(session_factory, "owner")
        other = await _make_user(session_factory, "other")
        await _add_style(
            session_factory,
            user_id=owner,
            style_id="oldest",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        await _add_style(
            session_factory,
            user_id=owner,
            style_id="newest",
            created_at=datetime(2026, 1, 3, tzinfo=UTC),
        )
        await _add_style(
            session_factory,
            user_id=owner,
            style_id="middle",
            created_at=datetime(2026, 1, 2, tzinfo=UTC),
        )
        await _add_style(session_factory, user_id=other, style_id="not-mine")
        await _add_style(session_factory, user_id=None, style_id="preset-style")

        async with session_factory() as session:
            styles = await CitationStyleRepository(session).list_for_user(owner)

        assert [style.style_id for style in styles] == ["newest", "middle", "oldest"]
        assert all(style.user_id == owner for style in styles)

    async def test_list_for_user_excludes_presets(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = await _make_user(session_factory, "no-preset")
        await _add_style(session_factory, user_id=None, style_id="preset-only")

        async with session_factory() as session:
            styles = await CitationStyleRepository(session).list_for_user(user_id)

        assert styles == []

    async def test_get_for_user_returns_own_style(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = await _make_user(session_factory, "get-own")
        await _add_style(session_factory, user_id=user_id, style_id="mine")

        async with session_factory() as session:
            style = await CitationStyleRepository(session).get_for_user(user_id, "mine")

        assert style is not None
        assert style.style_id == "mine"
        assert style.user_id == user_id

    async def test_get_for_user_cross_user_returns_none(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        owner = await _make_user(session_factory, "cross-owner")
        intruder = await _make_user(session_factory, "cross-intruder")
        await _add_style(session_factory, user_id=owner, style_id="private-style")

        async with session_factory() as session:
            style = await CitationStyleRepository(session).get_for_user(intruder, "private-style")

        assert style is None

    async def test_get_for_user_missing_returns_none(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = await _make_user(session_factory, "missing")

        async with session_factory() as session:
            style = await CitationStyleRepository(session).get_for_user(user_id, "不存在")

        assert style is None

    async def test_get_for_user_does_not_return_preset(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = await _make_user(session_factory, "preset-not-mine")
        await _add_style(session_factory, user_id=None, style_id="gbt-7714-2025")

        async with session_factory() as session:
            style = await CitationStyleRepository(session).get_for_user(user_id, "gbt-7714-2025")

        assert style is None

    async def test_get_preset_returns_system_style(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        await _add_style(session_factory, user_id=None, style_id="system-preset")

        async with session_factory() as session:
            style = await CitationStyleRepository(session).get_preset("system-preset")

        assert style is not None
        assert style.style_id == "system-preset"
        assert style.user_id is None

    async def test_get_preset_ignores_user_styles(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = await _make_user(session_factory, "preset-missing")
        await _add_style(session_factory, user_id=user_id, style_id="only-custom")

        async with session_factory() as session:
            repository = CitationStyleRepository(session)
            assert await repository.get_preset("only-custom") is None
            assert await repository.get_preset("根本不存在") is None

    async def test_delete_removes_row(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = await _make_user(session_factory, "delete-me")
        await _add_style(session_factory, user_id=user_id, style_id="to-delete")

        async with session_factory() as session:
            repository = CitationStyleRepository(session)
            style = await repository.get_for_user(user_id, "to-delete")
            assert style is not None
            await repository.delete(style)
            await session.commit()

        async with session_factory() as session:
            assert await CitationStyleRepository(session).get_for_user(user_id, "to-delete") is None


# ===== 约束：同用户唯一 + 预置部分唯一索引 =====


class TestCitationStyleConstraints:
    """唯一约束与部分唯一索引。"""

    async def test_duplicate_style_id_for_same_user_raises_integrity_error(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = await _make_user(session_factory, "dup-user")
        await _add_style(session_factory, user_id=user_id, style_id="dup-style")

        with pytest.raises(IntegrityError):
            await _add_style(session_factory, user_id=user_id, style_id="dup-style", title="重复")

    async def test_same_user_different_style_ids_allowed(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        user_id = await _make_user(session_factory, "two-styles")
        await _add_style(session_factory, user_id=user_id, style_id="style-a")
        await _add_style(session_factory, user_id=user_id, style_id="style-b")

        async with session_factory() as session:
            styles = await CitationStyleRepository(session).list_for_user(user_id)

        assert sorted(style.style_id for style in styles) == ["style-a", "style-b"]

    async def test_preset_and_custom_styles_share_style_id(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """预置（user_id IS NULL）可与多个用户同 style_id 的自定义样式共存。"""
        first = await _make_user(session_factory, "share-first")
        second = await _make_user(session_factory, "share-second")
        await _add_style(session_factory, user_id=None, style_id="shared-style")
        await _add_style(session_factory, user_id=first, style_id="shared-style")
        await _add_style(session_factory, user_id=second, style_id="shared-style")

        async with session_factory() as session:
            count = await session.scalar(
                sa.select(sa.func.count())
                .select_from(CitationStyle)
                .where(CitationStyle.style_id == "shared-style")
            )
            preset = await CitationStyleRepository(session).get_preset("shared-style")
            first_style = await CitationStyleRepository(session).get_for_user(first, "shared-style")

        assert count == 3
        assert preset is not None and preset.user_id is None
        assert first_style is not None and first_style.user_id == first

    async def test_duplicate_preset_style_id_raises_integrity_error(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """部分唯一索引：预置样式（user_id IS NULL）同 style_id 只能有一条。"""
        await _add_style(session_factory, user_id=None, style_id="preset-dup")

        with pytest.raises(IntegrityError):
            await _add_style(session_factory, user_id=None, style_id="preset-dup")


# ===== Alembic 迁移：SQLite 往返 =====


class TestCitationStyleMigration:
    """迁移必须在 SQLite 与 PostgreSQL 两方言都能跑；downgrade 完整可回滚。"""

    def test_revision_follows_previous_head_and_is_unique_head(self) -> None:
        migration = _citation_style_migration()

        assert migration.down_revision == PREVIOUS_HEAD
        assert isinstance(migration.revision, str) and migration.revision

        revisions: list[str] = []
        down_revisions: list[str] = []
        for path in _migration_paths():
            module = _load_module(path)
            revisions.append(module.revision)
            if module.down_revision is not None:
                down_revisions.append(module.down_revision)

        referenced = set(down_revisions)
        heads = [revision for revision in revisions if revision not in referenced]
        assert heads == [migration.revision], f"迁移链 head 不唯一：{heads}"

    def test_upgrade_creates_table_indexes_and_constraints_on_sqlite(self, tmp_path: Path) -> None:
        migration = _citation_style_migration()
        engine = sa.create_engine(f"sqlite:///{(tmp_path / 'upgrade.db').as_posix()}")
        try:
            with engine.begin() as connection:
                context = MigrationContext.configure(connection)
                with Operations.context(context):
                    migration.upgrade()

                inspector = sa.inspect(connection)
                assert "citation_style" in inspector.get_table_names()

                columns = {column["name"] for column in inspector.get_columns("citation_style")}
                assert {
                    "id",
                    "user_id",
                    "style_id",
                    "title",
                    "numbering",
                    "ordering",
                    "default_locale",
                    "filename",
                    "content_sha256",
                    "size_bytes",
                    "created_at",
                    "updated_at",
                } <= columns

                nullable = {
                    column["name"]: column["nullable"]
                    for column in inspector.get_columns("citation_style")
                }
                assert nullable["user_id"] is True  # NULL = 系统预置

                unique_names = {
                    constraint["name"]
                    for constraint in inspector.get_unique_constraints("citation_style")
                }
                assert "uq_citation_style_user_style_id" in unique_names

                index_names = {index["name"] for index in inspector.get_indexes("citation_style")}
                assert {"ix_citation_style_user_id", "uq_citation_style_preset_style_id"} <= (
                    index_names
                )

                ddl = connection.exec_driver_sql(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='citation_style'"
                ).scalar_one()
                assert "REFERENCES user" in ddl
                assert "ON DELETE CASCADE" in ddl

                partial_index_ddl = connection.exec_driver_sql(
                    "SELECT sql FROM sqlite_master WHERE type='index' "
                    "AND name='uq_citation_style_preset_style_id'"
                ).scalar_one()
                assert "UNIQUE" in partial_index_ddl.upper()
                assert "user_id IS NULL" in partial_index_ddl
        finally:
            engine.dispose()

    def test_downgrade_removes_table(self, tmp_path: Path) -> None:
        migration = _citation_style_migration()
        engine = sa.create_engine(f"sqlite:///{(tmp_path / 'roundtrip.db').as_posix()}")
        try:
            with engine.begin() as connection:
                context = MigrationContext.configure(connection)
                with Operations.context(context):
                    migration.upgrade()
                assert "citation_style" in sa.inspect(connection).get_table_names()

                with Operations.context(context):
                    migration.downgrade()

                assert "citation_style" not in sa.inspect(connection).get_table_names()
                remaining = connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE tbl_name='citation_style'"
                ).all()
                assert remaining == []
        finally:
            engine.dispose()

    def test_downgrade_is_idempotent_after_upgrade_downgrade_upgrade(self, tmp_path: Path) -> None:
        migration = _citation_style_migration()
        engine = sa.create_engine(f"sqlite:///{(tmp_path / 'twice.db').as_posix()}")
        try:
            with engine.begin() as connection:
                context = MigrationContext.configure(connection)
                for _ in range(2):
                    with Operations.context(context):
                        migration.upgrade()
                    assert "citation_style" in sa.inspect(connection).get_table_names()
                    with Operations.context(context):
                        migration.downgrade()
                    assert "citation_style" not in sa.inspect(connection).get_table_names()
        finally:
            engine.dispose()
