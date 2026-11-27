"""知识库上传 ↔ 引用库条目自动关联（Issue #73 Phase B / Task B1 Step 1）。

覆盖计划要求的行为：

1. 显式 ``ref_id="doi:10.1234/abc"`` → 文档 ``meta_info`` 写回
   ``ref_id`` 且 ``doi == "10.1234/abc"``；
2. 无 ``ref_id`` 但归一化 DOI 匹配 → 自动关联；
3. 标题匹配只在归一化长度 ≥20 **且候选唯一**时生效（短标题、歧义都不关联）；
4. 跨用户隔离：用户 B 的同 DOI 条目不会被用户 A 的上传关联。

另覆盖上传端点透传可选 ``ref_id`` 表单字段 —— 端点改动没有测试等于没验证过。

用 ``db_session``（函数级内存 SQLite）+ 存储/索引替身：本文件只关心关联与
``meta_info`` 落库，不加载真实嵌入模型、不出网。
"""

from __future__ import annotations

import io
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

from fastapi import FastAPI, UploadFile
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.knowledge import KnowledgeDoc
from app.knowledge.api import endpoints as knowledge_endpoints
from app.knowledge.models.document import ChunkStrategy
from app.knowledge.services.document_service import KnowledgeDocumentService
from app.knowledge.services.reference_library_service import ReferenceLibraryService
from app.knowledge.services.reference_linker import (
    ReferenceLinker,
    doi_from_ref_id,
    normalize_doi,
    normalize_title,
)
from app.tools.csl.models import CslItem

USER_A = uuid4()
USER_B = uuid4()

#: 归一化后 66 字符，远高于 20 字门槛。
LONG_TITLE = "Retrieval Augmented Generation for Software Engineering: A Systematic Survey"
#: 同一题名的写法变体（大小写 / 全角破折号 / 空白不同）。
LONG_TITLE_VARIANT = "Retrieval augmented generation for software engineering — a systematic survey"
#: 归一化后 7 字符：区分度不足，标题匹配必须拒绝。
SHORT_TITLE = "A Survey"


def _csl_item(
    *,
    ref_id: str,
    title: str,
    doi: str | None = None,
    url: str | None = "https://example.org/paper",
) -> CslItem:
    """构造一条**能通过引用库准入**的条目（缺年份或缺 URL/DOI 会被拒绝）。"""
    return CslItem(
        id=ref_id,
        type="article-journal",
        title=title,
        doi=doi,
        url=url,
        issued_year=2024,
    )


async def _seed_library(db: AsyncSession, user_id: UUID, *items: CslItem) -> None:
    """走真实入库路径播种引用库条目（与生产写入路径同形）。"""
    result = await ReferenceLibraryService(db).save(user_id, list(items))
    assert result.rejected == [], f"测试数据必须能入库：{result.rejected}"


class _FakeStorage:
    """模拟受控存储：save 返回对象键，localize 提供本地临时文件。"""

    def __init__(self, tmp_file: Path) -> None:
        self._tmp_file = tmp_file

    async def save(self, user_id: Any, upload: UploadFile) -> Any:
        return SimpleNamespace(
            path=Path("uploads/abc/xyz.pdf"),
            doc_uuid=uuid4(),
            original_name="paper.pdf",
            extension=".pdf",
            size=12,
        )

    @asynccontextmanager
    async def localize(self, path: str) -> Any:
        yield self._tmp_file


class _FakeManager:
    """解析/分块/写索引替身：固定产出一个分块，不加载模型。"""

    collection_name = "knowledge_base"
    embedding_dimension = 8

    def prepare_document(self, path: str, **kwargs: Any) -> tuple[dict[str, Any], list[Any]]:
        chunk = SimpleNamespace(chunk_id="c1", text="正文", metadata={"chunk_index": 0})
        return {}, [chunk]

    def index_chunks(self, chunks: list[Any]) -> None:
        return None

    def remove_document(self, doc_id: str) -> int:
        return 0


class _FakeKb:
    def invalidate_cache(self, key: str) -> None:
        return None


def _service(db: AsyncSession, tmp_path: Path) -> KnowledgeDocumentService:
    tmp_file = tmp_path / "staged.pdf"
    tmp_file.write_bytes(b"%PDF-1.4 body")
    return KnowledgeDocumentService(
        db,
        storage=_FakeStorage(tmp_file),  # type: ignore[arg-type]
        index_manager=_FakeManager(),  # type: ignore[arg-type]
        kb=_FakeKb(),  # type: ignore[arg-type]
    )


def _upload() -> UploadFile:
    return UploadFile(filename="paper.pdf", file=io.BytesIO(b"%PDF-1.4 body"))


async def _stored_doc(db: AsyncSession, doc_id: UUID) -> KnowledgeDoc:
    """从数据库重读：断言的是**落库后**的 meta_info，不是内存对象。"""
    db.expire_all()
    return (await db.execute(select(KnowledgeDoc).where(KnowledgeDoc.id == doc_id))).scalar_one()


