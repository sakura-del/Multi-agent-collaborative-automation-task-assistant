"""owner-scoped 证据上下文测试（Plan 3 Task 2）。

覆盖：
- 证据映射：稳定标记 `[E1]`、doc_id/chunk_id、excerpt ≤2000 字符、可选 source_url；
- 两个用户相同 query 时只返回调用者自己的 chunk；
- 空片段跳过、非法 UUID / 非法 URL → `ValidationException`；
- `KnowledgeDocumentService.search_for_context()` 强制 owner 过滤与 namespace 隔离。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.core.exceptions import ValidationException
from app.knowledge.models.document import RetrievalResult
from app.knowledge.services.context_builder import LiteratureContextBuilder
from app.knowledge.services.document_service import KnowledgeDocumentService
from app.knowledge.services.document_storage import DocumentStorage

USER_A = UUID("11111111-1111-1111-1111-111111111111")
USER_B = UUID("22222222-2222-2222-2222-222222222222")


def _result(
    *,
    doc_id: str | None = None,
    chunk_id: str | None = None,
    text: str = "检索增强生成可以提升证据可追溯性。",
    metadata: dict[str, Any] | None = None,
) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk_id or str(uuid4()),
        doc_id=doc_id or str(uuid4()),
        text=text,
        score=0.9,
        source="hybrid",
        metadata=metadata if metadata is not None else {},
    )


class _FakeKnowledge:
    """按 user_id 返回不同 chunk，并记录调用参数。"""

    def __init__(self, per_user: dict[UUID, list[RetrievalResult]]) -> None:
        self._per_user = per_user
        self.calls: list[dict[str, Any]] = []

    async def search_for_context(
        self, user_id: UUID, query: str, *, top_k: int = 8
    ) -> list[RetrievalResult]:
        self.calls.append({"user_id": user_id, "query": query, "top_k": top_k})
        return list(self._per_user.get(user_id, []))


class _RecordingKB:
    """记录 KnowledgeBase.query 的关键字参数（验证 owner 过滤）。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def query(self, query: str, **kwargs: Any) -> list[RetrievalResult]:
        self.calls.append({"query": query, **kwargs})
        return [_result()]


class TestEvidenceMapping:
    async def test_maps_stable_markers_and_ids(self) -> None:
        doc_id, chunk_id = uuid4(), uuid4()
        knowledge = _FakeKnowledge(
            {
                USER_A: [
                    _result(
                        doc_id=str(doc_id),
                        chunk_id=str(chunk_id),
                        text="RAG 通过检索外部知识提升事实一致性。",
                        metadata={"source_url": "https://arxiv.org/abs/2401.12345v1"},
                    ),
                    _result(text="多智能体协作需要共享证据上下文。"),
                ]
            }
        )
        context = await LiteratureContextBuilder(knowledge).build(USER_A, "检索增强生成", top_k=4)

        assert knowledge.calls == [{"user_id": USER_A, "query": "检索增强生成", "top_k": 4}]
        assert context.query == "检索增强生成"
        assert context.insufficient is False
        assert len(context.evidence) == 2

        first = context.evidence[0]
        assert first.doc_id == doc_id
        assert first.chunk_id == chunk_id
        assert str(first.source_url) == "https://arxiv.org/abs/2401.12345v1"
        assert first.excerpt == "RAG 通过检索外部知识提升事实一致性。"
        assert context.evidence[1].source_url is None

        assert context.text.count("[E1]") == 1
        assert context.text.count("[E2]") == 1
        assert f"[E1] doc={doc_id} chunk={chunk_id}" in context.text

    async def test_excerpt_is_capped_at_2000_characters(self) -> None:
        knowledge = _FakeKnowledge({USER_A: [_result(text="x" * 5000)]})
        context = await LiteratureContextBuilder(knowledge).build(USER_A, "长文本")
        assert len(context.evidence[0].excerpt) == 2000

    async def test_fewer_than_two_items_marks_insufficient(self) -> None:
        knowledge = _FakeKnowledge({USER_A: [_result()]})
        builder = LiteratureContextBuilder(knowledge)

        one = await builder.build(USER_A, "只有一个结果")
        assert one.insufficient is True
        assert len(one.evidence) == 1

        knowledge._per_user[USER_A] = []
        empty = await builder.build(USER_A, "没有结果")
        assert empty.insufficient is True
        assert empty.evidence == []
        assert empty.text == ""

    async def test_blank_chunks_are_skipped(self) -> None:
        knowledge = _FakeKnowledge({USER_A: [_result(text="   "), _result(text="有效证据")]})
        context = await LiteratureContextBuilder(knowledge).build(USER_A, "空白片段")
        assert len(context.evidence) == 1
        assert context.evidence[0].excerpt == "有效证据"

    async def test_two_users_receive_their_own_chunks(self) -> None:
        chunk_a, chunk_b = uuid4(), uuid4()
        knowledge = _FakeKnowledge(
            {
                USER_A: [_result(chunk_id=str(chunk_a), text="A 的证据")],
                USER_B: [_result(chunk_id=str(chunk_b), text="B 的证据")],
            }
        )
        builder = LiteratureContextBuilder(knowledge)

        context_a = await builder.build(USER_A, "同一个 query")
        context_b = await builder.build(USER_B, "同一个 query")

        assert [item.chunk_id for item in context_a.evidence] == [chunk_a]
        assert [item.chunk_id for item in context_b.evidence] == [chunk_b]
        assert str(chunk_b) not in context_a.text
        assert str(chunk_a) not in context_b.text

    async def test_invalid_identifiers_raise_validation_error(self) -> None:
        knowledge = _FakeKnowledge({USER_A: [_result(doc_id="not-a-uuid", chunk_id=str(uuid4()))]})
        with pytest.raises(ValidationException):
            await LiteratureContextBuilder(knowledge).build(USER_A, "非法标识")

    async def test_invalid_source_url_raises_validation_error(self) -> None:
        knowledge = _FakeKnowledge({USER_A: [_result(metadata={"source_url": "not-a-url"})]})
        with pytest.raises(ValidationException):
            await LiteratureContextBuilder(knowledge).build(USER_A, "非法 URL")


class TestSearchForContext:
    async def test_forces_owner_filter_and_namespace(self, tmp_path: Path) -> None:
        kb = _RecordingKB()
        service = KnowledgeDocumentService(
            None,  # type: ignore[arg-type] - search 路径不使用数据库会话
            storage=DocumentStorage(tmp_path / "uploads", max_file_bytes=1024),
            index_manager=None,  # type: ignore[arg-type] - 仅检索路径
            kb=kb,  # type: ignore[arg-type]
        )
        results = await service.search_for_context(USER_A, "证据检索", top_k=3)

        assert len(results) == 1
        assert kb.calls[0]["query"] == "证据检索"
        assert kb.calls[0]["top_k"] == 3
        assert kb.calls[0]["where"] == {"user_id": str(USER_A)}
        assert kb.calls[0]["namespace"] == str(USER_A)
