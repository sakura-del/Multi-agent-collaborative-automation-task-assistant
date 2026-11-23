"""增量刷新：只检索上次之后的新证据，生成带 `[E#]` 的增量摘要（Issue #27）。

## 这个模块解决什么

任务原本是一次性的：跑完就冻结。文献综述的真实需求是持续的——同一主题隔几周
就冒出新论文。刷新做三件事，**不重跑整篇综述**：

1. 用任务计划里的检索式重新检索，窗口从「上次刷新（或任务完成）− overlap」开始；
2. 与既有记录（`screening_record` + 参考文献表）比对，只留**新增**的那些；
3. 让模型基于新文献写 3–6 条要点，**每条必须带 `[E#]`**，再过一遍引用一致性闸门
   （Issue #25 的同一把尺子）——增量摘要绝不能比正文更松。

## 为什么摘要的「新增文献」清单是确定性拼装

模型写要点时会漏引、错引编号。要点由模型写，文末清单由代码按 `index` 逐条渲染，
保证 `[E1]..[En]` 恰好等于参考文献表的编号集合；闸门因此总能通过，也不会出现
「要点提到一篇、清单里没有」的脱节（#17 的老问题在增量场景会原样复现）。

## 为什么模型挂了也要产出摘要

刷新是「后台自己跑」的能力，用户不在场。模型超时/限流时退化成「本期新增 N 篇 +
清单」，比一条 `failed` 记录有用得多；失败原因记在 `error` 里，`tokens_used` 照实记。
"""

from __future__ import annotations

import asyncio
import json
import re
import unicodedata
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.llm.models import Message, MessageRole
from app.orchestration.validation.citation_consistency import audit_citation_alignment
from app.tools.literature.models import LiteratureRecord, LiteratureSearch

logger = get_logger(__name__)

#: 摘要提示词：只允许用给定条目、必须带编号、不要复述整篇综述
SUMMARY_SYSTEM = (
    "你是文献更新助手。用户有一篇已完成的文献综述，现在要汇报**本期新增的文献**。\n"
    "严格基于给出的条目写作，不得引入条目之外的知识，不得编造作者或结论。\n"
    "输出 3–6 条中文要点（Markdown 无序列表），每条 40–120 字，说明这批新文献补充了"
    "哪些证据、修正或冲突了什么看法。\n"
    "**每条要点至少引用一个编号**，格式为 [E1]、[E2]……，编号只能取自给定条目，"
    "不要输出参考文献列表（文末清单由系统生成），不要输出标题。"
)

#: 提示词里最多塞多少条新文献（再多也超出要点价值，且会挤掉上下文）
_MAX_PROMPT_ENTRIES = 12

_CITATION_MARKER = re.compile(r"\[E(\d+)\]")

#: 全部检索式失败后的等待时间（给上游限流留恢复窗口）
SEARCH_RETRY_DELAY_SECONDS = 20.0


def _now() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    """把数据库里读出的时间统一成带时区的 UTC。

    SQLite（单测）会把 `DateTime(timezone=True)` 读成 naive，直接和
    `datetime.now(UTC)` 相减就是 `can't subtract offset-naive and offset-aware`；
    PostgreSQL 返回 aware，但同一段代码要能跑在两种库上。
    """
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def normalize_title(title: str) -> str:
    """题名归一：NFKC + 折叠空白 + casefold（与 `screening_service` 同口径）。"""
    return " ".join(unicodedata.normalize("NFKC", title).split()).casefold()


def record_keys(record: LiteratureRecord) -> set[str]:
    """一条文献的全部身份键：`doi:...` / `sid:...` / `title:...`。

    与 `ScreeningService` 的去重口径一致（DOI → source_id → 归一化题名），
    任何一键命中即视为「同一篇」。多键是为了跨源场景：arXiv 条目没有 DOI，
    OpenAlex 条目有；只比 title 又会漏掉题名微调。
    """
    keys: set[str] = set()
    url = str(record.source_url or "")
    match = re.match(r"^https?://(?:dx\.)?doi\.org/(?P<doi>.+)$", url, re.IGNORECASE)
    if match is not None:
        doi = match.group("doi").strip().strip("/").casefold()
        if doi:
            keys.add(f"doi:{doi}")
    source_id = record.source_id.strip().casefold()
    if source_id:
        keys.add(f"sid:{source_id}")
    title = normalize_title(record.title)
    if title:
        keys.add(f"title:{title}")
    return keys


