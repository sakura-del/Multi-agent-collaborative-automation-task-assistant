"""自定义论文抽取表抽取引擎与 CSV 安全导出（Issue #36）。

职责：
1. 按论文聚合待抽上下文：
   - `knowledge_doc`：按列关键词从该文档 `DocChunk` 检索 Top-3 片段，去重拼接 `[P1]`..`[Pn]`
     （总长上限 6000 字符），继承 `DocChunk.position`/`chunk_meta` 的 `EvidenceLocator`；
   - `screening_record`：仅使用标题+摘要作为 `[P1]`，`evidence_scope="abstract_only"`、
     `locator=None`；无摘要时直接落库 `not_reported`，0 次 LLM 调用。
2. 单篇论文一次结构化 JSON 调用，并发上限 `asyncio.Semaphore(4)`；
3. 落库前空白归一化子串校验：未在送入片段中出现的幻觉 `quote` 一律清空；
4. CSV 安全导出：UTF-8 BOM (`utf-8-sig`) + 公式注入前置单引号转义 + RFC 4180 转义。
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models.extraction import ExtractionCell, ExtractionTable
from app.db.models.knowledge import DocChunk, KnowledgeDoc
from app.db.models.screening import ScreeningRecord
from app.db.models.task import Task
from app.knowledge.services.document_service import extract_locator_metadata
from app.llm.models import Message, MessageRole
from app.schemas.extraction import ExtractionColumnSpec

logger = get_logger(__name__)

#: 单篇论文送给 LLM 的片段总字符预算（省 token、不塞整篇 PDF）
MAX_CONTEXT_CHARS = 6000
#: 单列按相关性检索的 Top-K 分块数
TOP_CHUNKS_PER_COLUMN = 3
#: 多篇论文并发抽取上限
DEFAULT_MAX_CONCURRENCY = 4

_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_TOKEN_RE = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]", re.IGNORECASE)

_SYSTEM_PROMPT = """你是严谨的学术论文数据抽取助手。你会收到一篇论文的若干带编号原文片段（如 [P1]、[P2]）以及需要提取的列定义。
请严格仅基于给定片段提取信息，并只输出 JSON 数组，不要输出任何额外文字。

输出格式（数组每项对应一个 column_key）：
[
  {
    "column_key": "sample_size",
    "status": "extracted",
    "value": "简洁准确的提取值",
    "passage_id": "P1",
    "quote": "片段中的原文逐字子句（不得改写或省略）",
    "confidence": 0.9
  }
]

