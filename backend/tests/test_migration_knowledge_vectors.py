"""迁移链完整性与「无 pgvector 时不得崩溃」回归。

为什么两件事都要测：

1. 迁移链断了（down_revision 写错 / 出现两个 head）时，`alembic upgrade head`
   会直接报错——通常要到部署时才暴露；
2. **迁移必须能在没有 pgvector 的 PostgreSQL 上跑完**：本地与 CI 用的是
   `postgres:16-alpine`（不含 pgvector），而容器 entrypoint 启动即执行
   `alembic upgrade head`，硬失败会让后端**崩溃循环**（2026-11-03 部署实测：
   `extension "vector" is not available`，整站 502）。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND_DIR = Path(__file__).resolve().parents[1]
NEW_REVISION = "d5a8c1e3f7b2"
# 迁移链单一 head 的守卫：新增迁移后更新这里（当前 head = revoked_refresh_token）
EXPECTED_HEAD = "b8e4f2a6c9d1"
MIGRATION_FILE = BACKEND_DIR / "alembic" / "versions" / f"{NEW_REVISION}_add_knowledge_vectors.py"


def _script() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(str(BACKEND_DIR / "alembic.ini")))


def _migration_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("mig_knowledge_vectors", MIGRATION_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestMigrationChain:
    def test_single_head(self) -> None:
        assert _script().get_heads() == [EXPECTED_HEAD]

    def test_new_revision_follows_reference_library(self) -> None:
        revision = _script().get_revision(NEW_REVISION)
        assert revision is not None
        assert revision.down_revision == "b7d4e9f1c6a2"

    def test_revision_file_is_present(self) -> None:
        versions = BACKEND_DIR / "alembic" / "versions"
        assert any(p.name.startswith(NEW_REVISION) for p in versions.glob("*.py"))


class _FakeResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar(self) -> Any:
        return self._value


class _FakeBind:
    """按脚本依次返回查询结果，并记录执行过的 SQL。"""

    class dialect:
        name = "postgresql"

    def __init__(self, script: list[Any]) -> None:
        self._script = list(script)
        self.executed: list[str] = []

    def execute(self, statement: Any, params: Any = None) -> _FakeResult:
        text = str(statement)
        self.executed.append(text)
        # 只有目录查询消费脚本；DDL 不消费（否则建表会把后面的探测结果吃掉）
        if "pg_extension" in text or "pg_available_extensions" in text:
            return _FakeResult(self._script.pop(0) if self._script else None)
        return _FakeResult(None)


class _FakeOp:
    def __init__(self, bind: _FakeBind) -> None:
        self._bind = bind

    def get_bind(self) -> _FakeBind:
        return self._bind


class TestUpgradeWithoutPgvector:
    def test_noops_instead_of_crashing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """服务端没有 pgvector 安装包 → 只告警并跳过建表，不得抛异常。"""
        module = _migration_module()
        bind = _FakeBind([None, None])  # 没装、也装不了
        monkeypatch.setattr(module, "op", _FakeOp(bind))

        module.upgrade()  # 不应抛异常

        assert not any("CREATE TABLE" in sql for sql in bind.executed)
        assert not any("CREATE EXTENSION" in sql for sql in bind.executed)

    def test_creates_table_when_extension_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """扩展可安装时照常建表（这是 Supabase 的路径）。"""
        module = _migration_module()
        bind = _FakeBind([None, 1, "extensions"])
        monkeypatch.setattr(module, "op", _FakeOp(bind))

        module.upgrade()

        joined = " ".join(bind.executed)
        assert "CREATE EXTENSION" in joined
        assert "CREATE TABLE IF NOT EXISTS knowledge_vectors" in joined
        assert "ix_kv_embedding_hnsw" in joined