#: 检索式里出现但不带信息量的英文词（否则「for」「the」会让任何论文都算相关）
_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "into",
        "onto",
        "that",
        "this",
        "these",
        "those",
        "a",
        "an",
        "of",
        "in",
        "on",
        "to",
        "by",
        "as",
        "at",
        "is",
        "are",
        "be",
        "or",
        "vs",
        "using",
        "based",
        "toward",
        "towards",
        "via",
        "study",
        "survey",
        "review",
        "analysis",
        "method",
        "methods",
        "approach",
        "approaches",
        "system",
        "systems",
        "evaluation",
        "assessment",
        "quality",
        "dataset",
        "datasets",
        "benchmark",
        "benchmarks",
    }
)


def relevance_terms(
    *, keywords: Sequence[str], queries: Sequence[str], limit: int = 40
) -> list[str]:
    """从关键词与检索式里抽「主题词」，用于把跑题的新文献挡在门外。

    ## 为什么需要这道过滤

    上游检索是**全文/宽匹配**语义：一条长检索式里的任意词命中就算一条。窗口
    只有一两周时，剩下来的往往全是「同一周上传、但和主题无关」的论文——实测
    RAG 评估主题刷新出颈动脉超声、老年外语学习。把窗口内的每一篇都当新证据，
    比刷新没结果更糟：用户会开始怀疑整篇综述的可信度。

    ## 口径

    中文按 2 字以上、英文按 3 字以上取词，去掉高频虚词；命中**任意一个**术语
    即视为相关（宁可放宽召回，也别把真正的同义研究误杀）。
    """
    terms: list[str] = []
    seen: set[str] = set()

    def add(term: str) -> None:
        normalized = term.strip().casefold()
        if len(normalized) < 2 or normalized in seen:
            return
        seen.add(normalized)
        terms.append(normalized)

    for keyword in keywords:
        if keyword.strip():
            add(keyword.strip())

    for query in queries:
        for token in re.split(r"[^0-9A-Za-z\u4e00-\u9fa5]+", query):
            token = token.strip()
            if not token:
                continue
            is_ascii = token.isascii()
            if is_ascii and (len(token) < 3 or token.casefold() in _STOPWORDS):
                continue
            if not is_ascii and len(token) < 2:
                continue
            add(token)
            if len(terms) >= limit:
                return terms
    return terms


def is_relevant(record: LiteratureRecord, terms: Sequence[str]) -> bool:
    """题名或摘要命中任一主题词即算相关（大小写不敏感）。"""
    if not terms:
        return True
    haystack = f"{record.title} {record.abstract or ''}".casefold()
    return any(term in haystack for term in terms)


def collect_queries(task: Any, *, limit: int) -> list[str]:
    """取检索式：优先任务计划（用户确认过），退回任务配置的主题/关键词。

    **为什么优先计划**：计划里的 `search_queries` 是规划模型针对该主题生成的，
    比 `config["keywords"]` 更贴近实际执行过的检索；用它刷新才能和原文的覆盖面
    对齐。计划缺失（历史任务/规划失败）时才退回配置。
    """
    plan = getattr(task, "plan", None) or {}
    raw = plan.get("search_queries") if isinstance(plan, dict) else None
    queries = [str(item).strip() for item in (raw or []) if str(item).strip()]

    if not queries:
        config = getattr(task, "config", None) or {}
        topic = str(config.get("topic") or getattr(task, "title", "") or "").strip()
        keywords = [str(k).strip() for k in (config.get("keywords") or []) if str(k).strip()]
        if topic:
            queries.append(topic)
        if keywords:
            queries.append(" ".join(keywords[:4]))

    # 去重保序（大小写不敏感）
    seen: set[str] = set()
    unique: list[str] = []
    for query in queries:
        key = query.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(query)
    return unique[:limit]


@dataclass
class RefreshOutcome:
    """一次刷新的结果（供调用方发邮件/记日志，不依赖 ORM 对象）。"""

    refresh_id: uuid.UUID
    task_id: uuid.UUID
    user_id: uuid.UUID
    task_title: str
    status: str
    new_count: int = 0
    duplicate_count: int = 0
    filtered_count: int = 0
    hits_total: int = 0
    summary_markdown: str | None = None
    citation_ok: bool = True
    tokens_used: int = 0
    error: str | None = None
    new_titles: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)


