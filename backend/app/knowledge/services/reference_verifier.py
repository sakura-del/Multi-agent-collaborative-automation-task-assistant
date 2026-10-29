"""引用真实性核验器（Issue #3 / 计划 §2.3）。

职责：把本地参考文献候选（来自检索节点 artifacts 或 bibliography）与外部权威元数据源
（CrossRef / OpenAlex）比对，产出四态结论：

- ``VERIFIED``：DOI/标题可解析，且标题、年份、作者与外部元数据吻合；
- ``METADATA_MISMATCH``：文献存在，但标题相似度、年份或作者不符；
- ``NOT_FOUND``：DOI 无效（或不存在）且标题检索无命中；
- ``UNVERIFIED``：外部源超时 / 限流 / 不可用导致无法判定（降级态，不得据此判失败）。

设计要点：
- 外部源以 ``MetadataSource`` 协议注入：本模块不 import 具体 HTTP 客户端，
  测试可注入 fake 源并断言调用次数与并发峰值（不产生真实网络请求）；
- ``verify`` **永不向上抛网络类异常**：任一源失败都只降级为 ``UNVERIFIED``；
- 缓存 Redis 优先、内存回退，接口为 async（计划 §2.3 修订 R2，适配 ``redis.asyncio``）；
  缓存命中不得外呼，并返回 ``cached=True``；
- ``verify_many`` 按 key 去重、Semaphore 限并发、保持入参顺序。
"""

from __future__ import annotations

import asyncio
import difflib
import html
import re
import unicodedata
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from datetime import date, datetime
from typing import Any, Protocol

from pydantic import ValidationError

from app.core.exceptions import UpstreamError, ValidationException
from app.core.logging import get_logger
from app.tools.literature.models import (
    MetadataRecord,
    ReferenceCandidate,
    ReferenceVerdict,
    ReferenceVerification,
    ReferenceVerificationSummary,
)

logger = get_logger(__name__)

DEFAULT_SIMILARITY_THRESHOLD = 0.90
"""标题相似度阈值：低于该值视为「未找到」（计划 §2.3）。"""

# 缓存默认 TTL：7 天（与计划 §2.5 的 cache_ttl_seconds 默认值一致）
_DEFAULT_CACHE_TTL = 604800
# v2：载荷新增 `is_retracted` 字段，换命名空间避免旧缓存（无该字段）被当成有效命中
_CACHE_KEY_PREFIX = "refverify:v2:"

CACHE_REDIS_FAILURE_THRESHOLD = 2
"""连续 Redis 操作失败达到该次数即熔断（计划 §2.9 修订 R5②）。

Redis 不可达时每次操作要付 2.7–5s 连接超时；熔断后本进程内不再尝试 Redis，
直接走内存回退，避免每个 key 都付一次超时代价。
"""

# 契约上限：ReferenceCandidate.key / title 的最大长度
_MAX_KEY_LENGTH = 200
_MAX_TITLE_LENGTH = 500

# 年份合法区间（与 MetadataRecord.year 的字段约束一致）
_YEAR_MIN = 1000
_YEAR_MAX = 2999

_HTML_TAG_RE = re.compile(r"<[^>]*>")
_WHITESPACE_RE = re.compile(r"\s+")
_DOI_PREFIX_RE = re.compile(r"^(?:https?://)?(?:dx\.)?doi\.org/", re.IGNORECASE)
_DOI_IN_URL_RE = re.compile(r"doi\.org/(10\.[^\s?#]+)", re.IGNORECASE)
_YEAR_IN_TEXT_RE = re.compile(r"\d{4}")

# 可降级的源失败异常：网络类（超时/429/5xx/连接失败）+ 上游响应契约类。
# 说明：`UpstreamError` 是 `UpstreamTemporaryError` 的父类，一并覆盖 4xx 与格式错误，
# 保证 `verify` 在任何上游异常下都只降级、不抛出。
_SOURCE_ERRORS = (UpstreamError, ValidationException, TimeoutError, OSError)

