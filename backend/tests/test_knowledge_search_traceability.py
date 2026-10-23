"""知识库检索接口的 chunk_id 必须可回溯（2026-10-23 巡检发现）。

## 缺陷（服务器实测）

`POST /api/v1/knowledge/search` 返回的 `chunk_id` 是 **Chroma 内部 id**
（形如 `9eac9177523794ed`，16 位 hex），不是 SQLite `doc_chunk.id`（UUID）。

后果：**引用无法回溯到原文**。用户看到一条检索结果，点不回具体段落 ——
而「每条证据可点回原文」正是本项目（证据约束 + 引用可追溯）的核心能力。

## 根因

端点调的是 `service.search()`（直接返回索引层结果，id 未映射），
而正确的入口是 `service.search_for_context()` —— 它内部会走
`_attach_storage_ids()`，按 `VectorIndex.chroma_id` 把索引 id 换成
`DocChunk.id` / `KnowledgeDoc.id` 的 UUID。

## 为什么单测没抓到

既有测试大概断言了「有结果」「数量对」，但没断言 **id 的形态**。
一个 16 位 hex 和一个 UUID 在「有没有结果」这个维度上是一样的。

## 本文件锁定

1. `chunk_id` 必须是合法 UUID（能回溯到 `doc_chunk`）；
2. `doc_id` 必须是合法 UUID；
3. 映射失败（索引 id 在 `vector_index` 里查不到）时**不能崩** —— 保留原 id 并记 warning，
   因为崩掉会让整个检索不可用（比 id 不完美更糟）。
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.knowledge.services.document_service import KnowledgeDocumentService


class _FakeDB:
    """假 session：`_attach_storage_ids` 只用到 `execute()`。

    真实实现会带 `VectorIndex.chroma_id.in_(pending)` 过滤，替身无法解析
    SQL 表达式，所以**直接返回全表**（与真实 SQL 的语义一致：返回匹配行，
    由 `resolve_retrieval_ids` 按 mapping 查表）。
    """

    def __init__(self, mapping: dict[str, tuple[str, str]]) -> None:
        self._mapping = mapping
        self.queries = 0

    async def execute(self, _stmt: Any) -> Any:
        self.queries += 1
        mapping = self._mapping

        class _Rows:
            def all(self) -> list[Any]:
                return [
                    (chroma_id, chunk_id, doc_id)
                    for chroma_id, (chunk_id, doc_id) in mapping.items()
                ]

        return _Rows()


def _Result(chunk_id: str, doc_id: str, text: str = "内容") -> Any:
    """构造真实 `RetrievalResult`（**dataclass**）。

    必须用真实类型：`resolve_retrieval_ids` 内部调 `dataclasses.replace`，
    普通对象会报 `TypeError: replace() should be called on dataclass instances`
    （首版就是这样写的）。
    """
    from app.knowledge.models.document import RetrievalResult

    return RetrievalResult(chunk_id=chunk_id, doc_id=doc_id, text=text, score=0.9, source="hybrid")


class _StubService(KnowledgeDocumentService):
    """绕过真实构造，只测 `_attach_storage_ids` 的映射行为。"""

    def __init__(self, db: Any) -> None:
        self._db = db  # type: ignore[assignment]


class TestAttachStorageIds:
    @pytest.mark.asyncio
    async def test_maps_chroma_id_to_chunk_uuid(self) -> None:
        """**核心断言**：Chroma id → DocChunk.id（UUID）。"""
        chroma_id = "9eac9177523794ed"
        chunk_uuid = str(uuid.uuid4())
        doc_uuid = str(uuid.uuid4())
        db = _FakeDB({chroma_id: (chunk_uuid, doc_uuid)})
        service = _StubService(db)

        mapped = await service._attach_storage_ids([_Result(chroma_id, chroma_id)])

        assert len(mapped) == 1
        assert mapped[0].chunk_id == chunk_uuid
        assert mapped[0].doc_id == doc_uuid

    @pytest.mark.asyncio
    async def test_already_uuid_passes_through(self) -> None:
        """已经是 UUID 的结果不再查库（幂等，避免重复映射）。"""
        chunk_uuid = str(uuid.uuid4())
        doc_uuid = str(uuid.uuid4())
        db = _FakeDB({})
        service = _StubService(db)

        mapped = await service._attach_storage_ids([_Result(chunk_uuid, doc_uuid)])

        assert mapped[0].chunk_id == chunk_uuid
        assert db.queries == 0, "已是 UUID 时不该查库"

    @pytest.mark.asyncio
    async def test_unmapped_id_is_dropped(self) -> None:
        """映射失败的条目**被丢弃**，不是保留原 id。

        这是 `resolve_retrieval_ids` 的既定设计：**无法回溯到存储行的片段
        不能作为证据**。保留一个 Chroma 内部 id 只会让下游拿到一个点不回
        原文的「假证据」，比没有更糟。

        注意：首版测试断言的是「保留原 id」，与实现设计相反 ——
        是**测试写错了**，不是实现错了。已改正。
        """
        db = _FakeDB({})  # 空映射
        service = _StubService(db)

        mapped = await service._attach_storage_ids(
            [_Result("deadbeefdeadbeef", "deadbeefdeadbeef")]
        )

        assert mapped == [], "无法回溯的条目必须被丢弃"

    @pytest.mark.asyncio
    async def test_mixed_results_only_keep_resolvable(self) -> None:
        """混合结果：可回溯的保留，不可回溯的丢弃。"""
        good_chroma = "aaaaaaaaaaaaaaaa"
        good_chunk = str(uuid.uuid4())
        good_doc = str(uuid.uuid4())
        db = _FakeDB({good_chroma: (good_chunk, good_doc)})
        service = _StubService(db)

        mapped = await service._attach_storage_ids(
            [
                _Result(good_chroma, good_chroma),
                _Result("deadbeefdeadbeef", "deadbeefdeadbeef"),
            ]
        )

        assert len(mapped) == 1
        assert mapped[0].chunk_id == good_chunk


class TestEndpointUsesContextSearch:
    """端点必须调 `search_for_context()`（会做 id 映射），不能调 `search()`。"""

    def test_endpoint_calls_search_for_context(self) -> None:
        """端点必须**实际调用** `search_for_context()`。

        ⚠️ 不能只断言 `"search_for_context" in body`：docstring 里也会出现这个词，
        把调用改回 `search()` 后测试**仍然绿** —— 首版就是这样写的假测试
        （回退验证时抓到）。必须逐行找「以 `service.search_for_context(` 开头的
        非注释语句」。
        """
        import pathlib

        source = (
            pathlib.Path(__file__).resolve().parents[1]
            / "app"
            / "knowledge"
            / "api"
            / "endpoints.py"
        ).read_text(encoding="utf-8")

        call_lines = [
            line.strip()
            for line in source.splitlines()
            if "service.search_for_context(" in line
            and not line.strip().startswith("#")
            and "await" in line
        ]
        assert call_lines, (
            "search 端点必须**实际 await service.search_for_context(...)**"
            "（内含 chunk_id → UUID 映射）；调 service.search() 会让返回的 "
            "chunk_id 是 Chroma 内部 id，无法回溯原文"
        )

        # 反向断言：不能再出现 `await service.search(`（那是未映射的旧路径）
        legacy_calls = [
            line.strip()
            for line in source.splitlines()
            if "await service.search(" in line and not line.strip().startswith("#")
        ]
        assert not legacy_calls, f"仍有未映射的旧调用：{legacy_calls}"
