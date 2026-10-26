"""元数据证据构造（2026-10-26 引用库遗留计划 §3.1/§3.2）。

背景：引用库任务跑到 `completed`、检索节点 6/6 artifacts 来自引用库，
但 `evidence_pool` 是空的 —— 因为 `Evidence` 只从用户**知识库**构造，
与文献源无关。`extract`/`write` 节点因此在没有证据的情况下写作。

修法不是编造摘要（`abstract=""` 是故意的），而是把检索节点已有的
**真实书目元数据**（题名/作者/年份/URL）转成 `kind="metadata"` 证据，
并在契约里显式区分「元数据证据」与「原文证据」。
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.orchestration.contracts import Evidence
from app.orchestration.services.literature_evidence import (
    metadata_evidence,
    metadata_excerpt,
)
from app.tools.literature.models import LiteratureRecord


def record(
    *,
    title: str = "Ragas: Automated Evaluation of Retrieval Augmented Generation",
    authors: list[str] | None = None,
    year: int = 2024,
    source_url: str = "https://doi.org/10.18653/v1/2024.eacl-demo.16",
    source_id: str = "doi:10.18653/v1/2024.eacl-demo.16",
    abstract: str = "",
) -> LiteratureRecord:
    return LiteratureRecord(
        source_id=source_id,
        title=title,
        authors=authors if authors is not None else ["Es, Shahul", "James, Jithin"],
        abstract=abstract,
        published_at=datetime(year, 1, 1, tzinfo=UTC),
        source_url=source_url,
        pdf_url=source_url,
        categories=[],
    )


class TestEvidenceKind:
    """契约必须能区分两类证据，否则下游会混用。"""

    def test_default_kind_is_excerpt(self) -> None:
        """既有知识库证据不带 kind 时仍是原文证据（向后兼容）。"""
        item = Evidence(excerpt="知识库里的原文片段")
        assert item.kind == "excerpt"

    def test_metadata_kind_is_explicit(self) -> None:
        item = Evidence(excerpt="《某文献》— 张三，2024", kind="metadata")
        assert item.kind == "metadata"

    def test_kind_rejects_unknown_value(self) -> None:
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            Evidence(excerpt="x", kind="summary")  # type: ignore[arg-type]


class TestMetadataExcerpt:
    """excerpt 文本必须真实、可核验、不编造。"""

    def test_contains_title_authors_year_and_url(self) -> None:
        text = metadata_excerpt(record())
        assert "Ragas: Automated Evaluation" in text
        assert "Es, Shahul" in text
        assert "2024" in text
        assert "https://doi.org/10.18653/v1/2024.eacl-demo.16" in text

    def test_truncates_long_author_lists(self) -> None:
        text = metadata_excerpt(record(authors=["A, 1", "B, 2", "C, 3", "D, 4", "E, 5"]))
        assert "等" in text
        # 只列前三位，避免把整份作者名单塞进证据
        assert "D, 4" not in text

    def test_handles_missing_authors(self) -> None:
        """没有作者就写「佚名」，不能编一个。"""
        text = metadata_excerpt(record(authors=[]))
        assert "佚名" in text

    def test_does_not_invent_abstract(self) -> None:
        """证据里不能出现摘要内容 —— 我们没有摘要。"""
        text = metadata_excerpt(record(abstract=""))
        assert "摘要" not in text
        assert "abstract" not in text.lower()

    def test_never_claims_original_text(self) -> None:
        """不能出现「原文」「引述」这类会让人以为是原文片段的措辞。"""
        text = metadata_excerpt(record())
        for misleading in ("原文", "引述", "摘录", "作者指出"):
            assert misleading not in text


class TestMetadataEvidence:
    """转成 Evidence 后必须能被下游安全使用。"""

    def test_kind_is_metadata(self) -> None:
        item = metadata_evidence(record())
        assert item.kind == "metadata"

    def test_source_url_is_preserved(self) -> None:
        item = metadata_evidence(record())
        assert item.source_url is not None
        assert "10.18653/v1/2024.eacl-demo.16" in str(item.source_url)

    def test_excerpt_is_non_empty(self) -> None:
        """Evidence.excerpt 有 min_length=1，空字符串会直接炸。"""
        item = metadata_evidence(record())
        assert len(item.excerpt) >= 1

    def test_doc_and_chunk_ids_are_absent(self) -> None:
        """元数据证据不绑定知识库分块，这两个字段必须是 None。"""
        item = metadata_evidence(record())
        assert item.doc_id is None
        assert item.chunk_id is None

    def test_excerpt_is_capped(self) -> None:
        """超长题名必须截断，否则会撑爆证据上下文。

        不能靠构造超长 `LiteratureRecord` 来测：`LiteratureRecord.title` 自己有
        长度上限，先炸的是它。这里直接测纯函数，覆盖截断分支即可。
        """
        record_with_long_title = record().model_copy(update={"title": "很长的题名" * 400})
        text = metadata_excerpt(record_with_long_title)
        assert len(text) <= 2000
        assert len(text) == 2000
