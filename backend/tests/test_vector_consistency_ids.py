"""一致性校验必须检测 **id 级** 漂移，不能只比数量（2026-10-23）。

## 缺陷（我上一轮引入的漏检）

`check_vector_consistency` 只比对「分块数」与「向量库条数」。

实测踩到：reindex 后 Chroma 有 28344 条 UUID，`vector_index.chroma_id` 是
28344 条 16 位 hex —— **交集为 0**，但**数量相等** → 校验报「一致」。

这是最危险的一类漏检：**数量对得上，数据全错**。

## 本文件锁定

1. 数量一致但 id 集合不一致 → 必须报 `consistent=False`；
2. 报告要给出 `orphan_ids` / `missing_ids` 的数量；
3. 数量不一致时也要报 id 级信息（不重复扫库两次）；
4. 抽样校验要能配置（全量比对 28344 条会慢，但运维命令可接受）。
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.knowledge.services.consistency import (
    VectorConsistencyReport,
    check_vector_consistency,
)


class _FakeVectorStore:
    """提供 `size` 属性与 `list_ids()`。"""

    def __init__(self, ids: list[str]) -> None:
        self._ids = ids
        self.size = len(ids)

    def list_ids(self) -> list[str]:
        return list(self._ids)


class _FakeSession:
    def __init__(self, *, docs: int, chunks: int, chroma_ids: list[str]) -> None:
        self._docs = docs
        self._chunks = chunks
        self._chroma_ids = chroma_ids
        self._call = 0

    async def scalar(self, _stmt: Any) -> int:
        self._call += 1
        return self._docs if self._call == 1 else self._chunks

    async def execute(self, _stmt: Any) -> Any:
        ids = self._chroma_ids

        class _Result:
            def all(self) -> list[Any]:
                return [(cid,) for cid in ids]

            def scalars(self) -> Any:
                return self

            def __iter__(self) -> Any:
                return iter([(cid,) for cid in ids])

        return _Result()


class TestIdLevelDrift:
    @pytest.mark.asyncio
    async def test_detects_id_drift_despite_equal_count(self) -> None:
        """**核心断言**：数量相等但 id 完全不同 → 不一致。

        这正是实测场景：28344 个 UUID（Chroma）vs 28344 个 16 位 hex（DB）。
        """
        chroma_ids = [str(uuid.uuid4()) for _ in range(3)]
        db_ids = ["a" * 16, "b" * 16, "c" * 16]  # 数量相同、id 完全不同
        store = _FakeVectorStore(chroma_ids)
        session = _FakeSession(docs=1, chunks=3, chroma_ids=db_ids)

        report = await check_vector_consistency(
            session=session, vector_store=store, verify_ids=True
        )

        assert report.expected_vectors == 3
        assert report.actual_vectors == 3
        assert report.consistent is False, (
            "数量相等但 id 集合完全不同，必须报不一致 —— "
            "只比数量会让「数量对得上、数据全错」的漂移静默通过"
        )
        assert report.missing_vectors == 3
        assert report.orphan_vectors == 3

    @pytest.mark.asyncio
    async def test_ids_match_reports_consistent(self) -> None:
        ids = [str(uuid.uuid4()) for _ in range(3)]
        store = _FakeVectorStore(ids)
        session = _FakeSession(docs=1, chunks=3, chroma_ids=ids)

        report = await check_vector_consistency(
            session=session, vector_store=store, verify_ids=True
        )

        assert report.consistent is True
        assert report.missing_vectors == 0
        assert report.orphan_vectors == 0

    @pytest.mark.asyncio
    async def test_partial_drift(self) -> None:
        """部分漂移：2 条对、1 条不对。"""
        good = [str(uuid.uuid4()), str(uuid.uuid4())]
        chroma_ids = [*good, "f" * 16]
        db_ids = [*good, "e" * 16]
        store = _FakeVectorStore(chroma_ids)
        session = _FakeSession(docs=1, chunks=3, chroma_ids=db_ids)

        report = await check_vector_consistency(
            session=session, vector_store=store, verify_ids=True
        )

        assert report.consistent is False
        assert report.missing_vectors == 1
        assert report.orphan_vectors == 1


class TestBackwardCompatible:
    @pytest.mark.asyncio
    async def test_count_only_when_verify_ids_false(self) -> None:
        """`verify_ids=False` 时保持原有行为（只比数量，快）。"""
        store = _FakeVectorStore(["x", "y", "z"])
        session = _FakeSession(docs=1, chunks=3, chroma_ids=["a" * 16] * 3)

        report = await check_vector_consistency(
            session=session, vector_store=store, verify_ids=False
        )

        assert report.consistent is True, "未开启 id 校验时只比数量"


class TestReportShape:
    @pytest.mark.asyncio
    async def test_report_exposes_id_lists(self) -> None:
        store = _FakeVectorStore([str(uuid.uuid4())])
        session = _FakeSession(docs=1, chunks=1, chroma_ids=["d" * 16])

        report = await check_vector_consistency(
            session=session, vector_store=store, verify_ids=True
        )

        payload = report.as_dict()

        assert payload["consistent"] is False
        assert "missing_ids" in payload
        assert "orphan_ids" in payload
        assert isinstance(report, VectorConsistencyReport)

    @pytest.mark.asyncio
    async def test_id_lists_capped(self) -> None:
        """id 列表要有上限（28344 条全塞进报告会撑爆日志）。"""
        store = _FakeVectorStore([f"{i:016x}" for i in range(200)])
        session = _FakeSession(
            docs=1, chunks=200, chroma_ids=[f"{i + 1000:016x}" for i in range(200)]
        )

        report = await check_vector_consistency(
            session=session, vector_store=store, verify_ids=True
        )

        assert len(report.missing_ids) <= 20, "id 样本要有上限"
        assert len(report.orphan_ids) <= 20
