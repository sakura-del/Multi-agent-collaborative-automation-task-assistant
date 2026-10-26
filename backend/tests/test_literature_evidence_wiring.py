"""证据接线：检索到的文献必须进入证据池（2026-10-26 引用库遗留计划 §3.3）。

取证（计划 §1.1）：证据此前只从用户知识库构造，
与文献源（引用库 / arXiv）完全无关 —— 引用库任务检索到 6 条 artifacts，
`extract`/`write` 节点的 `evidence_pool` 仍然是 `[]`。

这里的测试锁三件事：
1. 检索节点的 artifacts 会变成 `kind="metadata"` 证据；
2. 知识库证据仍然是 `kind="excerpt"`（不回归）；
3. 两类证据**合并**而不是二选一（用户可能既有笔记又有引用库）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from app.orchestration.contracts import Evidence, EvidenceContext
from app.orchestration.workflows.literature_review import LiteratureReviewWorkflow


def _record_payload(
    *,
    source_id: str = "doi:10.18653/v1/2024.eacl-demo.16",
    title: str = "Ragas: Automated Evaluation of Retrieval Augmented Generation",
    year: int = 2024,
) -> dict[str, Any]:
    """检索节点 artifacts 的**真实形状**（`record.model_dump(mode="json")`）。"""
    return {
        "source_id": source_id,
        "title": title,
        "authors": ["Es, Shahul", "James, Jithin"],
        "abstract": "",
        "published_at": datetime(year, 1, 1, tzinfo=UTC).isoformat(),
        "source_url": f"https://doi.org/{source_id.removeprefix('doi:')}",
        "pdf_url": f"https://doi.org/{source_id.removeprefix('doi:')}",
        "categories": [],
    }


class _StubContextBuilder:
    """替代 `LiteratureContextBuilder`：只返回固定的知识库证据。"""

    def __init__(self, evidence: list[Evidence]) -> None:
        self._evidence = evidence
        self.calls: list[tuple[uuid.UUID, str]] = []

    async def build(self, user_id: uuid.UUID, query: str, *, top_k: int = 8) -> EvidenceContext:
        self.calls.append((user_id, query))
        text = "\n\n".join(
            f"[E{index}] {item.excerpt}" for index, item in enumerate(self._evidence, start=1)
        )
        return EvidenceContext(
            text=text,
            evidence=self._evidence,
            insufficient=len(self._evidence) < 2,
            query=query,
        )


class _FakeTask:
    def __init__(self, user_id: uuid.UUID, *, retrieval_mode: str = "hybrid_rerank") -> None:
        self.user_id = user_id
        self.config: dict[str, Any] = {
            "retrieval_mode": retrieval_mode,
            "keywords": ["RAG"],
            "topic": "检索增强生成评估",
        }


def _workflow(builder: _StubContextBuilder) -> LiteratureReviewWorkflow:
    return LiteratureReviewWorkflow(context_builder=builder)


class TestMetadataEvidenceWiring:
    async def test_search_artifacts_become_metadata_evidence(self) -> None:
        """核心修复：检索到的文献必须进入证据池。"""
        user_id = uuid.uuid4()
        builder = _StubContextBuilder([])  # 知识库为空
        workflow = _workflow(builder)

        prepared = await workflow.prepare_node(
            _FakeTask(user_id),
            {"id": "n1", "type": "extract", "title": "提取证据"},
            dependency_outputs={
                "n0": {
                    "artifacts": [
                        _record_payload(),
                        _record_payload(source_id="doi:10.1145/3626772.3657849", title="另一篇"),
                    ]
                }
            },
        )

        assert len(prepared["evidence"]) == 2
        kinds = [item["kind"] for item in prepared["evidence"]]
        assert kinds == ["metadata", "metadata"]

    async def test_metadata_evidence_carries_source_url(self) -> None:
        user_id = uuid.uuid4()
        workflow = _workflow(_StubContextBuilder([]))

        prepared = await workflow.prepare_node(
            _FakeTask(user_id),
            {"id": "n1", "type": "extract", "title": "提取证据"},
            dependency_outputs={"n0": {"artifacts": [_record_payload()]}},
        )

        item = prepared["evidence"][0]
        assert "10.18653/v1/2024.eacl-demo.16" in item["source_url"]
        assert "Ragas" in item["excerpt"]

    async def test_knowledge_evidence_still_marked_excerpt(self) -> None:
        """不回归：知识库证据仍是原文片段。"""
        user_id = uuid.uuid4()
        kb_evidence = [
            Evidence(
                doc_id=uuid.uuid4(),
                chunk_id=uuid.uuid4(),
                excerpt="知识库里的原文片段",
            )
        ]
        workflow = _workflow(_StubContextBuilder(kb_evidence))

        prepared = await workflow.prepare_node(
            _FakeTask(user_id),
            {"id": "n1", "type": "extract", "title": "提取证据"},
        )

        assert len(prepared["evidence"]) == 1
        assert prepared["evidence"][0]["kind"] == "excerpt"

    async def test_metadata_and_excerpt_are_merged(self) -> None:
        """两类证据合并，不是二选一。"""
        user_id = uuid.uuid4()
        kb_evidence = [Evidence(doc_id=uuid.uuid4(), chunk_id=uuid.uuid4(), excerpt="笔记原文")]
        workflow = _workflow(_StubContextBuilder(kb_evidence))

        prepared = await workflow.prepare_node(
            _FakeTask(user_id),
            {"id": "n1", "type": "extract", "title": "提取证据"},
            dependency_outputs={"n0": {"artifacts": [_record_payload()]}},
        )

        kinds = [item["kind"] for item in prepared["evidence"]]
        assert "metadata" in kinds
        assert "excerpt" in kinds
        assert len(prepared["evidence"]) == 2

    async def test_metadata_evidence_comes_first(self) -> None:
        """用户显式选择的文献优先，知识库检索作为补充。"""
        user_id = uuid.uuid4()
        kb_evidence = [Evidence(doc_id=uuid.uuid4(), chunk_id=uuid.uuid4(), excerpt="笔记原文")]
        workflow = _workflow(_StubContextBuilder(kb_evidence))

        prepared = await workflow.prepare_node(
            _FakeTask(user_id),
            {"id": "n1", "type": "extract", "title": "提取证据"},
            dependency_outputs={"n0": {"artifacts": [_record_payload()]}},
        )

        assert prepared["evidence"][0]["kind"] == "metadata"

    async def test_no_more_insufficient_when_only_metadata_exists(self) -> None:
        """有文献元数据就不该报「证据不足」。"""
        user_id = uuid.uuid4()
        workflow = _workflow(_StubContextBuilder([]))  # 知识库为空

        prepared = await workflow.prepare_node(
            _FakeTask(user_id),
            {"id": "n1", "type": "extract", "title": "提取证据"},
            dependency_outputs={
                "n0": {"artifacts": [_record_payload(), _record_payload(source_id="doi:10.1/b")]}
            },
        )

        assert prepared["insufficient_evidence"] is False
        assert "insufficient_evidence" not in prepared["warnings"]

    async def test_evidence_text_includes_metadata_lines(self) -> None:
        """证据上下文文本必须包含元数据行，否则写作 Agent 看不到。"""
        user_id = uuid.uuid4()
        workflow = _workflow(_StubContextBuilder([]))

        prepared = await workflow.prepare_node(
            _FakeTask(user_id),
            {"id": "n1", "type": "extract", "title": "提取证据"},
            dependency_outputs={"n0": {"artifacts": [_record_payload()]}},
        )

        assert "Ragas" in prepared["evidence_context"]

    async def test_retrieval_disabled_still_skips_knowledge_search(self) -> None:
        """`retrieval_mode=none` 时不查知识库，但文献元数据仍要进证据池。

        这两件事不同：`none` 关的是**知识库检索**（实验画像 no_rag），
        不是「用户明确选的文献」。
        """
        user_id = uuid.uuid4()
        builder = _StubContextBuilder([])
        workflow = _workflow(builder)

        prepared = await workflow.prepare_node(
            _FakeTask(user_id, retrieval_mode="none"),
            {"id": "n1", "type": "extract", "title": "提取证据"},
            dependency_outputs={"n0": {"artifacts": [_record_payload()]}},
        )

        assert builder.calls == []  # 没查知识库
        assert len(prepared["evidence"]) == 1  # 但文献元数据还在
        assert prepared["evidence"][0]["kind"] == "metadata"
        assert "retrieval_disabled" in prepared["warnings"]

    async def test_malformed_artifacts_do_not_crash(self) -> None:
        """artifacts 形状不对时不能炸掉整个节点（跳过并记 warning）。"""
        user_id = uuid.uuid4()
        workflow = _workflow(_StubContextBuilder([]))

        prepared = await workflow.prepare_node(
            _FakeTask(user_id),
            {"id": "n1", "type": "extract", "title": "提取证据"},
            dependency_outputs={"n0": {"artifacts": [{"nonsense": True}, "not-a-dict"]}},
        )

        assert prepared["evidence"] == []
        assert "invalid_search_artifact" in prepared["warnings"]
