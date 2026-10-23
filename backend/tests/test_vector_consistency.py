"""向量库一致性校验测试（2026-10-22 向量一致性 Task 1）。

## 为什么需要这个模块

实测发现：SQLite 有 104 篇文档 / 28344 个分块 / 28344 条 `vector_index`，
但 Chroma 实际只有 **4** 条向量。`index_status` 全部是 `ready` ——
**数据库在撒谎**。

后果：向量检索对所有用户完全失效，检索退化为 BM25 单路。而这个问题
是「实验跑不出引用标记」才偶然追查到的。

**应该有一个可随时运行的检查**，而不是靠碰运气。

## 本文件锁定

> 本文件是「**只比数量**」的历史用例（`verify_ids=False`）。
> id 级校验（数量相等但 id 不同 → 仍报不一致）由
> `test_vector_consistency_ids.py` 覆盖。

1. 一致时返回 `consistent=True`；
2. 不一致时准确报出 `expected` / `actual` / 缺失数量；
3. 能识别**孤儿向量**（Chroma 有、SQLite 没有）；
4. 空库不算异常（新部署场景）。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.knowledge.services.consistency import (
    VectorConsistencyReport,
    check_vector_consistency,
)


class _FakeVectorStore:
    """只实现 size 的假向量库。"""

    def __init__(self, count: int) -> None:
        self._count = count
        self.calls = 0

    def size(self) -> int:
        self.calls += 1
        return self._count


class _FakeSession:
    """最小 session 替身：返回预置的 (doc_count, chunk_count)。"""

    def __init__(self, *, docs: int, chunks: int) -> None:
        self._docs = docs
        self._chunks = chunks

    async def scalar(self, _stmt: Any) -> int:
        # 第一次查 docs，第二次查 chunks（顺序由实现决定，这里用计数器区分）
        if not hasattr(self, "_served_docs"):
            self._served_docs = True
            return self._docs
        return self._chunks


class TestConsistentCase:
    @pytest.mark.asyncio
    async def test_reports_consistent_when_counts_match(self) -> None:
        report = await check_vector_consistency(
            session=_FakeSession(docs=2, chunks=100),
            vector_store=_FakeVectorStore(100),
            verify_ids=False,
        )

        assert isinstance(report, VectorConsistencyReport)
        assert report.consistent is True
        assert report.expected_vectors == 100
        assert report.actual_vectors == 100
        assert report.missing_vectors == 0

    @pytest.mark.asyncio
    async def test_empty_kb_is_consistent(self) -> None:
        """空库不算异常（新部署/清库后重建前）。"""
        report = await check_vector_consistency(
            session=_FakeSession(docs=0, chunks=0),
            vector_store=_FakeVectorStore(0),
            verify_ids=False,
        )

        assert report.consistent is True
        assert report.expected_vectors == 0


class TestInconsistentCase:
    @pytest.mark.asyncio
    async def test_detects_missing_vectors(self) -> None:
        """**核心场景**：数据库说 28344 条，Chroma 只有 4 条。"""
        report = await check_vector_consistency(
            session=_FakeSession(docs=104, chunks=28344),
            vector_store=_FakeVectorStore(4),
            verify_ids=False,
        )

        assert report.consistent is False
        assert report.expected_vectors == 28344
        assert report.actual_vectors == 4
        assert report.missing_vectors == 28340

    @pytest.mark.asyncio
    async def test_detects_orphan_vectors(self) -> None:
        """Chroma 有、SQLite 没有的向量也是问题（孤儿数据）。"""
        report = await check_vector_consistency(
            session=_FakeSession(docs=1, chunks=10),
            vector_store=_FakeVectorStore(14),
            verify_ids=False,
        )

        assert report.consistent is False
        assert report.actual_vectors == 14
        assert report.expected_vectors == 10
        assert report.orphan_vectors == 4

    @pytest.mark.asyncio
    async def test_report_has_actionable_hint(self) -> None:
        """报告要告诉运维「怎么修」，不能只报数字。"""
        report = await check_vector_consistency(
            session=_FakeSession(docs=104, chunks=28344),
            vector_store=_FakeVectorStore(4),
            verify_ids=False,
        )

        assert report.hint
        assert "reindex" in report.hint.lower() or "重建" in report.hint

    @pytest.mark.asyncio
    async def test_report_serializable(self) -> None:
        """报告要能直接打日志/JSON 输出。"""
        report = await check_vector_consistency(
            session=_FakeSession(docs=104, chunks=28344),
            vector_store=_FakeVectorStore(4),
            verify_ids=False,
        )

        payload = report.as_dict()

        assert payload["consistent"] is False
        assert payload["expected_vectors"] == 28344
        assert payload["actual_vectors"] == 4
        assert isinstance(payload["hint"], str)
