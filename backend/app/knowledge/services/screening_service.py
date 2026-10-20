"""筛选服务（Phase 2 · PRISMA 深模块）。

职责：
1. `record_search`：把一次检索落成**可复现日志 + 结果快照**（跨源去重）；
2. `decide`：追加一条决策，按结论推进 `current_stage`；
3. `prisma_counts`：由明细**派生** PRISMA 各阶段计数（不落库）。

去重口径与 `app.tools.literature.source_registry._merge_deduped` **完全一致**：
`DOI → source_id → 归一化题名`。跨源同一条文献只落一条快照，否则 PRISMA 的
「去重后记录数」会失真。
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException
from app.db.models.screening import (
    DecisionStage,
    FulltextStatus,
    ScreeningRecord,
    ScreeningVerdict,
    SearchLog,
)
from app.db.repositories.screening import ScreeningRepository
from app.tools.literature.models import LiteratureRecord

#: 从 `source_url` 提取规范 DOI 链接（与 `source_registry` 同正则）。
_DOI_URL_PATTERN = re.compile(r"^https?://(?:dx\.)?doi\.org/(?P<doi>.+)$", re.IGNORECASE)

#: 阶段推进顺序：标题摘要 → 全文 → 纳入（纳入为终态，不再前进）。
_NEXT_STAGE: dict[DecisionStage, DecisionStage] = {
    DecisionStage.TITLE_ABSTRACT: DecisionStage.FULLTEXT,
    DecisionStage.FULLTEXT: DecisionStage.INCLUDED,
}

#: 已知来源前缀（`source_id` 形如 `openalex:W123`）；其余按 arxiv 处理。
_KNOWN_SOURCE_PREFIXES = frozenset({"arxiv", "openalex", "semantic_scholar", "s2", "pubmed"})


def _normalize_title(title: str) -> str:
    """题名归一：NFKC + 折叠空白 + casefold（与 `source_registry` 一致）。"""
    normalized = unicodedata.normalize("NFKC", title)
    return " ".join(normalized.split()).casefold()


def _record_doi(record: LiteratureRecord) -> str | None:
    """从 `source_url` 提取归一化 DOI（只有 `https://doi.org/...` 规范链接才算）。"""
    match = _DOI_URL_PATTERN.match(str(record.source_url))
    if match is None:
        return None
    return match.group("doi").strip().strip("/").casefold() or None


def source_of(record: LiteratureRecord) -> str:
    """从 `source_id` 前缀推断来源（`openalex:W123` → `openalex`；无前缀 → arxiv）。"""
    prefix, separator, _ = record.source_id.partition(":")
    if separator and prefix.casefold() in _KNOWN_SOURCE_PREFIXES:
        return prefix.casefold()
    return "arxiv"


def dedupe_records(records: list[LiteratureRecord]) -> list[LiteratureRecord]:
    """跨源去重：`DOI → source_id → 归一化题名`，保留首次出现者。

    与 `source_registry._merge_deduped` 同口径——两处若不一致，
    PRISMA 的「去重后记录数」就会与实际筛选对象矛盾。
    """
    seen_dois: set[str] = set()
    seen_source_ids: set[str] = set()
    seen_titles: set[str] = set()
    kept: list[LiteratureRecord] = []

    for record in records:
        doi = _record_doi(record)
        source_id = record.source_id.strip().casefold()
        title = _normalize_title(record.title)
        if (doi is not None and doi in seen_dois) or source_id in seen_source_ids:
            continue
        if title and title in seen_titles:
            continue
        if doi is not None:
            seen_dois.add(doi)
        seen_source_ids.add(source_id)
        if title:
            seen_titles.add(title)
        kept.append(record)
    return kept


class ScreeningService:
    """筛选工作台业务逻辑（HTTP 层只做校验与响应映射）。"""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._repo = ScreeningRepository(db)

    # ===== 检索落库 =====

    async def record_search(
        self,
        task_id: uuid.UUID,
        *,
        query: str,
        sources: list[str],
        records: list[LiteratureRecord],
        hits_by_source: dict[str, int] | None = None,
        hits_total: int | None = None,
    ) -> SearchLog:
        """把一次检索落成日志 + 快照，并返回日志（含去重后计数）。

        **快照而非实时重查**：上游源内容会变，若筛选时重查则 PRISMA 数字无法复现。

        `hits_by_source` 是 PRISMA「识别」阶段的原始各源命中数（**去重前**）。
        未提供时按记录的来源分布推断——单源场景够用；多源（尤其含跨源重复）时
        应显式传入，否则「各源合计」可能与 `hits_total` 不一致。

        `hits_total` 是各源命中总和（去重前）。**未提供时**取
        `sum(hits_by_source)`（显式给了各源）或 `len(records)`（都没给）。
        显式传入可支持「上游只回了前 N 条、但实际命中更多」的真实场景
        （分页检索时 `records` 只是样本，命中总数由上游报告）。
        """
        deduped = dedupe_records(records)
        if hits_by_source is None:
            inferred: dict[str, int] = {}
            for record in records:
                source = source_of(record)
                inferred[source] = inferred.get(source, 0) + 1
            hits_by_source = inferred

        if hits_total is None:
            # 显式给了各源命中时以它为准；否则按实际记录数
            provided_by_source = sum(int(value) for value in hits_by_source.values())
            hits_total = provided_by_source if provided_by_source else len(records)

        log = await self._repo.add_search_log(
            SearchLog(
                task_id=task_id,
                query=query,
                sources=list(sources),
                hits_total=int(hits_total),
                hits_by_source=dict(hits_by_source),
                hits_deduped=len(deduped),
                executed_at=datetime.now(UTC),
            )
        )
        for item in deduped:
            await self._repo.add_record(
                ScreeningRecord(
                    search_log_id=log.id,
                    task_id=task_id,
                    source=source_of(item),
                    source_id=item.source_id,
                    title=item.title,
                    authors=list(item.authors),
                    abstract=item.abstract,
                    doi=_record_doi(item),
                    fulltext_status=FulltextStatus.UNAVAILABLE.value,
                    current_stage=DecisionStage.TITLE_ABSTRACT.value,
                )
            )
        return log

    # ===== 查询 =====

    async def list_records(
        self, task_id: uuid.UUID, *, stage: DecisionStage | None = None
    ) -> list[ScreeningRecord]:
        return await self._repo.list_records(task_id, stage=stage)

    async def list_search_logs(self, task_id: uuid.UUID) -> list[SearchLog]:
        return await self._repo.list_search_logs(task_id)

    async def get_record(self, record_id: uuid.UUID) -> ScreeningRecord:
        record = await self._repo.get(record_id)
        if record is None:
            raise NotFoundException("筛选记录不存在")
        return record

    # ===== 决策 =====

    async def decide(
        self,
        record_id: uuid.UUID,
        *,
        stage: DecisionStage,
        verdict: ScreeningVerdict,
        reviewer_id: uuid.UUID,
        exclusion_reason: str | None = None,
        note: str = "",
    ) -> ScreeningRecord:
        """追加决策并按结论推进阶段。

        推进规则（PRISMA）：
        - `include` → 进入下一阶段（标题摘要→全文→纳入；纳入阶段为终态）；
        - `maybe` → 停在当前阶段（待复核）；
        - `exclude` → 停在当前阶段（已排除，不再前进，但仍保留供计数）。
        """
        record = await self.get_record(record_id)
        await self._repo.append_decision(
            record_id,
            stage=stage,
            verdict=verdict,
            reviewer_id=reviewer_id,
            exclusion_reason=exclusion_reason,
            note=note,
        )
        if verdict is ScreeningVerdict.INCLUDE:
            next_stage = _NEXT_STAGE.get(stage)
            if next_stage is not None:
                record.current_stage = next_stage.value
                await self._db.flush()
        return record

    # ===== PRISMA 计数（派生，不落库）=====

    async def prisma_counts(self, task_id: uuid.UUID) -> dict[str, Any]:
        """派生 PRISMA 2020 各阶段计数。

        全部由明细实时计算——不落汇总数字，避免与明细不一致。
        「识别」阶段用 `search_log.hits_by_source`（**去重前**原始命中数），
        不能从快照反推：快照已去重，反推会让各源合计小于 `identified_total`。
        """
        logs = await self._repo.list_search_logs(task_id)
        records = await self._repo.list_records(task_id, limit=1000)

        # 识别：各源原始命中数（去重前）
        identified_by_source: dict[str, int] = {}
        for log in logs:
            for source, hits in (log.hits_by_source or {}).items():
                identified_by_source[source] = identified_by_source.get(source, 0) + int(hits)
        identified_total = sum(int(log.hits_total) for log in logs)
        duplicates_removed = sum(
            max(0, int(log.hits_total) - int(log.hits_deduped)) for log in logs
        )

        title_screened = 0
        title_excluded = 0
        fulltext_assessed = 0
        fulltext_excluded = 0
        included = 0
        exclusion_reasons: dict[str, int] = {}

        for record in records:
            for stage in (DecisionStage.TITLE_ABSTRACT, DecisionStage.FULLTEXT):
                decisions = await self._repo.list_decisions(record.id, stage)
                if not decisions:
                    continue
                latest = decisions[-1]  # 按 seq 正序，末尾即最新
                is_excluded = latest.verdict == ScreeningVerdict.EXCLUDE.value
                if stage is DecisionStage.TITLE_ABSTRACT:
                    title_screened += 1
                    if is_excluded:
                        title_excluded += 1
                else:
                    fulltext_assessed += 1
                    if is_excluded:
                        fulltext_excluded += 1
                if is_excluded and latest.exclusion_reason:
                    reason = latest.exclusion_reason
                    exclusion_reasons[reason] = exclusion_reasons.get(reason, 0) + 1

            if record.current_stage == DecisionStage.INCLUDED.value:
                included += 1

        return {
            "identified_by_source": identified_by_source,
            "identified_total": identified_total,
            "duplicates_removed": duplicates_removed,
            "records_screened": len(records),
            "title_abstract": {"screened": title_screened, "excluded": title_excluded},
            "fulltext": {"assessed": fulltext_assessed, "excluded": fulltext_excluded},
            "included": included,
            "exclusion_reasons": exclusion_reasons,
        }


__all__ = [
    "ScreeningService",
    "dedupe_records",
    "source_of",
]