# 会被「拦截未核验引用」开关剔除的结论
_BLOCKED_VERDICTS = frozenset({ReferenceVerdict.METADATA_MISMATCH, ReferenceVerdict.NOT_FOUND})

# DOI 核验状态（决定「DOI 无效」能否作为判定依据）
_DOI_ABSENT = "absent"
"""候选本来就没有 DOI。"""

_DOI_RESOLVED = "resolved"
"""DOI 命中外部元数据。"""

_DOI_INVALID = "invalid"
"""所有可用源都明确回答「查无此 DOI」：DOI 是权威源，可判 METADATA_MISMATCH。"""

_DOI_UNKNOWN = "unknown"
"""DOI 查询因源超时/限流/未配置而未能完成：不得据此断言 DOI 无效。"""


class MetadataSource(Protocol):
    """外部元数据源协议（CrossRef / OpenAlex 适配器均满足）。

    只声明核验器实际调用的最小接口：``search_by_title`` 不传额外关键字参数，
    由各适配器使用自身默认条数（CrossRef ``rows=3`` / OpenAlex ``per_page=3``），
    从而同时兼容两个客户端差异化的关键字签名（计划 §2.3 的 ``rows_or_per_page`` 口径）。
    """

    async def lookup_doi(self, doi: str) -> MetadataRecord | None:
        """按 DOI 查询；查无此文献返回 ``None``。"""
        ...

    async def search_by_title(self, title: str) -> list[MetadataRecord]:
        """按标题检索；无命中返回空列表（不是异常）。"""
        ...


# ===== 文本归一化与相似度 =====


def _is_ignorable_char(char: str) -> bool:
    """标点与符号（Unicode 类别 P/S，含全角标点）在标题比对中一律忽略。"""
    return unicodedata.category(char)[0] in {"P", "S"}


def normalize_title(title: str) -> str:
    """标题归一化：NFKC → 去 HTML 标签 → 小写 → 去标点（含全角）→ 折叠空白。

    中英混排必须可用：全角标点（如 ``：``、``，``、``（）``）直接删除而非替换为空格，
    避免中文标题被无意义地插入空格而拉低相似度。
    """
    if not title:
        return ""
    text = unicodedata.normalize("NFKC", html.unescape(title))
    text = _HTML_TAG_RE.sub(" ", text)
    text = text.lower()
    text = "".join(char for char in text if not _is_ignorable_char(char))
    return _WHITESPACE_RE.sub(" ", text).strip()


def title_similarity(left: str, right: str) -> float:
    """标题相似度（0.0–1.0），基于 ``difflib.SequenceMatcher``。"""
    normalized_left = normalize_title(left)
    normalized_right = normalize_title(right)
    if not normalized_left or not normalized_right:
        return 0.0
    if normalized_left == normalized_right:
        return 1.0
    return difflib.SequenceMatcher(None, normalized_left, normalized_right).ratio()


def _normalize_doi(doi: str | None) -> str | None:
    """归一化 DOI：小写、去掉 ``https://doi.org/`` 前缀、去首尾空白。"""
    if not doi:
        return None
    text = str(doi).strip()
    if not text:
        return None
    normalized = _DOI_PREFIX_RE.sub("", text).strip().lower()
    return normalized or None


def _doi_from_url(url: Any) -> str | None:
    """从 ``https://doi.org/10.xxxx/yyy`` 形式的链接里提取归一化 DOI。"""
    if not url:
        return None
    match = _DOI_IN_URL_RE.search(str(url))
    if match is None:
        return None
    return _normalize_doi(match.group(1))


