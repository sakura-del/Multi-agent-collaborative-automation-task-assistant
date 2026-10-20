"""去重口径一致性测试（Phase 2 · PRISMA Task 3）。

## 为什么必须锁这个

`ScreeningService.dedupe_records` 与 `source_registry._merge_deduped` 是**两处独立实现**：

- `source_registry` 在**检索时**去重（决定哪些记录进入后续流程）；
- `screening_service` 在**落快照时**去重（决定 PRISMA 的「去重后记录数」）。

两者若口径不同，PRISMA 的「去重后记录数」就会与实际筛选对象矛盾——
比如检索层去重了 5 条，快照层只去重 3 条，流程图上写的数字就是错的。

本文件用同一份输入喂两条路径，断言结果**集合相同**。
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.knowledge.services.screening_service import dedupe_records
from app.tools.literature.models import LiteratureRecord
from app.tools.literature.source_registry import _merge_deduped


def _record(
    source_id: str,
    title: str,
    *,
    doi: str | None = None,
) -> LiteratureRecord:
    """构造记录；`doi` 会给成规范 DOI 链接（两条路径都按此提取 DOI）。"""
    source_url = f"https://doi.org/{doi}" if doi else f"https://arxiv.org/abs/{source_id}"
    return LiteratureRecord(
        source_id=source_id,
        title=title,
        authors=["A"],
        abstract="a",
        published_at=datetime(2024, 1, 1, tzinfo=UTC),
        source_url=source_url,
        pdf_url=f"{source_url}.pdf",
        categories=[],
    )


def _assert_same_dedupe(records: list[LiteratureRecord]) -> None:
    """两条路径对同一输入的去重结果必须一致。"""
    registry_result = _merge_deduped([records])
    service_result = dedupe_records(records)

    registry_ids = [record.source_id for record in registry_result]
    service_ids = [record.source_id for record in service_result]

    assert service_ids == registry_ids, (
        "去重口径不一致：\n"
        f"  source_registry → {registry_ids}\n"
        f"  screening_service → {service_ids}"
    )


class TestDedupeParity:
    def test_single_record_is_kept(self) -> None:
        _assert_same_dedupe([_record("a1", "Unique Title")])

    def test_same_doi_deduped_by_both(self) -> None:
        _assert_same_dedupe(
            [
                _record("a1", "Title A", doi="10.1234/abc"),
                _record("o1", "Completely Different", doi="10.1234/ABC"),
            ]
        )

    def test_same_source_id_deduped_by_both(self) -> None:
        _assert_same_dedupe(
            [
                _record("a1", "Title A"),
                _record("a1", "Totally Different Title"),
            ]
        )

    def test_same_normalized_title_deduped_by_both(self) -> None:
        _assert_same_dedupe(
            [
                _record("a1", "RAG  Survey"),
                _record("o1", "rag survey"),
            ]
        )

    def test_distinct_records_kept_by_both(self) -> None:
        _assert_same_dedupe([_record("a1", "Title A"), _record("a2", "Title B")])

    def test_mixed_duplicates_and_uniques(self) -> None:
        """混合场景：每条判重键都有命中，且要保留正确的那条。"""
        _assert_same_dedupe(
            [
                _record("a1", "Title One", doi="10.1/x"),
                _record("a2", "Title Two"),
                _record("o1", "Title One", doi="10.1/X"),  # DOI 重复
                _record("a2", "Other Title"),  # source_id 重复
                _record("a3", "TITLE  TWO"),  # 题名重复
                _record("a4", "Unique Four"),
            ]
        )

    def test_fullwidth_and_case_title_normalization(self) -> None:
        """全角/大小写差异应被 NFKC + casefold 归一后判重。"""
        _assert_same_dedupe(
            [
                _record("a1", "RAG：评估"),
                _record("o1", "rag：评估"),
            ]
        )
