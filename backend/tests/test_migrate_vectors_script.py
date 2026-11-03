"""Chroma → pgvector 迁移脚本的纯逻辑部分。

只测不依赖外部服务的函数：分页读取、脏行计数、id 比对、重叠率。
真实搬运在 `--dry-run` 与服务器上按 runbook 执行，不放进单测。
"""

from __future__ import annotations

from typing import Any

from scripts.migrate_vectors_to_pgvector import (
    Batch,
    build_batch,
    compare_ids,
    iter_batches,
    overlap_ratio,
)


class _FakeCollection:
    """按 offset/limit 切片的假 collection（真实 Chroma 的 get 语义）。"""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.calls: list[dict[str, Any]] = []

    def get(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        offset = int(kwargs.get("offset") or 0)
        limit = int(kwargs.get("limit") or len(self._rows))
        chunk = self._rows[offset : offset + limit]
        return {
            "ids": [row["id"] for row in chunk],
            "embeddings": [row["embedding"] for row in chunk],
            "documents": [row["document"] for row in chunk],
            "metadatas": [row["metadata"] for row in chunk],
        }


def _rows(n: int) -> list[dict[str, Any]]:
    return [
        {
            "id": f"c{i}",
            "embedding": [float(i), 0.0, 0.0],
            "document": f"正文{i}",
            "metadata": {"doc_id": f"d{i}", "user_id": "u1", "chunk_index": i},
        }
        for i in range(n)
    ]


class TestBuildBatch:
    def test_skips_rows_without_embedding_but_counts_them(self) -> None:
        """缺向量的行必须计数：静默丢一条 = 一条证据永久搜不到。"""
        payload = {
            "ids": ["a", "b", "c"],
            "embeddings": [[1.0, 0.0], None, [0.0, 1.0]],
            "documents": ["正文a", "正文b", "正文c"],
            "metadatas": [{"doc_id": "d1"}, None, {"doc_id": "d3"}],
        }

        batch = build_batch(payload)

        assert batch.ids == ["a", "c"]
        assert batch.skipped == 1
        assert batch.metadatas[1] == {"doc_id": "d3"}

    def test_missing_embeddings_marks_all_skipped(self) -> None:
        batch = build_batch({"ids": ["a", "b"], "embeddings": None})
        assert len(batch) == 0
        assert batch.skipped == 2

    def test_converts_numpy_like_vectors(self) -> None:
        class _Vec:
            def tolist(self) -> list[float]:
                return [0.5, 0.25]

        batch = build_batch(
            {"ids": ["a"], "embeddings": [_Vec()], "documents": ["x"], "metadatas": [{}]}
        )
        assert batch.vectors == [[0.5, 0.25]]


class TestIterBatches:
    def test_paginates_until_empty(self) -> None:
        collection = _FakeCollection(_rows(5))
        batches = list(iter_batches(collection, batch_size=2))

        assert [len(b) for b in batches] == [2, 2, 1]
        assert collection.calls[0]["offset"] == 0
        assert collection.calls[1]["offset"] == 2
        # 必须以「取到空页」作为终止条件（不是靠返回条数推断）：
        # 多一次空查询换取「不会少搬最后一页」。
        assert collection.calls[-1]["offset"] == 5

    def test_start_offset_resumes_midway(self) -> None:
        collection = _FakeCollection(_rows(6))
        batches = list(iter_batches(collection, batch_size=2, start_offset=4))

        assert [b.ids for b in batches] == [["c4", "c5"]]

    def test_empty_collection_yields_nothing(self) -> None:
        assert list(iter_batches(_FakeCollection([]), batch_size=10)) == []


class TestIdComparison:
    def test_missing_and_orphan(self) -> None:
        missing, orphan = compare_ids(["a", "b", "c"], ["b", "c", "d"])

        assert missing == ["a"]
        assert orphan == ["d"]

    def test_samples_are_truncated(self) -> None:
        missing, _ = compare_ids([f"m{i}" for i in range(100)], [])
        assert len(missing) == 20

    def test_identical_sets(self) -> None:
        missing, orphan = compare_ids(["a", "b"], ["b", "a"])
        assert (missing, orphan) == ([], [])


class TestOverlap:
    def test_full_overlap(self) -> None:
        assert overlap_ratio(["a", "b", "c"], ["c", "b", "a"]) == 1.0

    def test_partial_overlap(self) -> None:
        assert overlap_ratio(["a", "b", "c", "d"], ["a", "b"]) == 0.5

    def test_one_side_empty_is_zero(self) -> None:
        assert overlap_ratio(["a"], []) == 0.0
        assert overlap_ratio([], ["a"]) == 0.0

    def test_both_empty_is_one(self) -> None:
        assert overlap_ratio([], []) == 1.0


class TestBatchDataclass:
    def test_length_is_ids_length(self) -> None:
        batch = Batch(
            ids=["a", "b"], vectors=[[0.0], [0.0]], documents=["", ""], metadatas=[{}, {}]
        )
        assert len(batch) == 2