@dataclass
class _TaskContext:
    task_id: uuid.UUID
    user_id: uuid.UUID
    title: str
    queries: list[str]
    keywords: list[str]
    known: set[str]


def _entry_year(record: LiteratureRecord) -> str:
    published = record.published_at
    return str(published.year) if published is not None else "n.d."


def _entry_line(index: int, record: LiteratureRecord) -> str:
    """参考文献列表的一行：`[E1] 题名 — 作者（年）— 链接`。"""
    authors = "、".join(record.authors[:3]) if record.authors else "佚名"
    if len(record.authors) > 3:
        authors += " 等"
    url = str(record.source_url or "")
    tail = f" — {url}" if url else ""
    return f"[E{index}] {record.title} — {authors}（{_entry_year(record)}）{tail}"


def compose_summary(
    *,
    bullets: str | None,
    records: list[LiteratureRecord],
    window_from: datetime | None,
    window_to: datetime | None,
    hits_total: int,
    duplicate_count: int,
    filtered_count: int = 0,
    fallback_reason: str | None = None,
) -> str:
    """拼出完整摘要：模型要点 + 确定性清单。

    清单由代码渲染，是 `[E#]` 编号的唯一真源；要点里的编号若越界，闸门会在
    调用方那里拦下（并重试一次）。
    """
    start = window_from.date().isoformat() if window_from else "上次刷新"
    end = window_to.date().isoformat() if window_to else _now().date().isoformat()
    lines: list[str] = [f"## 新增证据摘要（{end}）", ""]
    filtered = f"，另有 {filtered_count} 篇与主题无关已过滤" if filtered_count else ""
    lines.append(
        f"本期检索命中 {hits_total} 篇，去重后**新增 {len(records)} 篇**"
        f"（窗口 {start} → {end}，与既有记录重复 {duplicate_count} 条{filtered}）。"
    )
    lines.append("")

    if records:
        lines.append("### 要点")
        lines.append("")
        if bullets and bullets.strip():
            lines.append(bullets.strip())
        else:
            reason = f"（{fallback_reason}）" if fallback_reason else ""
            lines.append(f"- 本期新增证据见下方清单{reason}。")
        lines.append("")
        lines.append("### 新增文献")
        lines.append("")
        for index, record in enumerate(records, start=1):
            lines.append(f"{index}. {_entry_line(index, record)}")
    else:
        lines.append("本期没有检索到新文献。可继续关注：索引有延迟，下一期会覆盖这段时间。")
    return "\n".join(lines)


def build_bullets_prompt(records: list[LiteratureRecord]) -> str:
    """把新文献压成提示词（题名 + 年份 + 摘要截断）。"""
    payload = []
    for index, record in enumerate(records[:_MAX_PROMPT_ENTRIES], start=1):
        payload.append(
            {
                "index": index,
                "title": record.title,
                "year": _entry_year(record),
                "authors": record.authors[:3],
                "abstract": (record.abstract or "")[:600],
            }
        )
    return (
        "本期新增文献（JSON）：\n"
        + json.dumps(payload, ensure_ascii=False)
        + "\n\n请按要求输出要点。"
    )


