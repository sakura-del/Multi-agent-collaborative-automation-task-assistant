"""VectorStore 门面的后端选择规则（迁移的安全性前提）。

优先级刻意设计成「显式 > 测试语义 > 配置 > 兜底」：

1. `backend=` 显式指定；
2. `persist_directory is not None` → chroma（12 个既有测试文件靠这条走内存模式）；
3. `RAG__VECTOR_BACKEND`；
4. 兜底 chroma。

第 2 条与第 3 条的先后不能反：反了以后，只要部署环境配了 pgvector，
所有离线单测都会去连外部数据库。
"""

from __future__ import annotations

import pytest

from app.knowledge.services.embedding import MockEmbeddingService
from app.knowledge.services.vector_backends.base import VectorStoreConfigError
from app.knowledge.services.vector_store import VectorStore, distance_to_score


def _store(**kwargs: object) -> VectorStore:
    return VectorStore(embedding_service=MockEmbeddingService(dimension=8), **kwargs)  # type: ignore[arg-type]


class TestBackendSelection:
    def test_persist_directory_empty_still_uses_chroma_memory(self) -> None:
        """既有测试语义：`persist_directory=""` 必须继续走 Chroma 内存模式。"""
        assert _store(persist_directory="").backend_name == "chroma"

    def test_explicit_backend_wins_over_persist_directory(self) -> None:
        """回滚/灰度需要显式指定时，不能被 persist_directory 覆盖。"""
        assert _store(persist_directory="", backend="pgvector").backend_name == "pgvector"

    def test_explicit_backend_is_case_insensitive(self) -> None:
        assert _store(backend="PGVector").backend_name == "pgvector"

    def test_unknown_backend_fails_fast(self) -> None:
        """配置写错必须立刻报错，不能悄悄回退（否则「以为迁了其实没迁」）。"""
        with pytest.raises(VectorStoreConfigError):
            _store(backend="pgsql")

    def test_configured_backend_is_used_when_nothing_explicit(self, monkeypatch) -> None:
        """未显式指定时读 `RAG__VECTOR_BACKEND`。"""
        from app.core.config import get_settings

        rag = get_settings().rag
        monkeypatch.setattr(rag, "vector_backend", "pgvector", raising=False)
        assert _store(chroma_host="chroma", chroma_port=8000).backend_name == "pgvector"

    def test_pgvector_construction_does_not_connect(self) -> None:
        """构造必须惰性：缺 DSN / 网络不通不能在 import 或构造期炸掉应用启动。"""
        store = _store(backend="pgvector")
        assert store.backend_name == "pgvector"
        assert store.collection_name == "knowledge_base"


class TestFacadeBehaviourIsBackendAgnostic:
    def test_empty_chunks_returns_zero_without_touching_backend(self) -> None:
        store = _store(backend="pgvector")
        assert store.add_chunks([]) == 0


class TestScoreConversion:
    """得分换算：两个后端共用同一处实现（换后端不能改变分数语义）。"""

    def test_identical_vector_scores_one(self) -> None:
        assert distance_to_score(0.0) == pytest.approx(1.0)

    def test_orthogonal_vector_scores_zero(self) -> None:
        """正交（余弦距离 1）→ 得分 0，与 Chroma 时代一致。"""
        assert distance_to_score(1.0) == pytest.approx(0.0)

    def test_opposite_vector_clamped_to_zero(self) -> None:
        """余弦距离可达 2（完全相反），得分必须截断为 0 而不是 -1。"""
        assert distance_to_score(2.0) == 0.0
        assert distance_to_score(1.5) == 0.0
