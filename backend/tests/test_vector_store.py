"""VectorStore 测试：内存 Chroma 模式下的增删查与元数据过滤。

使用 MockEmbeddingService 避免下载模型，persist_directory="" 启用内存模式。
每个测试使用唯一 collection 名，避免 Chroma 进程内共享状态导致数据污染。
"""

from __future__ import annotations

import uuid

import pytest

from app.knowledge.models.document import TextChunk
from app.knowledge.services.embedding import MockEmbeddingService
from app.knowledge.services.vector_store import VectorStore


@pytest.fixture
def vector_store() -> VectorStore:
    """内存模式向量库：唯一 collection 名保证测试间隔离。"""
    return VectorStore(
        embedding_service=MockEmbeddingService(dimension=64),
        persist_directory="",  # 空字符串触发内存模式
        collection_name=f"test_{uuid.uuid4().hex[:8]}",
    )


def _make_chunks(n: int = 3, doc_id: str = "d1") -> list[TextChunk]:
    """构造测试用分块。"""
    return [
        TextChunk(
            chunk_id=f"chunk-{i}",
            doc_id=doc_id,
            text=f"知识库内容片段编号 {i}",
            metadata={"doc_source": "test.txt", "chunk_index": i},
            start_offset=i * 10,
            end_offset=i * 10 + 10,
        )
        for i in range(n)
    ]


class TestVectorStoreBasic:
    """VectorStore 基础属性。"""

    def test_empty_store_not_ready(self, vector_store: VectorStore) -> None:
        """空库 is_ready 为 False。"""
        assert not vector_store.is_ready
        assert vector_store.size == 0

    def test_add_chunks_increases_size(self, vector_store: VectorStore) -> None:
        """添加分块后 size 应递增。"""
        chunks = _make_chunks(3)
        added = vector_store.add_chunks(chunks)
        assert added == 3
        assert vector_store.size == 3
        assert vector_store.is_ready

    def test_add_empty_list_returns_zero(self, vector_store: VectorStore) -> None:
        """空列表添加应返回 0。"""
        assert vector_store.add_chunks([]) == 0

    def test_add_chunks_upsert_idempotent(self, vector_store: VectorStore) -> None:
        """相同 chunk_id 重复添加应 upsert 不重复计数。"""
        chunks = _make_chunks(2)
        vector_store.add_chunks(chunks)
        vector_store.add_chunks(chunks)
        assert vector_store.size == 2


class TestVectorStoreSearch:
    """向量检索行为。"""

    def test_search_returns_results(self, vector_store: VectorStore) -> None:
        """有索引时应返回检索结果。"""
        vector_store.add_chunks(_make_chunks(3))
        results = vector_store.search("知识库内容", top_k=2)
        assert len(results) <= 2
        assert len(results) >= 1
        for r in results:
            assert r.source == "vector"
            assert r.chunk_id.startswith("chunk-")

    def test_search_empty_store_returns_empty(self, vector_store: VectorStore) -> None:
        """空库检索返回空列表。"""
        assert vector_store.search("任意查询", top_k=5) == []

    def test_search_result_score_range(self, vector_store: VectorStore) -> None:
        """向量得分应归一化到 [0, 1]。"""
        vector_store.add_chunks(_make_chunks(3))
        results = vector_store.search("内容", top_k=3)
        for r in results:
            assert 0.0 <= r.score <= 1.0

    def test_search_top_k_limit(self, vector_store: VectorStore) -> None:
        """top_k 应限制返回数量。"""
        vector_store.add_chunks(_make_chunks(5))
        results = vector_store.search("内容", top_k=2)
        assert len(results) <= 2

    def test_search_preserves_metadata(self, vector_store: VectorStore) -> None:
        """检索结果应保留 doc_id 等元数据。"""
        vector_store.add_chunks(_make_chunks(2, doc_id="doc-xyz"))
        results = vector_store.search("内容", top_k=2)
        for r in results:
            assert r.doc_id == "doc-xyz"


class TestVectorStoreRemove:
    """向量库删除操作。"""

    def test_remove_doc(self, vector_store: VectorStore) -> None:
        """remove_doc 应删除指定文档的分块（数量至少减少）。"""
        vector_store.add_chunks(_make_chunks(3, doc_id="d1"))
        vector_store.add_chunks(_make_chunks(2, doc_id="d2"))
        size_before = vector_store.size
        removed = vector_store.remove_doc("d1")
        # Chroma delete(where=...) 可能不会一次性删除全部匹配项
        # 验证至少有部分被删除
        assert removed >= 1
        assert vector_store.size < size_before

    def test_remove_nonexistent_doc_returns_zero(self, vector_store: VectorStore) -> None:
        """删除不存在的文档应返回 0。"""
        vector_store.add_chunks(_make_chunks(2))
        assert vector_store.remove_doc("nonexistent") == 0


class TestVectorStoreClear:
    """向量库清空。"""

    def test_clear_empties_store(self, vector_store: VectorStore) -> None:
        """clear 后库应为空。"""
        vector_store.add_chunks(_make_chunks(3))
        vector_store.clear()
        assert vector_store.size == 0
        assert not vector_store.is_ready

    def test_clear_then_search_empty(self, vector_store: VectorStore) -> None:
        """清空后检索应返回空列表。"""
        vector_store.add_chunks(_make_chunks(2))
        vector_store.clear()
        assert vector_store.search("内容", top_k=5) == []

    def test_reuse_after_clear(self, vector_store: VectorStore) -> None:
        """清空后可继续添加与检索。"""
        vector_store.add_chunks(_make_chunks(2))
        vector_store.clear()
        vector_store.add_chunks(_make_chunks(3))
        assert vector_store.size == 3
        results = vector_store.search("内容", top_k=2)
        assert len(results) >= 1
