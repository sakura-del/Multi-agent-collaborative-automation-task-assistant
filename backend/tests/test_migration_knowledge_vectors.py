"""迁移链完整性：`knowledge_vectors` 迁移必须接在当前 head 之后且只有一个 head。

为什么值得单独测：迁移链断了（down_revision 写错 / 出现两个 head）时，
`alembic upgrade head` 会直接报错——而这通常要到部署时才暴露。
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND_DIR = Path(__file__).resolve().parents[1]
NEW_REVISION = "d5a8c1e3f7b2"


def _script() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(str(BACKEND_DIR / "alembic.ini")))


class TestMigrationChain:
    def test_single_head(self) -> None:
        assert _script().get_heads() == [NEW_REVISION]

    def test_new_revision_follows_reference_library(self) -> None:
        revision = _script().get_revision(NEW_REVISION)
        assert revision is not None
        assert revision.down_revision == "b7d4e9f1c6a2"

    def test_revision_file_is_present(self) -> None:
        versions = BACKEND_DIR / "alembic" / "versions"
        assert any(p.name.startswith(NEW_REVISION) for p in versions.glob("*.py"))