def extract_bullets(content: str) -> str:
    """清洗模型输出：只保留列表行，剥掉它自己加的标题/参考文献段。"""
    lines = []
    for line in (content or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        if stripped.startswith(("-", "*", "•")) or re.match(r"^\d+[.、)]", stripped):
            lines.append(stripped)
    return "\n".join(lines[:8])


def audit_bullets(bullets: str, *, index_count: int) -> tuple[bool, str | None]:
    """用与正文同一把尺子校验要点。

    只校验「要点」而不是整篇摘要：清单是确定性拼装的，必然自洽；要点才是模型
    可能编造编号的地方。`index_count` 是清单条目数，编号必须落在 1..index_count。
    """
    fake_bibliography = [{"index": index} for index in range(1, index_count + 1)]
    # 给每条编号补一行占位正文，避免「清单条目未被引用」被误判成孤儿条目
    text = bullets + "\n" + "\n".join(f"[E{index}]" for index in range(1, index_count + 1))
    audit = audit_citation_alignment(text, fake_bibliography)
    return audit.ok, audit.reason


class SessionBoundSource:
    """把文献源与它依赖的数据库会话绑在一起。

    `LibraryLiteratureSource` 需要会话才能查用户的引用库；会话的生命周期必须
    覆盖整段检索。这里用「同生共死」的包装避免调用方写 try/finally：
    源没有 `aclose` 时也要把会话关掉，否则连接会漏。
    """

    def __init__(self, source: Any, session: Any) -> None:
        self._source = source
        self._session = session

    async def search(self, search: LiteratureSearch) -> list[LiteratureRecord]:
        return await self._source.search(search)

    async def aclose(self) -> None:
        closer = getattr(self._source, "aclose", None)
        if closer is not None:
            try:
                await closer()
            except Exception:
                logger.warning("refresh_source_close_failed")
        await self._session.close()


class RefreshRunner:
    """执行一次刷新：检索 → 去重 → 摘要 → 落库。

    依赖全部注入（session 工厂、文献源解析、LLM 调用），便于单测用假实现替换。
    `source_factory(task)` 返回实现了 `search(LiteratureSearch)` 的对象。
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], Any],
        source_factory: Callable[[Any], Awaitable[Any]],
        chat: Callable[..., Awaitable[Any]] | None = None,
        config: Any | None = None,
        now: Callable[[], datetime] = _now,
        retry_delay: float = SEARCH_RETRY_DELAY_SECONDS,
    ) -> None:
        self._session_factory = session_factory
        self._source_factory = source_factory
        self._config = config
        self._now = now
        self._retry_delay = retry_delay
        if chat is None:
            from app.llm.factory import get_llm_manager

            chat = get_llm_manager().chat
        self._chat = chat

    # ---- 配置 ----

    def _routine_config(self) -> Any:
        if self._config is not None:
            return self._config
        from app.core.config import get_settings

        return get_settings().routine

    # ---- 主流程 ----

    async def run(self, refresh_id: uuid.UUID) -> RefreshOutcome:
        from app.db.models.routine import TaskRefresh
        from app.db.models.task import Task

        async with self._session_factory() as session:
            refresh = await session.get(TaskRefresh, refresh_id)
            if refresh is None:
                raise LookupError(f"刷新记录不存在：{refresh_id}")
            task = await session.get(Task, refresh.task_id)
            if task is None:
                raise LookupError(f"任务不存在：{refresh.task_id}")
            cfg = self._routine_config()
            config = getattr(task, "config", None) or {}
            context = _TaskContext(
                task_id=task.id,
                user_id=task.user_id,
                title=task.title,
                queries=collect_queries(task, limit=cfg.max_queries),
                keywords=[
                    str(item).strip()
                    for item in (config.get("keywords") or [])
                    if str(item).strip()
                ],
                known=await self._known_keys(session, task),
            )
            window_from = refresh.window_from
            window_to = refresh.window_to or self._now()

        outcome = RefreshOutcome(
            refresh_id=refresh_id,
            task_id=context.task_id,
            user_id=context.user_id,
            task_title=context.title,
            status="completed",
            queries=list(context.queries),
        )
        if not context.queries:
            outcome.status = "failed"
            outcome.error = "任务没有可用的检索式（计划与配置都为空）"
            await self._finish(outcome, window_from=window_from, window_to=window_to)
            return outcome

        hits_by_query: dict[str, list[LiteratureRecord]] = {}
        errors: list[str] = []
        # 全部检索式都失败时重试一次：最常见的原因是上游限流（arXiv 429），
        # 后台任务等一会儿再来，比直接给用户留一条 failed 有用。
        for attempt in range(2):
            source = await self._source_factory(context)
            try:
                found, attempt_errors = await self._search_queries(
                    source,
                    context=context,
                    window_from=window_from,
                    window_to=window_to,
                    cfg=cfg,
                )
            finally:
                closer = getattr(source, "aclose", None)
                if closer is not None:
                    try:
                        await closer()
                    except Exception:
                        logger.warning("refresh_source_close_failed", task_id=str(context.task_id))
            errors.extend(attempt_errors)
            if found:
                hits_by_query = found
                break
            if attempt == 0:
                logger.warning(
                    "refresh_all_queries_failed_retrying",
                    task_id=str(context.task_id),
                    errors=attempt_errors[:2],
                )
                await asyncio.sleep(self._retry_delay)

        terms = relevance_terms(keywords=context.keywords, queries=context.queries)
        new_records: list[LiteratureRecord] = []
        duplicates = 0
        filtered = 0
        for query, records in hits_by_query.items():
            fresh, dup = self._split_new(records, context.known)
            duplicates += dup
            # 主题相关度过滤：窗口内的「新」不等于「相关」，跑题的不能当新证据
            relevant = [item for item in fresh if is_relevant(item, terms)]
            filtered += len(fresh) - len(relevant)
            if relevant:
                await self._record_search(context, query=query, hits=records, fresh=relevant)
            new_records.extend(relevant)

        # 上限截断：宁可少报，也不要把摘要 prompt 撑爆
        if len(new_records) > cfg.max_new_per_run:
            new_records = new_records[: cfg.max_new_per_run]

        outcome.hits_total = sum(len(items) for items in hits_by_query.values())
        outcome.duplicate_count = duplicates
        outcome.filtered_count = filtered
        outcome.new_count = len(new_records)
        outcome.new_titles = [record.title for record in new_records]

        if errors and not hits_by_query:
            outcome.status = "failed"
            outcome.error = "所有检索式都失败：" + "; ".join(errors[:3])
            await self._finish(outcome, window_from=window_from, window_to=window_to)
            return outcome

        bullets, tokens, citation_ok, fallback_reason = await self._summarize(
            new_records, has_errors=bool(errors)
        )
        outcome.tokens_used = tokens
        outcome.citation_ok = citation_ok
        outcome.summary_markdown = compose_summary(
            bullets=bullets,
            records=new_records,
            window_from=window_from,
            window_to=window_to,
            hits_total=outcome.hits_total,
            duplicate_count=duplicates,
            filtered_count=filtered,
            fallback_reason=fallback_reason,
        )
        if errors:
            outcome.error = "部分检索失败：" + "; ".join(errors[:3])
        await self._finish(outcome, window_from=window_from, window_to=window_to)
        return outcome

    async def _search_queries(
        self,
        source: Any,
        *,
        context: _TaskContext,
        window_from: datetime | None,
        window_to: datetime | None,
        cfg: Any,
    ) -> tuple[dict[str, list[LiteratureRecord]], list[str]]:
        """逐条检索式检索；单条失败只记录，不影响其余。"""
        found: dict[str, list[LiteratureRecord]] = {}
        errors: list[str] = []
        for query in context.queries:
            try:
                records = await source.search(
                    LiteratureSearch(
                        query=query,
                        max_results=cfg.max_results_per_query,
                        date_from=window_from.date() if window_from else None,
                        # 只要窗口内的新文献，但仍在窗口内按相关度排序：
                        # 纯按时间倒序会捞回一堆不相关的新论文（实测过）。
                        fresh_only=True,
                        date_to=window_to.date() if window_to else None,
                    )
                )
            except Exception as exc:  # 单个源/单条检索式失败不该拖垮整次刷新
                logger.warning(
                    "refresh_search_failed",
                    task_id=str(context.task_id),
                    query=query,
                    error=str(exc),
                )
                errors.append(f"{query}: {exc}")
                continue
            found[query] = list(records)
        return found, errors

    # ---- 去重 ----

    @staticmethod
    def _split_new(
        records: list[LiteratureRecord], known: set[str]
    ) -> tuple[list[LiteratureRecord], int]:
        """按身份键切出新增；`known` 会被就地更新（跨检索式也去重）。"""
        fresh: list[LiteratureRecord] = []
        duplicates = 0
        for record in records:
            keys = record_keys(record)
            if not keys:
                continue
            if keys & known:
                duplicates += 1
                continue
            known |= keys
            fresh.append(record)
        return fresh, duplicates

    @staticmethod
    async def _known_keys(session: Any, task: Any) -> set[str]:
        """既有身份键：筛选快照 + 参考文献表（两处都要，任一缺失都会重复推送）。"""
        from sqlalchemy import select

        from app.db.models.screening import ScreeningRecord

        keys: set[str] = set()
        result = await session.execute(
            select(ScreeningRecord.source_id, ScreeningRecord.doi, ScreeningRecord.title).where(
                ScreeningRecord.task_id == task.id
            )
        )
        for source_id, doi, title in result:
            if source_id:
                keys.add(f"sid:{str(source_id).strip().casefold()}")
            if doi:
                keys.add(f"doi:{str(doi).strip().casefold()}")
            if title:
                keys.add(f"title:{normalize_title(str(title))}")

        report = getattr(task, "review_report", None) or {}
        if isinstance(report, dict):
            for entry in report.get("bibliography") or []:
                if isinstance(entry, dict) and entry.get("title"):
                    keys.add(f"title:{normalize_title(str(entry['title']))}")
                if isinstance(entry, dict) and entry.get("doi"):
                    keys.add(f"doi:{str(entry['doi']).strip().casefold()}")
                url = str(entry.get("source_url") or "") if isinstance(entry, dict) else ""
                match = re.match(r"^https?://(?:dx\.)?doi\.org/(?P<doi>.+)$", url, re.IGNORECASE)
                if match is not None:
                    keys.add(f"doi:{match.group('doi').strip().strip('/').casefold()}")
        return keys

    # ---- 落库 ----

    async def _record_search(
        self,
        context: _TaskContext,
        *,
        query: str,
        hits: list[LiteratureRecord],
        fresh: list[LiteratureRecord],
    ) -> None:
        """把这次检索记成 PRISMA 的一条 search_log（只存新增记录）。

        `hits_total` 用上游命中数、记录只放新增的：日志读起来就是
        「命中 20、去重后 3」——既保留了真实命中量，又不会把已经筛过的文献
        每周重新塞回筛选工作台。
        """
        from app.knowledge.services.screening_service import ScreeningService, source_of

        by_source: dict[str, int] = {}
        for item in hits:
            name = source_of(item)
            by_source[name] = by_source.get(name, 0) + 1
        async with self._session_factory() as session:
            service = ScreeningService(session)
            await service.record_search(
                context.task_id,
                query=query,
                sources=sorted(by_source) or ["arxiv"],
                records=fresh,
                hits_by_source=by_source,
                hits_total=len(hits),
            )

    async def _finish(
        self,
        outcome: RefreshOutcome,
        *,
        window_from: datetime | None,
        window_to: datetime | None,
    ) -> None:
        from app.db.models.routine import TaskRefresh

        async with self._session_factory() as session:
            refresh = await session.get(TaskRefresh, outcome.refresh_id)
            if refresh is None:
                return
            refresh.status = outcome.status
            refresh.queries = list(outcome.queries)
            refresh.hits_total = outcome.hits_total
            refresh.new_count = outcome.new_count
            refresh.duplicate_count = outcome.duplicate_count
            refresh.summary_markdown = outcome.summary_markdown
            refresh.citation_ok = outcome.citation_ok
            refresh.tokens_used = outcome.tokens_used
            refresh.error = outcome.error
            refresh.window_from = window_from
            refresh.window_to = window_to
            refresh.finished_at = self._now()
            await session.commit()

    # ---- 摘要 ----

    async def _summarize(
        self, records: list[LiteratureRecord], *, has_errors: bool = False
    ) -> tuple[str | None, int, bool, str | None]:
        """生成要点；失败/越界时回落，绝不把刷新整体判失败。"""
        if not records:
            return None, 0, True, None

        tokens = 0
        last_reason = "模型未返回可用要点"
        for attempt in range(2):
            try:
                response = await self._chat(
                    [
                        Message(role=MessageRole.SYSTEM, content=SUMMARY_SYSTEM),
                        Message(role=MessageRole.USER, content=build_bullets_prompt(records)),
                    ],
                    temperature=0.2,
                    max_tokens=800,
                )
            except Exception as exc:
                logger.warning("refresh_summarize_failed", error=str(exc))
                return None, tokens, True, f"摘要模型不可用：{exc}"

            usage = getattr(response, "usage", None)
            if usage is not None:
                tokens += int(getattr(usage, "total_tokens", 0) or 0)
            bullets = extract_bullets(str(getattr(response, "content", "") or ""))
            if not bullets:
                last_reason = "模型未返回可用要点"
                continue
            ok, reason = audit_bullets(bullets, index_count=len(records))
            if ok:
                return bullets, tokens, True, None
            # 详细原因只进日志：它包含 `[E9]` 这种文件名/编号，写进摘要正文会
            # 让用户看到「模型出错了」的内部细节，而摘要本身是给人看的产出。
            logger.warning("refresh_bullets_audit_failed", reason=reason, attempt=attempt)
            last_reason = "模型给出的引用编号与清单不一致，已改为直接列出新增文献"

        return None, tokens, False, last_reason