# ===== 归一化函数 =====


def test_normalize_doi_strips_prefix_and_lowercases() -> None:
    assert normalize_doi("doi:10.1234/ABC") == "10.1234/abc"
    assert normalize_doi(" 10.1234/Abc ") == "10.1234/abc"
    assert normalize_doi("https://doi.org/10.1234/ABC") == "10.1234/abc"
    assert normalize_doi(None) is None
    assert normalize_doi("   ") is None


def test_normalize_title_removes_punctuation_and_requires_20_chars() -> None:
    assert normalize_title(LONG_TITLE_VARIANT) == normalize_title(LONG_TITLE)
    # 短标题归一化后 <20 字 → None（弱标识不可单独用于关联）
    assert normalize_title(SHORT_TITLE) is None
    assert normalize_title(None) is None


def test_doi_from_ref_id_only_accepts_doi_form() -> None:
    assert doi_from_ref_id("doi:10.1234/ABC") == "10.1234/abc"
    # `src:` / `title:` 形式的 ref_id 不是 DOI，不能被写进 meta_info["doi"]
    assert doi_from_ref_id("src:abc") is None
    assert doi_from_ref_id("title:deadbeef") is None
    assert doi_from_ref_id(None) is None


# ===== 显式 ref_id =====


async def test_ingest_with_explicit_ref_id_writes_meta_link(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """显式 ref_id 命中引用库条目 → ref_id 与 doi 写进 meta_info 并落库。"""
    await _seed_library(
        db_session,
        USER_A,
        _csl_item(ref_id="doi:10.1234/abc", title="A linked paper", doi="10.1234/abc"),
    )
    service = _service(db_session, tmp_path)

    doc = await service.ingest(
        USER_A,
        _upload(),
        title="上传的论文",
        strategy=ChunkStrategy.RECURSIVE,
        ref_id="doi:10.1234/abc",
    )

    stored = await _stored_doc(db_session, doc.id)
    assert stored.meta_info["ref_id"] == "doi:10.1234/abc"
    assert stored.meta_info["doi"] == "10.1234/abc"
    # 原有元数据不能丢
    assert stored.meta_info["original_name"] == "paper.pdf"


async def test_ingest_with_unknown_ref_id_keeps_doc_unlinked(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """引用库里找不到该 ref_id → 上传照常成功，只是不写关联字段。"""
    service = _service(db_session, tmp_path)

    doc = await service.ingest(
        USER_A,
        _upload(),
        title="上传的论文",
        strategy=ChunkStrategy.RECURSIVE,
        ref_id="doi:10.9999/unknown",
    )

    stored = await _stored_doc(db_session, doc.id)
    assert "ref_id" not in stored.meta_info
    assert "doi" not in stored.meta_info


# ===== DOI 自动匹配（无 ref_id） =====


async def test_linker_matches_normalized_doi_without_ref_id(db_session: AsyncSession) -> None:
    """无 ref_id，但 DOI 归一化后相同（大小写 / URL 前缀）→ 自动关联。"""
    await _seed_library(
        db_session,
        USER_A,
        _csl_item(ref_id="doi:10.1234/abc", title="A linked paper", doi="10.1234/abc"),
    )

    matched = await ReferenceLinker(db_session).match(
        USER_A, ref_id=None, doi="https://doi.org/10.1234/ABC", title=None
    )

    assert matched is not None
    assert matched.ref_id == "doi:10.1234/abc"


async def test_linker_doi_match_is_owner_scoped(db_session: AsyncSession) -> None:
    """用户 B 的同 DOI 条目对用户 A 不可见（跨用户隔离的第一道断言）。"""
    await _seed_library(
        db_session,
        USER_B,
        _csl_item(ref_id="doi:10.1234/abc", title="B 的论文", doi="10.1234/abc"),
    )

    matched = await ReferenceLinker(db_session).match(
        USER_A, ref_id=None, doi="10.1234/abc", title=None
    )

    assert matched is None


async def test_ingest_links_by_doi_when_ref_id_form_differs(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """上传的 ref_id 与库中条目 ref_id 不同，但归一化 DOI 相同 → 按 DOI 自动关联。"""
    await _seed_library(
        db_session,
        USER_A,
        _csl_item(ref_id="src:doi-match-001", title="另一条 DOI 论文", doi="10.1234/doi-match"),
    )
    service = _service(db_session, tmp_path)

    doc = await service.ingest(
        USER_A,
        _upload(),
        title="上传的论文",
        strategy=ChunkStrategy.RECURSIVE,
        ref_id="doi:10.1234/DOI-MATCH",
    )

    stored = await _stored_doc(db_session, doc.id)
    assert stored.meta_info["ref_id"] == "src:doi-match-001"
    assert stored.meta_info["doi"] == "10.1234/doi-match"


# ===== 标题匹配 =====


async def test_ingest_links_by_unique_long_title(db_session: AsyncSession, tmp_path: Path) -> None:
    """标题写法不同但归一化相同、且归一化长度 ≥20 → 关联。"""
    await _seed_library(
        db_session,
        USER_A,
        _csl_item(ref_id="src:title-match-001", title=LONG_TITLE),
    )
    service = _service(db_session, tmp_path)

    doc = await service.ingest(
        USER_A,
        _upload(),
        title=LONG_TITLE_VARIANT,
        strategy=ChunkStrategy.RECURSIVE,
    )

    stored = await _stored_doc(db_session, doc.id)
    assert stored.meta_info["ref_id"] == "src:title-match-001"
    # 库中该条目没有 DOI（url 准入）→ 计划约定写 None，不编造 DOI
    assert stored.meta_info["doi"] is None


async def test_ingest_skips_title_match_for_short_titles(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """归一化后 <20 字符的标题区分度不足，即使完全相同也不关联。"""
    await _seed_library(
        db_session,
        USER_A,
        _csl_item(ref_id="src:short-001", title=SHORT_TITLE),
    )
    service = _service(db_session, tmp_path)

    doc = await service.ingest(
        USER_A,
        _upload(),
        title=SHORT_TITLE,
        strategy=ChunkStrategy.RECURSIVE,
    )

    stored = await _stored_doc(db_session, doc.id)
    assert "ref_id" not in stored.meta_info


async def test_ingest_skips_title_match_when_ambiguous(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """库里两条同名文献（会议版 / 期刊版）→ 候选不唯一，宁可不关联。"""
    await _seed_library(
        db_session,
        USER_A,
        _csl_item(ref_id="src:dup-a", title=LONG_TITLE),
        _csl_item(ref_id="src:dup-b", title=LONG_TITLE_VARIANT),
    )
    service = _service(db_session, tmp_path)

    doc = await service.ingest(
        USER_A,
        _upload(),
        title=LONG_TITLE,
        strategy=ChunkStrategy.RECURSIVE,
    )

    stored = await _stored_doc(db_session, doc.id)
    assert "ref_id" not in stored.meta_info


# ===== 跨用户隔离（端到端） =====


async def test_ingest_does_not_link_other_users_item(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """用户 A 上传时带用户 B 条目的 ref_id：不得关联，也不得改动 B 的数据。"""
    await _seed_library(
        db_session,
        USER_B,
        _csl_item(ref_id="doi:10.1234/cross", title="B 的论文", doi="10.1234/cross"),
    )
    service = _service(db_session, tmp_path)

    doc = await service.ingest(
        USER_A,
        _upload(),
        title="A 的上传",
        strategy=ChunkStrategy.RECURSIVE,
        ref_id="doi:10.1234/cross",
    )

    stored = await _stored_doc(db_session, doc.id)
    assert "ref_id" not in stored.meta_info
    assert "doi" not in stored.meta_info


# ===== 上传端点透传 ref_id =====


def _upload_endpoint_client() -> tuple[TestClient, dict[str, Any]]:
    """最小 FastAPI 应用 + 依赖覆盖：只验证端点把 ref_id 透传给 ingest()。"""
    captured: dict[str, Any] = {}

    class _StubService:
        async def ingest(
            self,
            user_id: UUID,
            upload: UploadFile,
            *,
            title: str,
            strategy: ChunkStrategy,
            ref_id: str | None = None,
        ) -> Any:
            captured.update(user_id=user_id, title=title, strategy=strategy, ref_id=ref_id)
            return SimpleNamespace(
                id=uuid4(),
                title=title,
                doc_type="pdf",
                index_status="ready",
                chunk_count=1,
                created_at=datetime.now(UTC),
                source="upload",
                meta_info={"original_name": "paper.pdf"},
            )

    app = FastAPI()
    app.include_router(knowledge_endpoints.router, prefix="/api/v1")
    app.dependency_overrides[knowledge_endpoints.get_document_service] = lambda: _StubService()
    app.dependency_overrides[knowledge_endpoints.get_current_active_user] = lambda: SimpleNamespace(
        id=uuid4()
    )
    return TestClient(app), captured


def test_upload_endpoint_forwards_ref_id_form_field() -> None:
    client, captured = _upload_endpoint_client()

    with client:
        response = client.post(
            "/api/v1/knowledge/documents",
            files={"file": ("paper.pdf", b"%PDF-1.4 body", "application/pdf")},
            data={"title": "上传标题", "ref_id": "doi:10.1234/abc"},
        )

    assert response.status_code == 201
    assert captured["ref_id"] == "doi:10.1234/abc"
    assert captured["title"] == "上传标题"


def test_upload_endpoint_allows_missing_ref_id() -> None:
    """ref_id 是可选字段：不带它时按 None 传给 ingest（旧客户端行为不变）。"""
    client, captured = _upload_endpoint_client()

    with client:
        response = client.post(
            "/api/v1/knowledge/documents",
            files={"file": ("paper.pdf", b"%PDF-1.4 body", "application/pdf")},
            data={"title": "上传标题"},
        )

    assert response.status_code == 201
    assert captured["ref_id"] is None
