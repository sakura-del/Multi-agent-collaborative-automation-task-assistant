"""用户已关联全文 → 带页码 locator 的 excerpt 证据（Issue #73 Phase B / Task B2）。

## 为什么需要这层

Phase A 的 OA 全文抓取与 Task B1 的知识库上传都会把正文挂进 `knowledge_doc`
（`meta_info.doi` 指回引用库条目），但写作节点此前只拿得到检索命中的
**metadata** 证据（「存在这样一篇文献」），拿不到原文片段。本服务按检索记录的
DOI（优先）/标题反查**当前用户** ready 的全文，输出带页码 locator 的 excerpt
证据（「作者指出……」）。

测试覆盖计划 Step 1 的四条硬要求 + 实现细节里承诺的裁剪/优先级：

1. 命中 ready 全文 → `Evidence(kind="excerpt", doc_id, chunk_id, excerpt,
   locator.page_start)`；
2. 无匹配全文 → 空列表（调用方继续用 metadata）；
3. 跨用户隔离：用户 B 的同 DOI 文档不返回；
4. `index_status != "ready"` 的文档不返回。

另覆盖：每篇 ≤2 条、result/method 章节优先、excerpt 截断到 2000 字符、
标题弱标识（<20 字符不生效）、重复记录不重复出证据。

`_prepare_node` 接线部分用注入的替身服务验证：全文证据排在元数据之前、
与知识库检索证据按 `(doc_id, chunk_id)` 去重、服务失败退化为「没有全文证据」、
`retrieval_mode="none"`（实验画像 no_rag）不读知识库。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import HumanActionRequiredError
from app.db.models.knowledge import DocChunk, KnowledgeDoc
from app.orchestration.contracts import Evidence, EvidenceContext, EvidenceLocator
from app.orchestration.services.user_fulltext_evidence import UserFulltextEvidenceService
from app.orchestration.workflows.literature_review import LiteratureReviewWorkflow
from app.tools.literature.models import LiteratureRecord

USER_A = uuid4()
USER_B = uuid4()

DOI = "10.1234/abc"
LONG_TITLE = "Retrieval Augmented Generation for Software Engineering: A Systematic Survey"


def _record(
    *,
    source_id: str = f"doi:{DOI}",
    title: str = LONG_TITLE,
    source_url: str | None = None,
) -> LiteratureRecord:
    """检索节点 artifacts 的**真实形状**（`record.model_dump(mode="json")` 的源）。"""
    url = source_url or f"https://doi.org/{source_id.removeprefix('doi:')}"
    return LiteratureRecord(
        source_id=source_id,
        title=title,
        authors=["Doe, Jane"],
        abstract="",
        published_at=datetime(2024, 1, 1, tzinfo=UTC),
        source_url=url,
        pdf_url=url,
        categories=[],
    )


def _chunk(
    content: str,
    *,
    chunk_index: int,
    page_start: int | None = None,
    section_title: str | None = None,
    section_role: str | None = None,
) -> dict[str, Any]:
    """一条 `doc_chunk` 播种数据：page_start + section_title 是 locator 的来源。"""
    position: dict[str, Any] = {"start_offset": 0, "end_offset": len(content)}
    if page_start is not None:
        position["page_start"] = page_start
        position["page_end"] = page_start
    chunk_meta: dict[str, Any] = {}
    if section_title is not None:
        chunk_meta["section_title"] = section_title
    if section_role is not None:
        chunk_meta["section_role"] = section_role
    return {
        "id": uuid4(),
        "chunk_index": chunk_index,
        "content": content,
        "position": position,
        "chunk_meta": chunk_meta or None,
    }


async def _seed_doc(
    db: AsyncSession,
    user_id: UUID = USER_A,
    *,
    doi: str | None = DOI,
    title: str = LONG_TITLE,
    index_status: str = "ready",
    chunks: list[dict[str, Any]],
) -> tuple[KnowledgeDoc, list[UUID]]:
    """播种一篇知识库文档 + 分块，返回文档与分块 id（按播种顺序）。"""
    doc = KnowledgeDoc(
        user_id=user_id,
        title=title,
        source="upload",
        doc_type="pdf",
        index_status=index_status,
        meta_info={"doi": doi} if doi else {},
    )
    db.add(doc)
    await db.flush()
    for index, payload in enumerate(chunks):
        db.add(
            DocChunk(
                id=payload["id"],
                doc_id=doc.id,
                chunk_index=int(payload.get("chunk_index", index)),
                content=str(payload["content"]),
                char_count=len(str(payload["content"])),
                position=payload.get("position"),
                chunk_meta=payload.get("chunk_meta"),
            )
        )
    await db.flush()
    return doc, [payload["id"] for payload in chunks]


# ===== 服务：按记录反查用户全文 =====


async def test_returns_excerpt_evidence_with_page_locator(db_session: AsyncSession) -> None:
    """DOI 命中 ready 全文 → 带页码/章节定位的 excerpt 证据（大小写归一）。"""
    doc, chunk_ids = await _seed_doc(
        db_session,
        chunks=[
            _chunk(
                "引言正文",
                chunk_index=0,
                page_start=1,
                section_title="Introduction",
                section_role="background",
            ),
            _chunk(
                "结果正文",
                chunk_index=1,
                page_start=3,
                section_title="Results",
                section_role="result",
            ),
        ],
    )

    items = await UserFulltextEvidenceService(db_session).evidence_for(
        USER_A, [_record(source_id="doi:10.1234/ABC")]
    )

    assert len(items) == 2
    # result/method 章节优先，所以结果章节排在引言之前
    first = items[0]
    assert first.kind == "excerpt"
    assert first.doc_id == doc.id
    assert first.chunk_id == chunk_ids[1]
    assert first.doc_title == LONG_TITLE
    assert first.excerpt == "结果正文"
    assert first.locator is not None
    assert first.locator.page_start == 3
    assert first.locator.section_title == "Results"


async def test_returns_empty_without_matching_fulltext(db_session: AsyncSession) -> None:
    """用户没有这篇全文 → 空列表（调用方继续用 metadata 证据）。"""
    items = await UserFulltextEvidenceService(db_session).evidence_for(USER_A, [_record()])

    assert items == []


async def test_is_owner_scoped(db_session: AsyncSession) -> None:
    """用户 B 的同 DOI 全文对用户 A 不可见（跨用户隔离）。"""
    await _seed_doc(db_session, USER_B, chunks=[_chunk("B 的正文", chunk_index=0, page_start=2)])

    items = await UserFulltextEvidenceService(db_session).evidence_for(USER_A, [_record()])

    assert items == []


@pytest.mark.parametrize("status", ["pending", "indexing", "failed"])
async def test_skips_docs_not_ready(db_session: AsyncSession, status: str) -> None:
    """索引未完成/失败的文档分块不能当证据（半成品正文可能缺失/错乱）。"""
    await _seed_doc(
        db_session,
        index_status=status,
        chunks=[_chunk("尚未就绪的正文", chunk_index=0, page_start=1)],
    )

    items = await UserFulltextEvidenceService(db_session).evidence_for(USER_A, [_record()])

    assert items == []


async def test_caps_chunks_and_prefers_result_and_method(db_session: AsyncSession) -> None:
    """每篇默认 ≤2 条，优先 result/method 章节，其余按 chunk_index 顺序。"""
    _, chunk_ids = await _seed_doc(
        db_session,
        chunks=[
            _chunk("结论正文", chunk_index=0, page_start=5, section_role="conclusion"),
            _chunk("方法正文", chunk_index=1, page_start=2, section_role="method"),
            _chunk("背景正文", chunk_index=2, page_start=1, section_role="background"),
            _chunk("结果正文", chunk_index=3, page_start=4, section_role="result"),
        ],
    )

    items = await UserFulltextEvidenceService(db_session).evidence_for(USER_A, [_record()])

    # 先 result/method 两章（各自按 chunk_index），结论/背景不进默认 2 条
    assert [item.chunk_id for item in items] == [chunk_ids[1], chunk_ids[3]]


async def test_max_chunks_per_paper_is_configurable(db_session: AsyncSession) -> None:
    """构造参数控制条数（调用方可以按 token 预算收紧）。"""
    await _seed_doc(
        db_session,
        chunks=[
            _chunk("结果正文", chunk_index=0, page_start=4, section_role="result"),
            _chunk("方法正文", chunk_index=1, page_start=2, section_role="method"),
        ],
    )

    service = UserFulltextEvidenceService(db_session, max_chunks_per_paper=1)
    items = await service.evidence_for(USER_A, [_record()])

    assert len(items) == 1


async def test_truncates_excerpt_to_evidence_limit(db_session: AsyncSession) -> None:
    """`Evidence.excerpt` 上限 2000 字符：长分块必须截断而不是校验失败。"""
    await _seed_doc(
        db_session,
        chunks=[_chunk("字" * 2500, chunk_index=0, page_start=6, section_title="Discussion")],
    )

    items = await UserFulltextEvidenceService(db_session).evidence_for(USER_A, [_record()])

    assert len(items) == 1
    assert len(items[0].excerpt) == 2000


async def test_locator_is_none_without_page_or_section_metadata(db_session: AsyncSession) -> None:
    """没有页码/章节元数据时 locator 为 None（不编造定位）。"""
    await _seed_doc(db_session, chunks=[_chunk("无定位的正文", chunk_index=0)])

    items = await UserFulltextEvidenceService(db_session).evidence_for(USER_A, [_record()])

    assert len(items) == 1
    assert items[0].locator is None


async def test_matches_by_normalized_title_when_no_doi(db_session: AsyncSession) -> None:
    """无 DOI 的记录（如 arXiv）按归一化标题匹配（归一化长度 ≥20 才生效）。"""
    await _seed_doc(
        db_session,
        doi=None,
        title=LONG_TITLE,
        chunks=[_chunk("标题命中的正文", chunk_index=0, page_start=2)],
    )

    items = await UserFulltextEvidenceService(db_session).evidence_for(
        USER_A,
        [_record(source_id="2401.12345v1", source_url="https://arxiv.org/abs/2401.12345")],
    )

    assert len(items) == 1
    assert items[0].excerpt == "标题命中的正文"


async def test_does_not_match_short_titles(db_session: AsyncSession) -> None:
    """归一化后 <20 字符的标题是弱标识，不参与关联（与 Task B1 同一取舍）。"""
    await _seed_doc(
        db_session,
        doi=None,
        title="A Survey",
        chunks=[_chunk("短标题正文", chunk_index=0, page_start=1)],
    )

    items = await UserFulltextEvidenceService(db_session).evidence_for(
        USER_A,
        [_record(source_id="src:short", title="A Survey", source_url="https://example.org/x")],
    )

    assert items == []


async def test_matches_doi_from_doi_org_source_url(db_session: AsyncSession) -> None:
    """source_id 不是 `doi:` 形态时，从 `https://doi.org/<DOI>` 的 source_url 取 DOI。"""
    await _seed_doc(db_session, chunks=[_chunk("URL 命中的正文", chunk_index=0, page_start=1)])

    items = await UserFulltextEvidenceService(db_session).evidence_for(
        USER_A,
        [_record(source_id="src:abc", source_url=f"https://doi.org/{DOI.upper()}")],
    )

    assert len(items) == 1


async def test_duplicate_records_do_not_duplicate_evidence(db_session: AsyncSession) -> None:
    """多个检索节点返回同一篇文献时，同一分块只出一次证据。"""
    await _seed_doc(db_session, chunks=[_chunk("唯一正文", chunk_index=0, page_start=1)])

    items = await UserFulltextEvidenceService(db_session).evidence_for(
        USER_A, [_record(), _record(source_id="doi:10.1234/abc")]
    )

    assert len(items) == 1


# ===== `_prepare_node` 接线：全文证据优先、去重、降级 =====


class _StubContextBuilder:
    """替代 `LiteratureContextBuilder`：只返回固定的知识库检索证据。"""

    def __init__(self, evidence: list[Evidence]) -> None:
        self._evidence = evidence
        self.calls: list[tuple[UUID, str]] = []

    async def build(self, user_id: UUID, query: str, *, top_k: int = 8) -> EvidenceContext:
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


class _StubFulltextService:
    """替代 `UserFulltextEvidenceService`：记录调用并返回固定证据 / 抛错。"""

    def __init__(self, evidence: list[Evidence], *, error: Exception | None = None) -> None:
        self._evidence = evidence
        self._error = error
        self.calls: list[tuple[UUID, list[LiteratureRecord]]] = []

    async def evidence_for(self, user_id: UUID, records: list[LiteratureRecord]) -> list[Evidence]:
        self.calls.append((user_id, records))
        if self._error is not None:
            raise self._error
        return list(self._evidence)


class _FakeTask:
    def __init__(self, user_id: UUID, *, retrieval_mode: str = "hybrid_rerank") -> None:
        self.user_id = user_id
        self.config: dict[str, Any] = {
            "retrieval_mode": retrieval_mode,
            "keywords": ["RAG"],
            "topic": "检索增强生成评估",
        }


def _record_payload(
    *,
    source_id: str = f"doi:{DOI}",
    title: str = LONG_TITLE,
) -> dict[str, Any]:
    """检索节点 artifacts 的**真实形状**（生产链路里落库的就是它）。"""
    record = _record(source_id=source_id, title=title)
    return record.model_dump(mode="json")


def _search_output(*records: dict[str, Any]) -> dict[str, Any]:
    """**生产形状**的检索节点输出（artifacts 在 `execution_result` 里面）。"""
    return {
        "node_id": "n0",
        "result": "- 文献列表",
        "status": "completed",
        "execution_result": {
            "node_id": "n0",
            "content": "- 文献列表",
            "evidence": [],
            "artifacts": list(records),
            "warnings": [],
            "duration_ms": 0,
            "node_type": "search",
        },
    }


def _workflow(
    builder: _StubContextBuilder,
    *,
    fulltext: _StubFulltextService | None = None,
) -> LiteratureReviewWorkflow:
    # 用注入对象保持测试离线：不连知识库、不调 LLM
    kwargs: dict[str, Any] = {"context_builder": builder}
    if fulltext is not None:
        kwargs["user_fulltext_evidence"] = fulltext
    return LiteratureReviewWorkflow(**kwargs)


async def test_prepare_node_puts_fulltext_excerpt_before_metadata() -> None:
    """全文证据（带页码）排在元数据之前：有原文时它比书目信息强。"""
    user_id = uuid4()
    doc_id, chunk_id = uuid4(), uuid4()
    fulltext = _StubFulltextService(
        [
            Evidence(
                doc_id=doc_id,
                chunk_id=chunk_id,
                doc_title=LONG_TITLE,
                excerpt="作者指出：RAG 评估仍缺可复现基准。",
                locator=EvidenceLocator(page_start=4, page_end=4, section_title="Results"),
            )
        ]
    )
    workflow = _workflow(_StubContextBuilder([]), fulltext=fulltext)

    prepared = await workflow.prepare_node(
        _FakeTask(user_id),
        {"id": "n1", "type": "extract", "title": "提取证据"},
        dependency_outputs={"n0": _search_output(_record_payload())},
    )

    kinds = [item["kind"] for item in prepared["evidence"]]
    assert kinds == ["excerpt", "metadata"]
    assert prepared["evidence"][0]["doc_id"] == str(doc_id)
    assert prepared["evidence"][0]["chunk_id"] == str(chunk_id)
    assert prepared["evidence"][0]["locator"]["page_start"] == 4
    # 证据文本里 excerpt 行带 doc/chunk，写作 Agent 能回溯
    assert f"doc={doc_id}" in prepared["evidence_context"]
    # 服务收到的是已通过契约校验的文献记录
    assert len(fulltext.calls) == 1
    assert fulltext.calls[0][0] == user_id
    assert fulltext.calls[0][1][0].source_id == f"doi:{DOI}"


async def test_prepare_node_skips_fulltext_lookup_without_search_records() -> None:
    """没有检索 artifacts 就没有可升级的文献 → 不查知识库。"""
    fulltext = _StubFulltextService([])
    workflow = _workflow(_StubContextBuilder([]), fulltext=fulltext)

    prepared = await workflow.prepare_node(
        _FakeTask(uuid4()), {"id": "n1", "type": "extract", "title": "提取证据"}
    )

    assert fulltext.calls == []
    assert prepared["evidence"] == []


async def test_prepare_node_dedupes_fulltext_against_retrieval_evidence() -> None:
    """全文证据与知识库检索命中的同一分块只出现一次（按 doc_id + chunk_id 去重）。"""
    user_id = uuid4()
    doc_id, chunk_id = uuid4(), uuid4()
    chunk = Evidence(
        doc_id=doc_id,
        chunk_id=chunk_id,
        doc_title=LONG_TITLE,
        excerpt="同一段原文",
        locator=EvidenceLocator(page_start=2),
    )
    fulltext = _StubFulltextService([chunk])
    workflow = _workflow(_StubContextBuilder([chunk]), fulltext=fulltext)

    prepared = await workflow.prepare_node(
        _FakeTask(user_id),
        {"id": "n1", "type": "extract", "title": "提取证据"},
        dependency_outputs={"n0": _search_output(_record_payload())},
    )

    excerpt_items = [item for item in prepared["evidence"] if item["kind"] == "excerpt"]
    assert len(excerpt_items) == 1
    assert len(prepared["evidence"]) == 2  # 1 条 excerpt + 1 条 metadata


async def test_prepare_node_degrades_when_fulltext_lookup_fails() -> None:
    """全文查询失败只是「没有全文证据」，不能炸掉节点（元数据证据照常保留）。"""
    user_id = uuid4()
    fulltext = _StubFulltextService([], error=RuntimeError("db unavailable"))
    workflow = _workflow(_StubContextBuilder([]), fulltext=fulltext)

    prepared = await workflow.prepare_node(
        _FakeTask(user_id),
        {"id": "n1", "type": "extract", "title": "提取证据"},
        dependency_outputs={"n0": _search_output(_record_payload())},
    )

    assert [item["kind"] for item in prepared["evidence"]] == ["metadata"]


async def test_prepare_node_skips_fulltext_when_retrieval_disabled() -> None:
    """`retrieval_mode="none"`（实验画像 no_rag）不读知识库，但元数据证据仍在。"""
    fulltext = _StubFulltextService(
        [Evidence(doc_id=uuid4(), chunk_id=uuid4(), excerpt="不应出现")]
    )
    workflow = _workflow(_StubContextBuilder([]), fulltext=fulltext)

    prepared = await workflow.prepare_node(
        _FakeTask(uuid4(), retrieval_mode="none"),
        {"id": "n1", "type": "extract", "title": "提取证据"},
        dependency_outputs={"n0": _search_output(_record_payload())},
    )

    assert fulltext.calls == []
    assert [item["kind"] for item in prepared["evidence"]] == ["metadata"]
    assert "retrieval_disabled" in prepared["warnings"]


async def test_prepare_node_requires_user_id_before_fulltext_lookup() -> None:
    """任务没有 UUID user_id 时，既有证据上下文构建器先拒绝，不进入全文查询。"""
    fulltext = _StubFulltextService(
        [Evidence(doc_id=uuid4(), chunk_id=uuid4(), excerpt="不应出现")]
    )
    workflow = _workflow(_StubContextBuilder([]), fulltext=fulltext)

    class _NoUserTask:
        def __init__(self) -> None:
            self.config: dict[str, Any] = {"retrieval_mode": "hybrid_rerank"}

    with pytest.raises(HumanActionRequiredError):
        await workflow.prepare_node(
            _NoUserTask(),
            {"id": "n1", "type": "extract", "title": "提取证据"},
            dependency_outputs={"n0": _search_output(_record_payload())},
        )

    assert fulltext.calls == []
