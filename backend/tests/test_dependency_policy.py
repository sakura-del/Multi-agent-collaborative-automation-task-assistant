"""依赖策略：会引入「上游无法修复」漏洞的包只能做可选依赖（Issue #11 M-5）。

## 背景（2026-11-12 用 OSV 实测）

生产镜像里 11 个被点名的包里，只剩 3 个仍有公告，而且**上游都没有修复版本**
（`fixed_in = None`）：

| 包 | 公告 | 能否修 | 本项目的处置 |
| --- | --- | --- | --- |
| `chromadb` 1.5.9 | 4 个 CVE（矩阵见 `docs/security/dependency-risk.md`） | 不能 | 移出主依赖，改成可选 `[chroma]`，**生产镜像不装** |
| `python-jose` 3.5.0 | CVE-2024-33663（算法混淆） | 不能 | 无法替换 JWT 库 → 锁死 `algorithms=[HS256]`（已有测试） |
| `ecdsa` 0.19.2 | CVE-2024-23342（Minerva 计时侧信道） | 不能（维护者明确不修） | 只用 HS256，ES 系列算法分支不可达 |

`certifi` / `urllib3` / `aiohttp` / `cryptography` 等已随版本漂移升到无公告版本，
审计报告里那张表已经过期——**判断依赖风险要现查 OSV，不要抄历史结论**。

## 本文件锁住什么

1. `chromadb` 不得回到主依赖（否则生产镜像又带上 4 个无法修的 CVE）；
2. 可选依赖 `[chroma]` 必须存在（本地开发/回滚要用）；
3. 缺包时 chroma 后端要给出可照做的提示，而不是裸 `ModuleNotFoundError`；
4. `vector_backend` 默认必须是 `pgvector`——镜像里没有 chromadb，默认指向 chroma
   只会在第一次向量操作时炸。
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


@pytest.fixture(scope="module")
def project_meta() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


class TestDependencySplit:
    def test_chromadb_is_not_a_main_dependency(self, project_meta: dict) -> None:
        deps = project_meta["project"]["dependencies"]
        offenders = [d for d in deps if d.lower().startswith("chromadb")]
        assert offenders == [], (
            "chromadb 不能再回到主依赖：它带着 4 个上游未修复的 CVE，"
            f"生产镜像不该装。请放到 [project.optional-dependencies].chroma。实际：{offenders}"
        )

    def test_chroma_extra_exists_for_local_dev(self, project_meta: dict) -> None:
        extras = project_meta["project"]["optional-dependencies"]
        assert "chroma" in extras, "本地开发/回滚仍需要 [chroma] 可选依赖"
        assert any(d.lower().startswith("chromadb") for d in extras["chroma"])

    def test_vector_backend_defaults_to_pgvector(self) -> None:
        """默认值看**配置默认**（`config/default.yaml`）。

        运行时 `settings.rag.vector_backend` 在测试里会被 conftest 的
        `RAG__VECTOR_BACKEND=chroma` 覆盖（单测走内存模式），所以这里读 YAML。
        """
        yaml_path = PYPROJECT.parents[1] / "config" / "default.yaml"
        text = yaml_path.read_text(encoding="utf-8")
        assert (
            'vector_backend: "pgvector"' in text
        ), "生产镜像不含 chromadb，配置默认后端必须是 pgvector"


class TestChromaBackendFailsHelpfully:
    def test_missing_package_gives_actionable_error(self, monkeypatch) -> None:
        """模拟生产镜像（没有 chromadb）：报错必须能告诉人怎么办。"""
        from app.knowledge.services.vector_backends.chroma_backend import ChromaVectorBackend

        # 让 `import chromadb` 抛 ModuleNotFoundError：sys.modules 置 None 是标准做法
        monkeypatch.setitem(sys.modules, "chromadb", None)

        backend = ChromaVectorBackend(persist_directory="")
        with pytest.raises(RuntimeError) as excinfo:
            backend._ensure_collection()

        message = str(excinfo.value)
        assert ".[chroma]" in message
        assert "pgvector" in message