硬规则：
1. 文档片段中未明确提及的字段，必须返回 "status": "not_reported"、"value": null、"quote": null，严禁猜测或引入外部知识。
2. "quote" 必须是对应片段中的**原样子串**（允许空白折叠差异，不要改写、翻译或加省略号）。
3. "passage_id" 必须是给定的编号之一（如 "P1"）；无法定位时填 null。
4. "confidence" 为 0 到 1 之间的小数。
5. 只输出 JSON 数组，不要包含 Markdown 说明或多余字符。"""


def _normalize_for_match(text: str) -> str:
    """空白折叠 + 忽略大小写，用于落库前原文子串校验。"""
    return re.sub(r"\s+", " ", text).strip().casefold()


def _tokenize_terms(text: str) -> set[str]:
    """轻量中英文分词 + 中文 2-gram，用于按列从文档分块中筛选高相关片段。"""
    raw_tokens = [t.casefold() for t in _TOKEN_RE.findall(text)]
    terms = set(raw_tokens)
    cjk_chars = [t for t in raw_tokens if len(t) == 1 and "\u4e00" <= t <= "\u9fff"]
    for idx in range(len(cjk_chars) - 1):
        terms.add(cjk_chars[idx] + cjk_chars[idx + 1])
    return terms


def _safe_csv_cell(value: object) -> str:
    """防 CSV 公式注入：对以危险前缀开头的单元格前置单引号。"""
    text = "" if value is None else str(value)
    if text and text[0] in _CSV_FORMULA_PREFIXES:
        return "'" + text
    return text


def format_locator_text(locator: dict[str, Any] | None) -> str:
    """将定位字典格式化为紧凑 CSV 文本（如 `PDF p.5 | 3.2 实验设置 | Table 2`）。"""
    if not locator:
        return ""
    parts: list[str] = []
    start = locator.get("page_start")
    end = locator.get("page_end")
    if isinstance(start, int) and not isinstance(start, bool) and start >= 1:
        span = (
            f"–{end}" if isinstance(end, int) and not isinstance(end, bool) and end > start else ""
        )
        parts.append(f"PDF p.{start}{span}")
    if locator.get("section_title"):
        parts.append(str(locator["section_title"]))
    if locator.get("table_ref"):
        parts.append(str(locator["table_ref"]))
    return " | ".join(parts)


def _source_type_label(source_type: str) -> str:
    return "知识库文档" if source_type == "knowledge_doc" else "PRISMA 筛选文献"


def export_table_csv(table: Any, *, include_quotes: bool = True) -> bytes:
    """将抽取表序列化为 Excel 兼容的 UTF-8 BOM CSV 字节流（含防公式注入）。"""
    raw_columns = list(getattr(table, "columns", []) or [])
    raw_sources = list(getattr(table, "sources", []) or [])
    raw_cells = list(getattr(table, "cells", []) or [])

    columns: list[tuple[str, str]] = []
    for col in raw_columns:
        if isinstance(col, dict):
            columns.append((str(col["key"]), str(col["label"])))
        else:
            columns.append((str(col.key), str(col.label)))

    cell_map: dict[tuple[str, str, str], Any] = {}
    for cell in raw_cells:
        if isinstance(cell, dict):
            key = (str(cell["source_type"]), str(cell["source_id"]), str(cell["column_key"]))
        else:
            key = (str(cell.source_type), str(cell.source_id), str(cell.column_key))
        cell_map[key] = cell

    header = ["论文标题", "年份", "来源类型", "证据范围"]
    for _col_key, col_label in columns:
        header.append(col_label)
        if include_quotes:
            header.append(f"{col_label}_原文引用")
            header.append(f"{col_label}_定位")

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow([_safe_csv_cell(item) for item in header])

    for src in raw_sources:
        if isinstance(src, dict):
            src_type = str(src["source_type"])
            src_id = str(src["source_id"])
            title = str(src.get("title") or "")
            year = src.get("year")
        else:
            src_type = str(src.source_type)
            src_id = str(src.source_id)
            title = str(src.title)
            year = src.year

        row_cells = [cell_map.get((src_type, src_id, col_key)) for col_key, _ in columns]
        scope_value = "摘要" if src_type == "screening_record" else "全文"
        for cell in row_cells:
            if cell is None:
                continue
            scope = cell.get("evidence_scope") if isinstance(cell, dict) else cell.evidence_scope
            if scope == "abstract_only":
                scope_value = "摘要"
                break

        row: list[str] = [
            _safe_csv_cell(title),
            _safe_csv_cell("" if year is None else year),
            _safe_csv_cell(_source_type_label(src_type)),
            _safe_csv_cell(scope_value),
        ]

        for cell in row_cells:
            if cell is None:
                status = "pending"
                value = None
                quote = None
                locator = None
            elif isinstance(cell, dict):
                status = str(cell.get("status") or "pending")
                value = cell.get("value")
                quote = cell.get("quote")
                locator = cell.get("locator")
            else:
                status = str(cell.status)
                value = cell.value
                quote = cell.quote
                locator = (
                    cell.locator.model_dump(mode="json")
                    if hasattr(cell.locator, "model_dump")
                    else cell.locator
                )

            display_val = value if (status == "extracted" and value) else "未报告"
            row.append(_safe_csv_cell(display_val))
            if include_quotes:
                row.append(_safe_csv_cell(quote or ""))
                row.append(_safe_csv_cell(format_locator_text(locator)))

        writer.writerow(row)

    return buf.getvalue().encode("utf-8-sig")


@dataclass(frozen=True)
class _ContextPassage:
    """送入 LLM 的单条编号片段。"""

    pid: str
    text: str
    locator: dict[str, Any] | None


@dataclass(frozen=True)
class _SourceExtractionJob:
    """单篇论文的待抽取上下文（在并发调 LLM 前由 DB 同步准备完毕）。"""

    source_type: str
    source_id: str
    title: str
    evidence_scope: Literal["full_text", "abstract_only"]
    passages: tuple[_ContextPassage, ...]
    columns: tuple[ExtractionColumnSpec, ...]


@dataclass(frozen=True)
class _CellOutcome:
    """单格抽取结果（已做子串防幻觉清洗）。"""

    source_type: str
    source_id: str
    column_key: str
    status: Literal["extracted", "not_reported", "error"]
    value: str | None
    quote: str | None
    passage: str | None
    locator: dict[str, Any] | None
    evidence_scope: Literal["full_text", "abstract_only"]
    confidence: float | None


def _select_chunks_for_columns(
    chunks: Sequence[DocChunk],
    columns: Sequence[ExtractionColumnSpec],
    *,
    top_k_per_column: int = TOP_CHUNKS_PER_COLUMN,
    max_chars: int = MAX_CONTEXT_CHARS,
) -> list[DocChunk]:
    """按列关键词从文档分块中挑选 Top-K 并去重，按原序截断在预算内。"""
    if not chunks:
        return []

    chunk_terms: list[tuple[DocChunk, set[str], dict[str, Any]]] = []
    for chunk in chunks:
        loc = extract_locator_metadata(chunk.position, chunk.chunk_meta)
        extra_text = " ".join(str(loc[k]) for k in ("section_title", "table_ref") if loc.get(k))
        terms = _tokenize_terms(f"{chunk.content}\n{extra_text}")
        chunk_terms.append((chunk, terms, loc))

    selected_ids: set[UUID] = set()
    for col in columns:
        query_terms = _tokenize_terms(f"{col.label} {col.prompt}")
        scored: list[tuple[float, int, DocChunk]] = []
        for chunk, terms, loc in chunk_terms:
            overlap = len(query_terms & terms)
            bonus = (
                0.5 if (overlap > 0 and (loc.get("section_title") or loc.get("table_ref"))) else 0.0
            )
            score = float(overlap) + bonus
            scored.append((score, -chunk.chunk_index, chunk))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)

        picked = [item[2] for item in scored[:top_k_per_column] if item[0] > 0]
        if not picked:
            picked = list(chunks[:top_k_per_column])
        for item in picked:
            selected_ids.add(item.id)

    ordered = [c for c in chunks if c.id in selected_ids]
    ordered.sort(key=lambda c: c.chunk_index)

    budget = max_chars
    result: list[DocChunk] = []
    for chunk in ordered:
        length = len(chunk.content)
        if result and length > budget:
            continue
        result.append(chunk)
        budget -= length
        if budget <= 0:
            break
    return result


def _extract_json_array(raw_text: str) -> list[dict[str, Any]]:
    """从模型响应中宽松解析 JSON 数组（兼容 Markdown 代码围栏）。"""
    text = (raw_text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("[")
        end = text.rfind("]")
        if start < 0 or end <= start:
            raise ValueError("模型输出不包含合法 JSON 数组") from None
        parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, list):
        raise ValueError("模型输出顶层必须为 JSON 数组")
    return [item for item in parsed if isinstance(item, dict)]


def _sanitize_cell_outcome(
    raw: dict[str, Any] | None,
    *,
    job: _SourceExtractionJob,
    column_key: str,
) -> _CellOutcome:
    """校验单格模型输出：规范空值，并强制执行空白归一化子串校验防止幻觉原句落库。"""
    if raw is None:
        return _CellOutcome(
            source_type=job.source_type,
            source_id=job.source_id,
            column_key=column_key,
            status="not_reported",
            value=None,
            quote=None,
            passage=None,
            locator=None,
            evidence_scope=job.evidence_scope,
            confidence=0.0,
        )

    raw_status = str(raw.get("status") or "").strip().lower()
    raw_value = raw.get("value")
    value = str(raw_value).strip() if raw_value is not None else ""

    raw_conf = raw.get("confidence")
    confidence: float | None = None
    if raw_conf is not None:
        try:
            confidence = min(1.0, max(0.0, float(raw_conf)))
        except (TypeError, ValueError):
            confidence = None

    if raw_status != "extracted" or not value:
        return _CellOutcome(
            source_type=job.source_type,
            source_id=job.source_id,
            column_key=column_key,
            status="not_reported",
            value=None,
            quote=None,
            passage=None,
            locator=None,
            evidence_scope=job.evidence_scope,
            confidence=confidence,
        )

    passage_by_id = {p.pid.upper(): p for p in job.passages}
    requested_pid = str(raw.get("passage_id") or "").strip().upper()
    preferred_passage = passage_by_id.get(requested_pid)

    raw_quote = raw.get("quote")
    quote_candidate = str(raw_quote).strip() if raw_quote is not None else ""
    norm_quote = _normalize_for_match(quote_candidate) if quote_candidate else ""

    verified_quote: str | None = None
    matched_passage: _ContextPassage | None = None

    if norm_quote:
        # 先检查模型指明的片段，未命中再退化遍历全部片段
        candidates: list[_ContextPassage] = []
        if preferred_passage is not None:
            candidates.append(preferred_passage)
        candidates.extend(p for p in job.passages if p is not preferred_passage)

        for candidate in candidates:
            if norm_quote in _normalize_for_match(candidate.text):
                verified_quote = quote_candidate
                matched_passage = candidate
                break

    if matched_passage is None:
        matched_passage = preferred_passage

    locator: dict[str, Any] | None = None
    if job.evidence_scope == "full_text" and matched_passage is not None:
        locator = matched_passage.locator

    return _CellOutcome(
        source_type=job.source_type,
        source_id=job.source_id,
        column_key=column_key,
        status="extracted",
        value=value,
        quote=verified_quote,
        passage=matched_passage.text if matched_passage is not None else None,
        locator=locator,
        evidence_scope=job.evidence_scope,
        confidence=confidence,
    )


class TableExtractor:
    """按列检索式论文批量抽取服务（并发上限 4，单篇失败隔离）。"""

    def __init__(self, llm: Any, *, max_concurrency: int = DEFAULT_MAX_CONCURRENCY) -> None:
        self._llm = llm
        self._max_concurrency = max(1, max_concurrency)

    async def run(
        self,
        session: AsyncSession,
        table_id: UUID,
        user_id: UUID,
        *,
        column_keys: Sequence[str] = (),
        source_ids: Sequence[UUID] = (),
    ) -> ExtractionTable:
        """执行整表或指定列/指定论文抽取，并回写单元格与表状态。"""
        table = (
            await session.execute(
                select(ExtractionTable).where(
                    ExtractionTable.id == table_id,
                    ExtractionTable.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        if table is None:
            raise ValueError(f"抽取表不存在或无权访问：{table_id}")

        table.status = "running"
        table.error = None
        await session.commit()

        try:
            all_columns = [ExtractionColumnSpec.model_validate(c) for c in (table.columns or [])]
            target_col_keys = set(column_keys)
            target_columns = (
                [c for c in all_columns if c.key in target_col_keys]
                if target_col_keys
                else all_columns
            )

            target_source_ids = {str(sid) for sid in source_ids}
            target_sources = [
                src
                for src in (table.sources or [])
                if not target_source_ids or str(src.get("source_id")) in target_source_ids
            ]

            jobs = await self._build_jobs(
                session=session,
                user_id=user_id,
                sources=target_sources,
                columns=target_columns,
            )

            semaphore = asyncio.Semaphore(self._max_concurrency)

            async def _run_one(job: _SourceExtractionJob) -> list[_CellOutcome]:
                async with semaphore:
                    return await self._extract_source(job)

            nested_outcomes = await asyncio.gather(*(_run_one(job) for job in jobs))
            outcomes = [item for batch in nested_outcomes for item in batch]

            await self._upsert_outcomes(session, table.id, outcomes)
            table.status = "completed"
            table.error = None
            table.updated_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(table)
            return table
        except Exception as exc:
            logger.exception("extraction_table_failed", table_id=str(table_id), error=str(exc))
            table.status = "failed"
            table.error = str(exc)[:1000]
            table.updated_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(table)
            return table

    async def _build_jobs(
        self,
        *,
        session: AsyncSession,
        user_id: UUID,
        sources: Sequence[dict[str, Any]],
        columns: Sequence[ExtractionColumnSpec],
    ) -> list[_SourceExtractionJob]:
        """在主协程一次性从数据库装载上下文，避免并发共享 AsyncSession。"""
        jobs: list[_SourceExtractionJob] = []
        col_tuple = tuple(columns)
        if not col_tuple:
            return jobs

        for src in sources:
            source_type = str(src.get("source_type") or "")
            source_id_str = str(src.get("source_id") or "")
            title = str(src.get("title") or "")
            try:
                source_uuid = UUID(source_id_str)
            except ValueError:
                continue

            if source_type == "knowledge_doc":
                doc = (
                    await session.execute(
                        select(KnowledgeDoc).where(
                            KnowledgeDoc.id == source_uuid,
                            KnowledgeDoc.user_id == user_id,
                        )
                    )
                ).scalar_one_or_none()
                if doc is None:
                    continue
                chunk_rows = (
                    (
                        await session.execute(
                            select(DocChunk)
                            .where(DocChunk.doc_id == doc.id)
                            .order_by(DocChunk.chunk_index)
                        )
                    )
                    .scalars()
                    .all()
                )
                picked = _select_chunks_for_columns(chunk_rows, col_tuple)
                passages = tuple(
                    _ContextPassage(
                        pid=f"P{idx}",
                        text=chunk.content,
                        locator=extract_locator_metadata(chunk.position, chunk.chunk_meta) or None,
                    )
                    for idx, chunk in enumerate(picked, start=1)
                )
                jobs.append(
                    _SourceExtractionJob(
                        source_type="knowledge_doc",
                        source_id=source_id_str,
                        title=title or doc.title,
                        evidence_scope="full_text",
                        passages=passages,
                        columns=col_tuple,
                    )
                )
            elif source_type == "screening_record":
                record = (
                    await session.execute(
                        select(ScreeningRecord)
                        .join(Task, Task.id == ScreeningRecord.task_id)
                        .where(
                            ScreeningRecord.id == source_uuid,
                            Task.user_id == user_id,
                        )
                    )
                ).scalar_one_or_none()
                if record is None:
                    continue
                abstract_text = (record.abstract or "").strip()
                if abstract_text:
                    text = f"{record.title.strip()}\n\n{abstract_text}"[:MAX_CONTEXT_CHARS]
                    passages = (_ContextPassage(pid="P1", text=text, locator=None),)
                else:
                    passages = ()
                jobs.append(
                    _SourceExtractionJob(
                        source_type="screening_record",
                        source_id=source_id_str,
                        title=title or record.title,
                        evidence_scope="abstract_only",
                        passages=passages,
                        columns=col_tuple,
                    )
                )

        return jobs

    async def _extract_source(self, job: _SourceExtractionJob) -> list[_CellOutcome]:
        """对单篇论文执行一次结构化抽取；无片段时直接 not_reported（0 次 LLM 调用）。"""
        if not job.passages:
            return [
                _CellOutcome(
                    source_type=job.source_type,
                    source_id=job.source_id,
                    column_key=col.key,
                    status="not_reported",
                    value=None,
                    quote=None,
                    passage=None,
                    locator=None,
                    evidence_scope=job.evidence_scope,
                    confidence=None,
                )
                for col in job.columns
            ]

        if self._llm is None:
            return [
                _CellOutcome(
                    source_type=job.source_type,
                    source_id=job.source_id,
                    column_key=col.key,
                    status="error",
                    value=None,
                    quote=None,
                    passage=None,
                    locator=None,
                    evidence_scope=job.evidence_scope,
                    confidence=None,
                )
                for col in job.columns
            ]

        user_payload = {
            "paper_title": job.title,
            "evidence_scope": job.evidence_scope,
            "columns": [
                {"column_key": c.key, "label": c.label, "prompt": c.prompt} for c in job.columns
            ],
            "passages": [{"passage_id": p.pid, "text": p.text} for p in job.passages],
        }
        messages = [
            Message(role=MessageRole.SYSTEM, content=_SYSTEM_PROMPT),
            Message(
                role=MessageRole.USER,
                content=json.dumps(user_payload, ensure_ascii=False),
            ),
        ]

        try:
            response = await self._llm.chat(messages, temperature=0.1)
            raw_content = getattr(response, "content", "") or ""
            items = _extract_json_array(raw_content)
        except Exception as exc:
            logger.warning(
                "extraction_source_failed",
                source_type=job.source_type,
                source_id=job.source_id,
                error=str(exc),
            )
            return [
                _CellOutcome(
                    source_type=job.source_type,
                    source_id=job.source_id,
                    column_key=col.key,
                    status="error",
                    value=None,
                    quote=None,
                    passage=None,
                    locator=None,
                    evidence_scope=job.evidence_scope,
                    confidence=None,
                )
                for col in job.columns
            ]

        by_key: dict[str, dict[str, Any]] = {}
        for item in items:
            key = str(item.get("column_key") or "").strip()
            if key and key not in by_key:
                by_key[key] = item

        return [
            _sanitize_cell_outcome(by_key.get(col.key), job=job, column_key=col.key)
            for col in job.columns
        ]

    async def _upsert_outcomes(
        self,
        session: AsyncSession,
        table_id: UUID,
        outcomes: Sequence[_CellOutcome],
    ) -> None:
        """仅更新目标坐标单元格，绝不覆盖未指定的旧列。"""
        if not outcomes:
            return
        existing_rows = (
            (
                await session.execute(
                    select(ExtractionCell).where(ExtractionCell.table_id == table_id)
                )
            )
            .scalars()
            .all()
        )
        existing_by_coord: dict[tuple[str, str, str], ExtractionCell] = {
            (row.source_type, row.source_id, row.column_key): row for row in existing_rows
        }

        now = datetime.now(UTC)
        for item in outcomes:
            coord = (item.source_type, item.source_id, item.column_key)
            cell = existing_by_coord.get(coord)
            if cell is None:
                cell = ExtractionCell(
                    table_id=table_id,
                    source_type=item.source_type,
                    source_id=item.source_id,
                    column_key=item.column_key,
                )
                session.add(cell)
                existing_by_coord[coord] = cell
            cell.status = item.status
            cell.value = item.value
            cell.quote = item.quote
            cell.passage = item.passage
            cell.locator = item.locator
            cell.evidence_scope = item.evidence_scope
            cell.confidence = item.confidence
            cell.updated_at = now