def _author_tokens(authors: Sequence[str]) -> set[str]:
    """作者姓名 token 集合：按空白切分，兼容 ``"Zhang, Wei"`` 与 ``"Wei Zhang"``。

    额外加入「无空格整名」token，使中文姓名（``张三``）与连写姓名也可比对。
    """
    tokens: set[str] = set()
    for author in authors:
        if not author:
            continue
        normalized = normalize_title(str(author))
        if not normalized:
            continue
        tokens.add(normalized)
        tokens.update(part for part in normalized.split(" ") if part)
        tokens.add(normalized.replace(" ", ""))
    return tokens


def _coerce_year(value: Any) -> int | None:
    """把 int / 字符串年份收敛到合理区间；非法值返回 ``None``。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if _YEAR_MIN <= value <= _YEAR_MAX else None
    if isinstance(value, str):
        match = _YEAR_IN_TEXT_RE.search(value)
        if match is None:
            return None
        year = int(match.group(0))
        return year if _YEAR_MIN <= year <= _YEAR_MAX else None
    return None


def _year_from_published(value: Any) -> int | None:
    """从 ``published_at``（datetime 或 ISO 字符串）取年份。"""
    if value is None:
        return None
    if isinstance(value, datetime | date):
        return value.year
    return _coerce_year(value)


def _format_seconds(value: float) -> str:
    """秒数格式化：整秒保留一位小数（20.0），不足 1 秒用紧凑写法（0.05）。"""
    if value >= 1:
        return f"{value:.1f}"
    return f"{value:g}"


# ===== 候选构造 =====


def _authors_from_entry(value: Any) -> list[str]:
    """把 artifacts / bibliography 里的 authors 字段收敛为字符串列表。"""
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, Sequence):
        return [str(item) for item in value if str(item).strip()]
    return []


def _candidate_key(doi: str | None, title: str) -> str:
    """稳定标识：有 DOI 用归一化 DOI，否则用标题归一化值（超长截断到契约上限）。"""
    if doi:
        return doi[:_MAX_KEY_LENGTH]
    normalized = normalize_title(title) or title.strip()
    return normalized[:_MAX_KEY_LENGTH]


def _candidate_from_entry(entry: Any) -> ReferenceCandidate | None:
    """单条记录 → 候选；缺标题等无法构造时返回 ``None``（跳过而不中断整批）。"""
    if not isinstance(entry, dict):
        return None
    title = entry.get("title")
    if not isinstance(title, str) or not title.strip():
        return None

    doi = _normalize_doi(entry.get("doi")) or _doi_from_url(entry.get("source_url"))
    year = _coerce_year(entry.get("year"))
    if year is None:
        year = _year_from_published(entry.get("published_at"))
    source_url = entry.get("source_url")
    try:
        return ReferenceCandidate(
            key=_candidate_key(doi, title),
            title=title.strip()[:_MAX_TITLE_LENGTH],
            authors=_authors_from_entry(entry.get("authors")),
            year=year,
            doi=doi,
            source_url=str(source_url) if source_url else None,
        )
    except ValidationError as exc:
        logger.warning("reference_candidate_invalid", title=title[:80], error=str(exc))
        return None


def _candidates_from_entries(entries: Sequence[dict[str, Any]]) -> list[ReferenceCandidate]:
    candidates: list[ReferenceCandidate] = []
    for entry in entries:
        candidate = _candidate_from_entry(entry)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def candidates_from_artifacts(artifacts: Sequence[dict[str, Any]]) -> list[ReferenceCandidate]:
    """从检索节点 artifacts（``LiteratureRecord.model_dump()``）构造候选。

    标题缺失的条目直接跳过；年份取 ``year`` 或从 ``published_at`` 解析；
    DOI 取 ``doi`` 字段，缺失时尝试从 ``source_url`` 中的 doi.org 链接提取。
    """
    return _candidates_from_entries(artifacts)


def candidates_from_bibliography(entries: Sequence[dict[str, Any]]) -> list[ReferenceCandidate]:
    """从 bibliography 条目构造候选；有 ``doi`` 时 key 用归一化 DOI。"""
    return _candidates_from_entries(entries)


# ===== 汇总与拦截键 =====


def blocked_keys(verifications: Sequence[ReferenceVerification]) -> set[str]:
    """``verdict ∈ {METADATA_MISMATCH, NOT_FOUND}`` 的 ``reference_key``。"""
    return {item.reference_key for item in verifications if item.verdict in _BLOCKED_VERDICTS}


def summarize_verifications(
    verifications: Sequence[ReferenceVerification], *, dropped: int = 0, intercepted: bool = False
) -> ReferenceVerificationSummary:
    """核验统计汇总：供校验报告与导出报告展示。"""
    counts = Counter(item.verdict for item in verifications)
    return ReferenceVerificationSummary(
        total=len(verifications),
        verified=counts[ReferenceVerdict.VERIFIED],
        metadata_mismatch=counts[ReferenceVerdict.METADATA_MISMATCH],
        not_found=counts[ReferenceVerdict.NOT_FOUND],
        unverified=counts[ReferenceVerdict.UNVERIFIED],
        cached_hits=sum(1 for item in verifications if item.cached),
        dropped=dropped,
        intercepted=intercepted,
    )


# ===== 缓存 =====


class ReferenceVerificationCache:
    """Redis 优先、内存回退；键为 ``refverify:v2:{reference_key}``。

    接口为 **async**（计划 §2.3 修订 R2）：项目 Redis 只有 ``redis.asyncio`` 客户端
    （``app/core/redis.py::RedisManager.get_client()``），同步接口无法消费 awaitable，
    会让「Redis 优先」名存实亡；核验器本身是 async，await 无额外成本。
    写路径沿用项目既有写法 ``await client.setex(key, ttl, value)``。

    **连续失败熔断**（计划 §2.9 修订 R5②）：Redis 不可达时每次操作都要付连接超时
    （实测 2.7–5s）。连续失败达到 :data:`CACHE_REDIS_FAILURE_THRESHOLD` 次后，本实例
    不再尝试 Redis（行为与「没有 Redis」完全一致，直接走内存）；中途成功一次即清零。
    """

    def __init__(self, *, redis_client: Any | None = None, ttl: int = _DEFAULT_CACHE_TTL) -> None:
        self._redis = redis_client
        self._ttl = ttl
        # 内存回退缓存：未注入 Redis 或 Redis 异常/熔断时使用，保证核验缓存始终可用
        self._memory: dict[str, str] = {}
        # 连续失败计数与熔断标志（R5②）
        self._redis_consecutive_failures = 0
        self._redis_circuit_open = False

    @property
    def redis_circuit_open(self) -> bool:
        """Redis 是否已熔断（熔断后本实例不再触碰 Redis，行为等价于纯内存）。"""
        return self._redis_circuit_open

    @staticmethod
    def _cache_key(key: str) -> str:
        return f"{_CACHE_KEY_PREFIX}{key}"

    def _use_redis(self) -> bool:
        """是否还应该尝试 Redis；熔断后恒为 False。"""
        return self._redis is not None and not self._redis_circuit_open

    def _record_redis_failure(self, event: str, exc: Exception, cache_key: str) -> None:
        """记一次 Redis 失败；连续失败达阈值即熔断并告警一次。"""
        self._redis_consecutive_failures += 1
        logger.warning(
            event,
            key=cache_key,
            error=str(exc),
            consecutive_failures=self._redis_consecutive_failures,
        )
        if (
            self._redis_consecutive_failures >= CACHE_REDIS_FAILURE_THRESHOLD
            and not self._redis_circuit_open
        ):
            self._redis_circuit_open = True
            logger.warning(
                "reference_cache_circuit_open",
                consecutive_failures=self._redis_consecutive_failures,
                threshold=CACHE_REDIS_FAILURE_THRESHOLD,
            )

    async def get(self, key: str) -> ReferenceVerification | None:
        """查询缓存；未命中返回 ``None``。Redis 异常或已熔断时回退内存。"""
        cache_key = self._cache_key(key)
        if self._use_redis():
            # `_use_redis()` 是独立方法，mypy 不做跨方法收窄，这里显式断言收窄类型
            assert self._redis is not None
            try:
                raw = await self._redis.get(cache_key)
            except Exception as exc:
                self._record_redis_failure("reference_cache_get_failed", exc, cache_key)
            else:
                self._redis_consecutive_failures = 0  # 成功一次即清零（「连续」语义）
                if raw is not None:
                    return self._deserialize(raw)
        raw = self._memory.get(cache_key)
        if raw is None:
            return None
        return self._deserialize(raw)

    async def set(self, key: str, verification: ReferenceVerification) -> None:
        """写入缓存（带 TTL）；Redis 异常或已熔断时只写内存回退。"""
        cache_key = self._cache_key(key)
        raw = verification.model_dump_json()
        if self._use_redis():
            # 同上：显式收窄 `self._redis` 供 mypy 使用
            assert self._redis is not None
            try:
                await self._redis.setex(cache_key, self._ttl, raw)
            except Exception as exc:
                self._record_redis_failure("reference_cache_set_failed", exc, cache_key)
            else:
                self._redis_consecutive_failures = 0  # 成功一次即清零（「连续」语义）
                return
        self._memory[cache_key] = raw

    @staticmethod
    def _deserialize(raw: Any) -> ReferenceVerification | None:
        """反序列化缓存载荷；载荷损坏时按未命中处理（不阻断核验）。"""
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            return ReferenceVerification.model_validate_json(raw)
        except Exception as exc:
            logger.warning("reference_cache_payload_invalid", error=str(exc))
            return None


# ===== 核验器 =====


class ReferenceVerifier:
    """引用真实性核验器：计划 §2.3 判定算法 1–5 步的唯一权威实现。"""

    def __init__(
        self,
        *,
        crossref: MetadataSource | None = None,
        openalex: MetadataSource | None = None,
        cache: ReferenceVerificationCache | None = None,
        similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
        year_tolerance: int = 1,
        max_concurrency: int = 4,
        intercept_unverified: bool = False,
        total_timeout: float | None = None,
    ) -> None:
        self._crossref = crossref
        self._openalex = openalex
        # 未注入缓存时使用内存缓存，保证默认构造也满足「缓存命中不外呼」
        self._cache = cache if cache is not None else ReferenceVerificationCache()
        self._similarity_threshold = similarity_threshold
        self._year_tolerance = max(0, year_tolerance)
        self._max_concurrency = max(1, max_concurrency)
        self._intercept_unverified = intercept_unverified
        # 整批核验总预算（秒，计划 §2.9 修订 R4）；None 表示不设预算
        self._total_timeout = total_timeout

    @property
    def intercept_unverified(self) -> bool:
        """是否拦截未通过核验的参考文献（接线方据此剔除条目并计 ``dropped``）。"""
        return self._intercept_unverified

    @intercept_unverified.setter
    def intercept_unverified(self, value: bool) -> None:
        self._intercept_unverified = bool(value)

    @property
    def _sources(self) -> list[tuple[str, MetadataSource]]:
        """按优先级排列的可用源：CrossRef 优先，OpenAlex 兜底。"""
        sources: list[tuple[str, MetadataSource]] = []
        if self._crossref is not None:
            sources.append(("crossref", self._crossref))
        if self._openalex is not None:
            sources.append(("openalex", self._openalex))
        return sources

    async def verify(self, candidate: ReferenceCandidate) -> ReferenceVerification:
        """核验单条候选；永不向上抛网络类异常（失败降级为 ``UNVERIFIED``）。

        判定顺序（计划 §2.3，含修订 R1）：
        1. 缓存命中直接返回（``cached=True``），不外呼；
        2. 有 DOI：CrossRef → OpenAlex 依次查 DOI，命中即比对；
        3. DOI 全部落空（或本来无 DOI）：CrossRef → OpenAlex 标题检索，取相似度最高者；
        4. 判定映射以 **DOI 是否解析成功** 为分支：
           - DOI 解析成功且标题/年份/作者全通过 → ``VERIFIED``；
           - DOI 解析成功但标题相似度不足、或年份/作者不符 → ``METADATA_MISMATCH``
             （文献确实存在，只是本地记录有偏差）；
           - 无 DOI / DOI 落空：标题达标且年份作者通过 → ``VERIFIED``；
             标题达标但年份或作者不符 → ``METADATA_MISMATCH``；
             DOI 落空且所有源都明确返回 ``None`` → ``METADATA_MISMATCH``（reason 说明 DOI 无效）；
             标题无命中或相似度不足 → ``NOT_FOUND``；
        5. 所有可用源均失败 → ``UNVERIFIED``。
        """
        cached = await self._cache.get(candidate.key)
        if cached is not None:
            return cached.model_copy(update={"cached": True})

        sources = self._sources
        failures: list[str] = []
        answered = 0
        doi = _normalize_doi(candidate.doi)

        doi_status = _DOI_ABSENT
        if doi:
            # 先按「所有可用源都明确否定」处理；任一源失败即降级为 unknown
            doi_status = _DOI_INVALID if sources else _DOI_UNKNOWN
            for name, source in sources:
                try:
                    record = await source.lookup_doi(doi)
                except _SOURCE_ERRORS as exc:
                    doi_status = _DOI_UNKNOWN
                    failures.append(f"{name} DOI 查询失败（{exc}）")
                    continue
                answered += 1
                if record is not None:
                    verification = self._judge(candidate, record, doi=doi, doi_status=_DOI_RESOLVED)
                    await self._store(candidate.key, verification)
                    return verification

        best: MetadataRecord | None = None
        best_similarity = -1.0
        for name, source in sources:
            try:
                records = await source.search_by_title(candidate.title)
            except _SOURCE_ERRORS as exc:
                failures.append(f"{name} 标题检索失败（{exc}）")
                continue
            answered += 1
            for record in records:
                similarity = title_similarity(candidate.title, record.title)
                if similarity > best_similarity:
                    best, best_similarity = record, similarity

        if best is not None:
            verification = self._judge(
                candidate, best, doi=doi, doi_status=doi_status, similarity=best_similarity
            )
        elif answered == 0:
            verification = ReferenceVerification(
                reference_key=candidate.key,
                title=candidate.title,
                verdict=ReferenceVerdict.UNVERIFIED,
                reason=self._degraded_reason(failures),
            )
        else:
            verification = ReferenceVerification(
                reference_key=candidate.key,
                title=candidate.title,
                verdict=ReferenceVerdict.NOT_FOUND,
                reason=self._not_found_reason(doi, doi_status, failures),
            )

        await self._store(candidate.key, verification)
        return verification

    async def verify_many(
        self, candidates: Sequence[ReferenceCandidate]
    ) -> list[ReferenceVerification]:
        """去重（按 key）→ 缓存查询 → 有界并发（Semaphore）→ 保持入参顺序。

        设置 ``total_timeout`` 时整批核验受**整体预算**约束（计划 §2.9 修订 R4）：
        超预算不抛异常，已完成的候选保留原结论，未完成的按入参顺序补 ``UNVERIFIED``。
        ``total_timeout=None`` 时行为与不设预算完全一致。
        """
        if not candidates:
            return []

        unique: dict[str, ReferenceCandidate] = {}
        for candidate in candidates:
            unique.setdefault(candidate.key, candidate)

        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def _verify_one(candidate: ReferenceCandidate) -> ReferenceVerification:
            async with semaphore:
                return await self.verify(candidate)

        if self._total_timeout is None:
            results = await asyncio.gather(*(_verify_one(item) for item in unique.values()))
            by_key = dict(zip(unique.keys(), results, strict=True))
        else:
            by_key = await self._verify_with_budget(unique, _verify_one)

        # 重复 key 复用同一结论对象，同时严格保持入参顺序；
        # 预算超时未完成的候选不在映射里 → 补 UNVERIFIED（降级态，绝不写缓存）
        ordered: list[ReferenceVerification] = []
        for candidate in candidates:
            verification = by_key.get(candidate.key)
            ordered.append(
                verification if verification is not None else self._budget_exceeded(candidate)
            )
        return ordered

    async def _verify_with_budget(
        self,
        unique: dict[str, ReferenceCandidate],
        verify_one: Callable[[ReferenceCandidate], Awaitable[ReferenceVerification]],
    ) -> dict[str, ReferenceVerification]:
        """整批核验受 ``total_timeout`` 约束；未在预算内完成的候选不进入返回映射。

        预算到期后取消在飞任务并收敛取消（避免悬挂任务与未取回的异常）；
        任务自身抛出的意外异常同样按「未完成」处理，保证 ``verify_many`` 不外抛。
        """
        tasks = [asyncio.ensure_future(verify_one(item)) for item in unique.values()]
        done, pending = await asyncio.wait(tasks, timeout=self._total_timeout)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        by_key: dict[str, ReferenceVerification] = {}
        for key, task in zip(unique.keys(), tasks, strict=True):
            if task not in done or task.cancelled():
                continue
            error = task.exception()
            if error is not None:
                logger.warning("reference_verify_task_failed", key=key, error=str(error))
                continue
            by_key[key] = task.result()
        return by_key

    def _budget_exceeded(self, candidate: ReferenceCandidate) -> ReferenceVerification:
        """超预算未完成的候选：补 ``UNVERIFIED``（reason 说明预算秒数）。"""
        budget = self._total_timeout if self._total_timeout is not None else 0.0
        return ReferenceVerification(
            reference_key=candidate.key,
            title=candidate.title,
            verdict=ReferenceVerdict.UNVERIFIED,
            reason=f"核验超预算（{_format_seconds(budget)}s）",
        )

    # ===== 内部：判定与写缓存 =====

    async def _store(self, key: str, verification: ReferenceVerification) -> None:
        """写缓存；``UNVERIFIED`` 属降级态，不写缓存以免把临时故障固化 7 天。"""
        if verification.verdict is ReferenceVerdict.UNVERIFIED:
            return
        await self._cache.set(key, verification)

    def _judge(
        self,
        candidate: ReferenceCandidate,
        record: MetadataRecord,
        *,
        doi: str | None,
        doi_status: str,
        similarity: float | None = None,
    ) -> ReferenceVerification:
        """按「标题 → 年份 → 作者」顺序比对，给出结论与人类可读理由。"""
        score = (
            title_similarity(candidate.title, record.title) if similarity is None else similarity
        )
        doi_note = self._doi_note(doi, doi_status)

        if score < self._similarity_threshold:
            if doi_status == _DOI_RESOLVED:
                # 修订 R1：DOI 已解析成功 ⇒ 文献确实存在，标题不符属「元数据不符」而非「未找到」
                return ReferenceVerification(
                    reference_key=candidate.key,
                    title=candidate.title,
                    verdict=ReferenceVerdict.METADATA_MISMATCH,
                    reason=(
                        f"DOI {doi} 解析成功，但标题相似度 {score:.2f} "
                        f"低于阈值 {self._similarity_threshold:.2f}"
                    ),
                    similarity=score,
                    matched=record,
                )
            reason = (
                f"标题相似度 {score:.2f} 低于阈值 {self._similarity_threshold:.2f}"
                f"（最佳候选来自 {record.source}）"
            )
            if doi_note:
                reason = f"{doi_note}；{reason}"
            return ReferenceVerification(
                reference_key=candidate.key,
                title=candidate.title,
                verdict=ReferenceVerdict.NOT_FOUND,
                reason=reason,
                similarity=score,
                matched=record,
            )

        problems = [
            problem
            for problem in (
                self._year_problem(candidate, record),
                self._author_problem(candidate, record),
            )
            if problem
        ]
        if problems:
            reason = f"标题相似度 {score:.2f} 达标，但" + "；".join(problems)
            if doi_note:
                reason = f"{doi_note}；{reason}"
            verdict = ReferenceVerdict.METADATA_MISMATCH
        elif doi_status == _DOI_INVALID:
            # DOI 是权威源：所有源都明确查无此 DOI，而标题命中且吻合 → 元数据不符
            verdict = ReferenceVerdict.METADATA_MISMATCH
            reason = (
                f"{doi_note}，但标题检索命中 {record.source} 且元数据吻合（相似度 {score:.2f}）"
            )
        else:
            verdict = ReferenceVerdict.VERIFIED
            if doi_status == _DOI_RESOLVED:
                reason = f"DOI {doi} 命中 {record.source}，标题相似度 {score:.2f}，年份/作者吻合"
            elif doi_status == _DOI_UNKNOWN:
                reason = (
                    f"标题检索命中 {record.source} 且元数据吻合（相似度 {score:.2f}）；"
                    f"DOI 未能核验（元数据源不可用）"
                )
            else:
                reason = f"标题检索命中 {record.source} 且元数据吻合（相似度 {score:.2f}）"

        return ReferenceVerification(
            reference_key=candidate.key,
            title=candidate.title,
            verdict=verdict,
            reason=reason,
            similarity=score,
            is_retracted=record.is_retracted,
            matched=record,
        )

    @staticmethod
    def _doi_note(doi: str | None, doi_status: str) -> str:
        """DOI 路径的问题说明；DOI 有效或无 DOI 时为空。"""
        if not doi:
            return ""
        if doi_status == _DOI_INVALID:
            return f"DOI {doi} 无效（未解析到元数据）"
        if doi_status == _DOI_UNKNOWN:
            return f"DOI {doi} 未能核验（元数据源不可用）"
        return ""

    def _year_problem(self, candidate: ReferenceCandidate, record: MetadataRecord) -> str:
        """年份偏差超容差时返回问题描述；任一侧缺失则跳过检查。"""
        if candidate.year is None or record.year is None:
            return ""
        delta = abs(candidate.year - record.year)
        if delta <= self._year_tolerance:
            return ""
        return (
            f"年份偏差 {delta} 年超过容差 {self._year_tolerance}"
            f"（本地 {candidate.year} vs 外部 {record.year}）"
        )

    def _author_problem(self, candidate: ReferenceCandidate, record: MetadataRecord) -> str:
        """双方作者非空且 token 无交集时返回问题描述；任一侧为空则跳过检查。"""
        local = _author_tokens(candidate.authors)
        external = _author_tokens(record.authors)
        if not local or not external:
            return ""
        if local & external:
            return ""
        return f"作者无交集（本地 {list(candidate.authors)} vs 外部 {list(record.authors)}）"

    def _not_found_reason(self, doi: str | None, doi_status: str, failures: Sequence[str]) -> str:
        if doi and doi_status == _DOI_INVALID:
            reason = f"DOI {doi} 无效（未解析到元数据），标题检索亦无命中"
        elif doi and doi_status == _DOI_UNKNOWN:
            reason = f"DOI {doi} 未能核验（元数据源不可用），标题检索亦无命中"
        else:
            reason = f"标题检索无命中（相似度均低于阈值 {self._similarity_threshold:.2f}）"
        if failures:
            reason = f"{reason}；部分源不可用：{'；'.join(failures)}"
        return reason

    @staticmethod
    def _degraded_reason(failures: Sequence[str]) -> str:
        if not failures:
            return "未配置可用的元数据源，无法判定（降级为未核验）"
        return f"所有可用元数据源均失败（降级为未核验）：{'；'.join(failures)}"
