"""BGE 影子向量表迁移：链头、扩展降级与独立索引回归。"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND_DIR = Path(__file__).resolve().parents[1]
REVISION = "c4f2a8d7e9b1"
MIGRATION_FILE = BACKEND_DIR / "alembic" / "versions" / f"{REVISION}_add_knowledge_vectors_bge.py"


def _script() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(str(BACKEND_DIR / "alembic.ini")))


def _migration_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("mig_knowledge_vectors_bge", MIGRATION_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar(self) -> Any:
        return self._value


class _FakeBind:
    class dialect:
        name = "postgresql"

    def __init__(self, script: list[Any]) -> None:
        self._script = list(script)
        self.executed: list[str] = []

    def execute(self, statement: Any, params: Any = None) -> _FakeResult:
        text = str(statement)
        self.executed.append(text)
        if "pg_extension" in text or "pg_available_extensions" in text:
            return _FakeResult(self._script.pop(0) if self._script else None)
        return _FakeResult(None)


class _FakeOp:
    def __init__(self, bind: _FakeBind) -> None:
        self._bind = bind

    def get_bind(self) -> _FakeBind:
        return self._bind


def test_shadow_follows_current_pgvector_revision() -> None:
    revision = _script().get_revision(REVISION)
    assert revision is not None
    assert revision.down_revision == "d5a8c1e3f7b2"


def test_noops_when_pgvector_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _migration_module()
    bind = _FakeBind([None, None])
    monkeypatch.setattr(module, "op", _FakeOp(bind))

    module.upgrade()

    assert not any("CREATE TABLE" in sql for sql in bind.executed)
    assert not any("CREATE EXTENSION" in sql for sql in bind.executed)


def test_creates_shadow_with_independent_indexes(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _migration_module()
    bind = _FakeBind([None, 1, "extensions"])
    monkeypatch.setattr(module, "op", _FakeOp(bind))

    module.upgrade()

    joined = " ".join(bind.executed)
    assert "CREATE EXTENSION" in joined
    assert "CREATE TABLE IF NOT EXISTS knowledge_vectors_bge" in joined
    assert '"extensions"."vector"(1024)' in joined
    assert "ix_kvbge_embedding_hnsw" in joined
    assert "ix_kvbge_collection_doc" in joined
    assert "ix_kvbge_collection_user" in joined
    assert "ix_kv_embedding_hnsw" not in joined


def test_downgrade_drops_only_shadow_table(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _migration_module()
    bind = _FakeBind([])
    monkeypatch.setattr(module, "op", _FakeOp(bind))

    module.downgrade()

    assert len(bind.executed) == 1
    assert "DROP TABLE IF EXISTS knowledge_vectors_bge" in bind.executed[0]
    assert "DROP EXTENSION" not in bind.executed[0]
    assert "DROP TABLE IF EXISTS knowledge_vectors\n" not in bind.executed[0]
