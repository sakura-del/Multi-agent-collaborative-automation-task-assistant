"""T2 核验器测试：三态判定 + 降级 + 缓存 + 有界并发（计划 §2.3）。

铁律（本文件必须遵守）：
- **不产生任何真实网络请求**：所有 ``MetadataSource`` 都是 fake，逐个记录调用次数、
  查询参数与并发峰值；也不 import ``crossref.py`` / ``openalex.py``（T1 的文件）。
- 判定语义以计划 §2.3 的 1–5 步为唯一权威口径。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar, Literal

import fakeredis.aioredis
import pytest

from app.core.exceptions import UpstreamError, UpstreamTemporaryError, ValidationException
from app.knowledge.services import reference_verifier as verifier_module
from app.knowledge.services.reference_verifier import (
    CACHE_REDIS_FAILURE_THRESHOLD,
    DEFAULT_SIMILARITY_THRESHOLD,
    MetadataSource,
    ReferenceVerificationCache,
    ReferenceVerifier,
    blocked_keys,
    candidates_from_artifacts,
    candidates_from_bibliography,
    normalize_title,
    summarize_verifications,
    title_similarity,
)
from app.tools.literature.models import (
    MetadataRecord,
    ReferenceCandidate,
    ReferenceVerdict,
    ReferenceVerification,
)

# fakeredis 2.37 自身给 setex 加了弃用提示；redis-py 与项目 app/core/redis.py 均在用 setex
pytestmark = pytest.mark.filterwarnings("ignore:Call to deprecated setex:DeprecationWarning")

# ===== 测试替身 =====


class FakeSource:
    """记录调用次数 / 查询参数 / 并发峰值的 fake 元数据源。"""

    def __init__(
        self,
        *,
        source: Literal["crossref", "openalex"] = "crossref",
        doi_result: MetadataRecord | None = None,
        title_results: Sequence[MetadataRecord] = (),
        title_echo: bool = False,
        doi_error: Exception | None = None,
        title_error: Exception | None = None,
        delay: float = 0.0,
        delay_by_title: Mapping[str, float] | None = None,
    ) -> None:
        self.source = source
        self.doi_calls = 0
        self.title_calls = 0
        self.doi_queries: list[str] = []
        self.title_queries: list[str] = []
        self.in_flight = 0
        self.peak_in_flight = 0
        self._doi_result = doi_result
        self._title_results = list(title_results)
        self._title_echo = title_echo
        self._doi_error = doi_error
        self._title_error = title_error
        self._delay = delay
        self._delay_by_title = dict(delay_by_title or {})

    @property
    def calls(self) -> int:
        """外呼总次数（DOI + 标题）。"""
        return self.doi_calls + self.title_calls

    async def _enter(self, delay: float | None = None) -> None:
        self.in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        actual = self._delay if delay is None else delay
        if actual:
            await asyncio.sleep(actual)

    async def lookup_doi(self, doi: str) -> MetadataRecord | None:
        self.doi_calls += 1
        self.doi_queries.append(doi)
        await self._enter()
        try:
            if self._doi_error is not None:
                raise self._doi_error
            return self._doi_result
        finally:
            self.in_flight -= 1

    async def search_by_title(self, title: str) -> list[MetadataRecord]:
        self.title_calls += 1
        self.title_queries.append(title)
        await self._enter(self._delay_by_title.get(title))
        try:
            if self._title_error is not None:
                raise self._title_error
            if self._title_echo:
                return [make_record(source=self.source, doi=None, title=title)]
            return list(self._title_results)
        finally:
            self.in_flight -= 1


class FakeAsyncRedis:
    """异步 Redis 替身：记录 setex 调用（与 app/core/redis.py 的 async setex 写法一致）。"""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.setex_calls: list[tuple[str, int, str]] = []

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.setex_calls.append((key, ttl, value))
        self.store[key] = value


class BrokenRedis:
    """读写都抛异常、并记录真实被触碰次数的 Redis 替身（用于熔断断言）。"""

    def __init__(self) -> None:
        self.calls = 0

    async def get(self, key: str) -> str | None:
        self.calls += 1
        raise RuntimeError("redis down")

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.calls += 1
        raise RuntimeError("redis down")


class ToggleRedis:
    """可切换「故障 / 正常」的异步 Redis 替身（用于验证连续失败计数清零）。"""

    def __init__(self, *, failing: bool = True) -> None:
        self.failing = failing
        self.calls = 0
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        self.calls += 1
        if self.failing:
            raise RuntimeError("redis down")
        return self.store.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.calls += 1
        if self.failing:
            raise RuntimeError("redis down")
        self.store[key] = value


class CorruptRedis:
    """返回非法 JSON 的 Redis 替身。"""

    async def get(self, key: str) -> str:
        return "{不是合法 JSON"

    async def setex(self, key: str, ttl: int, value: str) -> None:
        return None


def make_record(
    *,
    source: Literal["crossref", "openalex"] = "crossref",
    doi: str | None = "10.1234/abc",
    title: str = "Deep Learning for Graphs",
    authors: Sequence[str] = ("Wei Zhang",),
    year: int | None = 2024,
) -> MetadataRecord:
    """构造外部元数据记录。"""
    return MetadataRecord(
        source=source,
        doi=doi,
        title=title,
        authors=list(authors),
        year=year,
    )


def make_candidate(
    *,
    key: str = "10.1234/abc",
    title: str = "Deep Learning for Graphs",
    authors: Sequence[str] = ("Wei Zhang",),
    year: int | None = 2024,
    doi: str | None = "10.1234/abc",
    source_url: str | None = None,
) -> ReferenceCandidate:
    """构造待核验候选。"""
    return ReferenceCandidate(
        key=key,
        title=title,
        authors=list(authors),
        year=year,
        doi=doi,
        source_url=source_url,
    )


def make_verification(
    key: str, verdict: ReferenceVerdict, *, cached: bool = False
) -> ReferenceVerification:
    """构造核验结论（用于汇总函数测试）。"""
    return ReferenceVerification(
        reference_key=key, title=f"标题 {key}", verdict=verdict, cached=cached
    )


# ===== 标题归一化与相似度 =====


class TestNormalizeTitle:
    """NFKC → 去 HTML 标签 → 小写 → 去标点（含全角）→ 折叠空白。"""

    def test_removes_html_tags_and_lowercases(self) -> None:
        assert normalize_title("<b>Attention Is All You Need</b>") == "attention is all you need"

    def test_collapses_whitespace(self) -> None:
        assert normalize_title("  Deep\t\nLearning   for  Graphs ") == "deep learning for graphs"

    def test_drops_ascii_and_fullwidth_punctuation(self) -> None:
        assert normalize_title("深度学习：综述（2024）") == "深度学习综述2024"
        assert normalize_title("Graphs, Networks; and More!") == "graphs networks and more"

    def test_nfkc_normalizes_fullwidth_letters_and_digits(self) -> None:
        assert normalize_title("ＡＢＣ１２３") == "abc123"

    def test_chinese_english_mixed_title_is_usable(self) -> None:
        assert normalize_title("图神经网络（GNN）：综述") == "图神经网络gnn综述"

    def test_empty_title_returns_empty_string(self) -> None:
        assert normalize_title("<i></i>") == ""


class TestTitleSimilarity:
    """0.0–1.0，基于 difflib.SequenceMatcher。"""

    def test_identical_after_normalization(self) -> None:
        assert (
            title_similarity("<i>Attention, Is All You Need</i>", "attention is all you need")
            == 1.0
        )

    def test_chinese_punctuation_difference_is_ignored(self) -> None:
        assert title_similarity("深度学习综述", "深度学习，综述") == 1.0

    def test_unrelated_titles_below_default_threshold(self) -> None:
        similarity = title_similarity("深度学习综述", "图神经网络在推荐系统中的应用")
        assert similarity < DEFAULT_SIMILARITY_THRESHOLD

    def test_empty_side_returns_zero(self) -> None:
        assert title_similarity("", "深度学习综述") == 0.0
        assert title_similarity("深度学习综述", "<b></b>") == 0.0

    def test_similar_typo_title_lands_between_zero_and_one(self) -> None:
        similarity = title_similarity(
            "Deep Learning for Graphs", "Deep Learning for Graph Networks"
        )
        assert 0.0 < similarity < 1.0


# ===== 候选构造 =====


class TestCandidatesFromArtifacts:
    """LiteratureRecord dump（无 doi）→ 候选。"""

    ARTIFACT: ClassVar[dict[str, Any]] = {
        "source_id": "2401.00001",
        "title": "Deep Learning for Graphs",
        "authors": ["Wei Zhang", "Li Wang"],
        "abstract": "摘要",
        "published_at": "2024-01-05T00:00:00Z",
        "source_url": "https://arxiv.org/abs/2401.00001",
        "pdf_url": "https://arxiv.org/pdf/2401.00001",
        "categories": ["cs.LG"],
    }

    def test_builds_candidate_from_literature_record_dump(self) -> None:
        candidates = candidates_from_artifacts([self.ARTIFACT])
        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate.title == "Deep Learning for Graphs"
        assert candidate.authors == ["Wei Zhang", "Li Wang"]
        assert candidate.year == 2024
        assert candidate.doi is None
        assert candidate.source_url == "https://arxiv.org/abs/2401.00001"
        # 无 DOI 时 key 用标题归一化值
        assert candidate.key == normalize_title("Deep Learning for Graphs")

    def test_year_from_datetime_object(self) -> None:
        from datetime import UTC, datetime

        artifact = dict(self.ARTIFACT)
        artifact["published_at"] = datetime(2023, 7, 1, tzinfo=UTC)
        assert candidates_from_artifacts([artifact])[0].year == 2023

    def test_unparsable_published_at_leaves_year_none(self) -> None:
        artifact = dict(self.ARTIFACT)
        artifact["published_at"] = "不是日期"
        assert candidates_from_artifacts([artifact])[0].year is None

    def test_doi_derived_from_doi_org_source_url(self) -> None:
        artifact = {
            "title": "Attention Is All You Need",
            "authors": [],
            "source_url": "https://doi.org/10.5555/ABC123",
        }
        candidate = candidates_from_artifacts([artifact])[0]
        assert candidate.doi == "10.5555/abc123"
        assert candidate.key == "10.5555/abc123"

    def test_explicit_doi_wins_and_key_uses_normalized_doi(self) -> None:
        artifact = dict(self.ARTIFACT)
        artifact["doi"] = "https://doi.org/10.1234/XYZ"
        candidate = candidates_from_artifacts([artifact])[0]
        assert candidate.doi == "10.1234/xyz"
        assert candidate.key == "10.1234/xyz"

    def test_entries_without_title_are_skipped(self) -> None:
        assert candidates_from_artifacts([{"title": ""}, {"published_at": "2024-01-01"}]) == []

    def test_overlong_title_key_truncated_to_contract_limit(self) -> None:
        artifact = {"title": "深度学习" * 100, "authors": []}
        candidate = candidates_from_artifacts([artifact])[0]
        assert len(candidate.key) == 200

    def test_empty_input_returns_empty_list(self) -> None:
        assert candidates_from_artifacts([]) == []


class TestCandidatesFromBibliography:
    """bibliography 条目（含 doi 字段）→ 候选。"""

    def test_doi_entry_key_uses_normalized_doi(self) -> None:
        entry = {
            "index": 1,
            "title": "Attention Is All You Need",
            "source_url": "https://doi.org/10.5555/3295222",
            "doi": "https://doi.org/10.5555/3295222",
            "authors": ["Ashish Vaswani"],
            "year": 2017,
        }
        candidates = candidates_from_bibliography([entry])
        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate.key == "10.5555/3295222"
        assert candidate.doi == "10.5555/3295222"
        assert candidate.authors == ["Ashish Vaswani"]
        assert candidate.year == 2017

    def test_entry_without_doi_falls_back_to_title_key(self) -> None:
        entry = {"index": 2, "title": "深度学习综述", "source_url": "https://example.org/a"}
        candidate = candidates_from_bibliography([entry])[0]
        assert candidate.doi is None
        assert candidate.key == normalize_title("深度学习综述")

    def test_doi_derived_from_source_url_when_doi_field_missing(self) -> None:
        entry = {"title": "Some Paper", "source_url": "https://doi.org/10.1000/XYZ"}
        candidate = candidates_from_bibliography([entry])[0]
        assert candidate.doi == "10.1000/xyz"
        assert candidate.key == "10.1000/xyz"

    def test_entries_without_title_are_skipped(self) -> None:
        assert candidates_from_bibliography([{"index": 1, "source_url": "https://x.org"}]) == []

    def test_empty_input_returns_empty_list(self) -> None:
        assert candidates_from_bibliography([]) == []


# ===== 汇总与拦截键 =====


class TestBlockedKeysAndSummary:
    """blocked_keys / summarize_verifications。"""

    def test_blocked_keys_only_mismatch_and_not_found(self) -> None:
        verifications = [
            make_verification("a", ReferenceVerdict.VERIFIED),
            make_verification("b", ReferenceVerdict.METADATA_MISMATCH),
            make_verification("c", ReferenceVerdict.NOT_FOUND),
            make_verification("d", ReferenceVerdict.UNVERIFIED),
        ]
        assert blocked_keys(verifications) == {"b", "c"}

    def test_blocked_keys_empty_input(self) -> None:
        assert blocked_keys([]) == set()

    def test_summary_counts_each_verdict(self) -> None:
        verifications = [
            make_verification("a", ReferenceVerdict.VERIFIED, cached=True),
            make_verification("b", ReferenceVerdict.METADATA_MISMATCH),
            make_verification("c", ReferenceVerdict.NOT_FOUND),
            make_verification("d", ReferenceVerdict.UNVERIFIED),
        ]
        summary = summarize_verifications(verifications)
        assert summary.total == 4
        assert summary.verified == 1
        assert summary.metadata_mismatch == 1
        assert summary.not_found == 1
        assert summary.unverified == 1
        assert summary.cached_hits == 1

    def test_summary_passes_through_dropped_and_intercepted(self) -> None:
        summary = summarize_verifications(
            [make_verification("a", ReferenceVerdict.VERIFIED)], dropped=2, intercepted=True
        )
        assert summary.dropped == 2
        assert summary.intercepted is True

    def test_summary_of_empty_sequence(self) -> None:
        summary = summarize_verifications([])
        assert summary.total == 0
        assert summary.verified == 0
        assert summary.intercepted is False


# ===== 缓存组件 =====


class TestReferenceVerificationCache:
    """Redis 优先、内存回退；键为 `refverify:v2:{reference_key}`；接口为 async（修订 R2）。"""

    async def test_memory_fallback_round_trip(self) -> None:
        cache = ReferenceVerificationCache()
        verification = make_verification("10.1/x", ReferenceVerdict.VERIFIED)
        await cache.set("10.1/x", verification)
        cached = await cache.get("10.1/x")
        assert cached is not None
        assert cached.reference_key == "10.1/x"
        assert cached.verdict is ReferenceVerdict.VERIFIED

    async def test_get_miss_returns_none(self) -> None:
        assert await ReferenceVerificationCache().get("未写入") is None

    async def test_redis_is_preferred_and_key_is_prefixed(self) -> None:
        redis = FakeAsyncRedis()
        cache = ReferenceVerificationCache(redis_client=redis, ttl=120)
        await cache.set("10.1/x", make_verification("10.1/x", ReferenceVerdict.NOT_FOUND))
        assert redis.setex_calls[0][0] == "refverify:v2:10.1/x"
        assert redis.setex_calls[0][1] == 120
        cached = await cache.get("10.1/x")
        assert cached is not None
        assert cached.verdict is ReferenceVerdict.NOT_FOUND

    async def test_default_ttl_is_seven_days(self) -> None:
        redis = FakeAsyncRedis()
        await ReferenceVerificationCache(redis_client=redis).set(
            "k", make_verification("k", ReferenceVerdict.VERIFIED)
        )
        assert redis.setex_calls[0][1] == 604800

    async def test_broken_redis_falls_back_to_memory_without_raising(self) -> None:
        cache = ReferenceVerificationCache(redis_client=BrokenRedis())
        assert await cache.get("k") is None  # 不抛出
        await cache.set("k", make_verification("k", ReferenceVerdict.VERIFIED))  # 不抛出
        cached = await cache.get("k")
        assert cached is not None
        assert cached.verdict is ReferenceVerdict.VERIFIED

    async def test_corrupt_payload_returns_none(self) -> None:
        cache = ReferenceVerificationCache(redis_client=CorruptRedis())
        assert await cache.get("k") is None

    async def test_fakeredis_async_client_is_really_used(self) -> None:
        """注入 redis.asyncio 客户端就是真正可用路径（修订 R2）：写入真落在 Redis 里。"""
        client = fakeredis.aioredis.FakeRedis(decode_responses=True)
        cache = ReferenceVerificationCache(redis_client=client)
        await cache.set("k", make_verification("k", ReferenceVerdict.VERIFIED))
        assert await client.get("refverify:v2:k") is not None
        cached = await cache.get("k")
        assert cached is not None
        assert cached.verdict is ReferenceVerdict.VERIFIED


class TestCacheCircuitBreaker:
    """R5②：连续失败熔断——Redis 不可达时不能在每次操作上重复付连接超时代价。"""

    def test_failure_threshold_constant_is_exposed(self) -> None:
        assert isinstance(CACHE_REDIS_FAILURE_THRESHOLD, int)
        assert CACHE_REDIS_FAILURE_THRESHOLD >= 2

    async def test_stops_touching_redis_after_consecutive_failures(self) -> None:
        """连续失败达阈值后第 3 次操作不再触碰 Redis，且内存命中正常。"""
        redis = BrokenRedis()
        cache = ReferenceVerificationCache(redis_client=redis)

        for _ in range(CACHE_REDIS_FAILURE_THRESHOLD):
            assert await cache.get("k") is None  # 未写入 → 未命中
        assert redis.calls == CACHE_REDIS_FAILURE_THRESHOLD
        assert cache.redis_circuit_open is True

        # 熔断后：不再触碰 Redis，set 写内存、get 从内存命中
        await cache.set("k", make_verification("k", ReferenceVerdict.VERIFIED))
        cached = await cache.get("k")
        assert redis.calls == CACHE_REDIS_FAILURE_THRESHOLD
        assert cached is not None
        assert cached.verdict is ReferenceVerdict.VERIFIED
        assert cached.reference_key == "k"

    async def test_circuit_open_returns_value_written_after_trip(self) -> None:
        """熔断前后链路正确：熔断期间 set 写入内存，随后 get 命中同一值。"""
        redis = BrokenRedis()
        cache = ReferenceVerificationCache(redis_client=redis)
        # 先触发熔断
        for _ in range(CACHE_REDIS_FAILURE_THRESHOLD):
            await cache.get("预热")
        await cache.set("10.1/y", make_verification("10.1/y", ReferenceVerdict.NOT_FOUND))
        calls_after_trip = redis.calls

        cached = await cache.get("10.1/y")

        assert cached is not None
        assert cached.verdict is ReferenceVerdict.NOT_FOUND
        assert redis.calls == calls_after_trip  # get 未触碰 Redis

    async def test_circuit_open_behaves_like_no_redis_client(self) -> None:
        """熔断后行为等价于「没有 Redis」：同样的写入/读取结果。"""
        tripped = ReferenceVerificationCache(redis_client=BrokenRedis())
        plain = ReferenceVerificationCache()
        for _ in range(CACHE_REDIS_FAILURE_THRESHOLD):
            await tripped.get("预热")
        assert tripped.redis_circuit_open is True
        assert plain.redis_circuit_open is False

        for cache in (tripped, plain):
            await cache.set("k", make_verification("k", ReferenceVerdict.METADATA_MISMATCH))
            cached = await cache.get("k")
            assert cached is not None
            assert cached.verdict is ReferenceVerdict.METADATA_MISMATCH
            assert await cache.get("缺失键") is None

    async def test_success_resets_consecutive_failure_count(self) -> None:
        """按「连续」语义：中间成功一次即清零，后续需重新累计到阈值才熔断。"""
        redis = ToggleRedis(failing=True)
        cache = ReferenceVerificationCache(redis_client=redis)

        await cache.get("k1")  # 失败 1
        redis.failing = False
        await cache.get("k2")  # 成功 → 计数清零
        redis.failing = True
        await cache.get("k3")  # 失败 1（若未清零，此处就是第 2 次 → 熔断）
        assert cache.redis_circuit_open is False

        await cache.get("k4")  # 失败 2 → 熔断
        assert cache.redis_circuit_open is True

    async def test_circuit_open_logs_warning_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        records: list[tuple[str, dict[str, Any]]] = []

        class _Recorder:
            def warning(self, event: str, **kwargs: Any) -> None:
                records.append((event, kwargs))

        monkeypatch.setattr(verifier_module, "logger", _Recorder())
        cache = ReferenceVerificationCache(redis_client=BrokenRedis())

        for _ in range(CACHE_REDIS_FAILURE_THRESHOLD + 2):
            await cache.get("k")

        events = [event for event, _ in records]
        assert "reference_cache_circuit_open" in events
        assert events.count("reference_cache_circuit_open") == 1  # 只告警一次
        opened = next(
            payload for event, payload in records if event == "reference_cache_circuit_open"
        )
        assert opened["threshold"] == CACHE_REDIS_FAILURE_THRESHOLD

    async def test_no_redis_client_is_unaffected(self) -> None:
        """redis_client=None 行为不变：纯内存，且永不进入熔断态。"""
        cache = ReferenceVerificationCache()
        await cache.set("k", make_verification("k", ReferenceVerdict.VERIFIED))
        cached = await cache.get("k")
        assert cached is not None
        assert cached.verdict is ReferenceVerdict.VERIFIED
        assert cache.redis_circuit_open is False


# ===== verify：判定算法 =====


class TestVerifyByDoi:
    """有 DOI：crossref.lookup_doi → openalex.lookup_doi，DOI 是权威源。"""

    async def test_doi_hit_with_matching_metadata_is_verified(self) -> None:
        crossref = FakeSource(doi_result=make_record())
        openalex = FakeSource(source="openalex", doi_result=make_record(source="openalex"))
        verifier = ReferenceVerifier(crossref=crossref, openalex=openalex)

        verification = await verifier.verify(make_candidate())

        assert verification.verdict is ReferenceVerdict.VERIFIED
        assert verification.cached is False
        assert verification.matched is not None
        assert verification.similarity == pytest.approx(1.0)
        assert crossref.doi_calls == 1
        assert crossref.title_calls == 0  # DOI 命中即判定，不再标题检索
        assert openalex.calls == 0

    async def test_doi_lookup_normalizes_doi_before_call(self) -> None:
        crossref = FakeSource(doi_result=make_record())
        verifier = ReferenceVerifier(crossref=crossref)
        candidate = make_candidate(
            key="10.1234/abc", doi="https://doi.org/10.1234/ABC", title="Deep Learning for Graphs"
        )

        verification = await verifier.verify(candidate)

        assert verification.verdict is ReferenceVerdict.VERIFIED
        assert crossref.doi_queries == ["10.1234/abc"]

    async def test_crossref_miss_falls_through_to_openalex(self) -> None:
        crossref = FakeSource(doi_result=None)
        openalex = FakeSource(source="openalex", doi_result=make_record(source="openalex"))
        verifier = ReferenceVerifier(crossref=crossref, openalex=openalex)

        verification = await verifier.verify(make_candidate())

        assert verification.verdict is ReferenceVerdict.VERIFIED
        assert crossref.doi_calls == 1
        assert openalex.doi_calls == 1
        assert verification.matched is not None
        assert verification.matched.source == "openalex"

    async def test_year_beyond_tolerance_is_metadata_mismatch(self) -> None:
        crossref = FakeSource(doi_result=make_record(year=2024))
        verifier = ReferenceVerifier(crossref=crossref, year_tolerance=1)

        verification = await verifier.verify(make_candidate(year=2020))

        assert verification.verdict is ReferenceVerdict.METADATA_MISMATCH
        assert "年份" in verification.reason
        assert verification.similarity == pytest.approx(1.0)

    async def test_year_within_tolerance_is_verified(self) -> None:
        crossref = FakeSource(doi_result=make_record(year=2024))
        verifier = ReferenceVerifier(crossref=crossref, year_tolerance=1)
        verification = await verifier.verify(make_candidate(year=2023))
        assert verification.verdict is ReferenceVerdict.VERIFIED

    async def test_year_check_skipped_when_either_side_missing(self) -> None:
        crossref = FakeSource(doi_result=make_record(year=None))
        verifier = ReferenceVerifier(crossref=crossref)
        assert (await verifier.verify(make_candidate(year=1999))).verdict is (
            ReferenceVerdict.VERIFIED
        )

    async def test_author_tokens_disjoint_is_metadata_mismatch(self) -> None:
        crossref = FakeSource(doi_result=make_record(authors=["Zhang, Wei"]))
        verifier = ReferenceVerifier(crossref=crossref)

        verification = await verifier.verify(make_candidate(authors=["Alice Brown"]))

        assert verification.verdict is ReferenceVerdict.METADATA_MISMATCH
        assert "作者" in verification.reason

    async def test_author_check_accepts_comma_and_space_name_orders(self) -> None:
        crossref = FakeSource(doi_result=make_record(authors=["Zhang, Wei", "Li Wang"]))
        verifier = ReferenceVerifier(crossref=crossref)
        verification = await verifier.verify(make_candidate(authors=["Wei Zhang", "Wang, Li"]))
        assert verification.verdict is ReferenceVerdict.VERIFIED

    async def test_author_check_skipped_when_candidate_authors_empty(self) -> None:
        crossref = FakeSource(doi_result=make_record(authors=["Zhang, Wei"]))
        verifier = ReferenceVerifier(crossref=crossref)
        assert (await verifier.verify(make_candidate(authors=[]))).verdict is (
            ReferenceVerdict.VERIFIED
        )

    async def test_author_check_skipped_when_record_authors_empty(self) -> None:
        crossref = FakeSource(doi_result=make_record(authors=[]))
        verifier = ReferenceVerifier(crossref=crossref)
        assert (await verifier.verify(make_candidate(authors=["Alice Brown"]))).verdict is (
            ReferenceVerdict.VERIFIED
        )

    async def test_doi_miss_and_no_title_hit_is_not_found(self) -> None:
        crossref = FakeSource(doi_result=None, title_results=[])
        openalex = FakeSource(source="openalex", doi_result=None, title_results=[])
        verifier = ReferenceVerifier(crossref=crossref, openalex=openalex)

        verification = await verifier.verify(make_candidate())

        assert verification.verdict is ReferenceVerdict.NOT_FOUND
        assert verification.matched is None
        assert verification.similarity == 0.0
        assert crossref.title_calls == 1
        assert openalex.title_calls == 1

    async def test_doi_miss_but_title_hit_and_match_is_mismatch_with_doi_reason(self) -> None:
        """DOI 是权威源：DOI 落空而标题命中且吻合 → METADATA_MISMATCH（reason 说明 DOI 无效）。"""
        crossref = FakeSource(
            doi_result=None, title_results=[make_record(title="Deep Learning for Graphs")]
        )
        openalex = FakeSource(source="openalex", doi_result=None, title_results=[])
        verifier = ReferenceVerifier(crossref=crossref, openalex=openalex)

        verification = await verifier.verify(make_candidate())

        assert verification.verdict is ReferenceVerdict.METADATA_MISMATCH
        assert "DOI" in verification.reason
        assert verification.similarity == pytest.approx(1.0)
        assert verification.matched is not None

    async def test_doi_miss_with_low_similarity_title_hit_is_not_found(self) -> None:
        crossref = FakeSource(
            doi_result=None, title_results=[make_record(title="完全无关的主题研究")]
        )
        verifier = ReferenceVerifier(crossref=crossref)

        verification = await verifier.verify(make_candidate())

        assert verification.verdict is ReferenceVerdict.NOT_FOUND
        assert "DOI" in verification.reason
        assert verification.similarity < DEFAULT_SIMILARITY_THRESHOLD


class TestDoiResolvedTitleMapping:
    """修订 R1：DOI 解析成功时标题不符 → METADATA_MISMATCH（不得判 NOT_FOUND）。

    依据 Issue #3 原文表格「元数据不符 = DOI 存在，但标题/作者/年份偏差超阈值」
    与验收标准「故意改错标题 → 判为元数据不符」。
    """

    async def test_resolved_doi_with_wrong_title_is_metadata_mismatch(self) -> None:
        """真 DOI 命中 + 标题被改错 → 元数据不符（reason 说明标题问题）。"""
        crossref = FakeSource(doi_result=make_record(title="Deep Learning for Graphs"))
        verifier = ReferenceVerifier(crossref=crossref)

        verification = await verifier.verify(
            make_candidate(title="Quantum Computing for Beginners")
        )

        assert verification.verdict is ReferenceVerdict.METADATA_MISMATCH
        assert "标题" in verification.reason
        assert verification.similarity < DEFAULT_SIMILARITY_THRESHOLD
        assert verification.matched is not None
        assert crossref.title_calls == 0  # DOI 已解析成功，无需标题检索

    async def test_wrong_title_with_matching_year_and_authors_is_still_mismatch(self) -> None:
        """年份/作者都对，仅标题被改错：仍是元数据不符（证明结论来自标题分支）。"""
        crossref = FakeSource(
            doi_result=make_record(
                title="Graph Neural Networks in Practice", authors=["Zhang, Wei"], year=2024
            )
        )
        verifier = ReferenceVerifier(crossref=crossref)

        verification = await verifier.verify(
            make_candidate(title="Completely Different Topic", authors=["Wei Zhang"], year=2024)
        )

        assert verification.verdict is ReferenceVerdict.METADATA_MISMATCH
        assert "标题" in verification.reason
        assert "年份" not in verification.reason
        assert "作者" not in verification.reason

    async def test_no_doi_with_low_similarity_title_is_still_not_found(self) -> None:
        """无 DOI：标题不达标仍是 NOT_FOUND（保持原语义）。"""
        crossref = FakeSource(title_results=[make_record(doi=None, title="完全无关的主题研究")])
        verifier = ReferenceVerifier(crossref=crossref)

        verification = await verifier.verify(make_candidate(doi=None))

        assert verification.verdict is ReferenceVerdict.NOT_FOUND

    async def test_doi_miss_with_low_similarity_title_is_still_not_found(self) -> None:
        """DOI 落空：标题不达标仍是 NOT_FOUND（DOI 未解析成功）。"""
        crossref = FakeSource(
            doi_result=None, title_results=[make_record(title="完全无关的主题研究")]
        )
        openalex = FakeSource(source="openalex", doi_result=None, title_results=[])
        verifier = ReferenceVerifier(crossref=crossref, openalex=openalex)

        verification = await verifier.verify(make_candidate())

        assert verification.verdict is ReferenceVerdict.NOT_FOUND
        assert "DOI" in verification.reason

    async def test_resolved_doi_with_matching_title_is_verified(self) -> None:
        """回归：DOI 命中且标题吻合 → VERIFIED。"""
        crossref = FakeSource(doi_result=make_record())
        verifier = ReferenceVerifier(crossref=crossref)

        verification = await verifier.verify(make_candidate())

        assert verification.verdict is ReferenceVerdict.VERIFIED
        assert verification.similarity == pytest.approx(1.0)


class TestVerifyByTitle:
    """无 DOI（或 DOI 全部落空）：标题检索取相似度最高者。"""

    async def test_title_hit_with_matching_metadata_is_verified(self) -> None:
        crossref = FakeSource(title_results=[])
        openalex = FakeSource(
            source="openalex", title_results=[make_record(source="openalex", doi=None)]
        )
        verifier = ReferenceVerifier(crossref=crossref, openalex=openalex)

        verification = await verifier.verify(make_candidate(doi=None))

        assert verification.verdict is ReferenceVerdict.VERIFIED
        assert verification.matched is not None
        assert verification.matched.source == "openalex"
        assert "openalex" in verification.reason

    async def test_best_similarity_record_wins(self) -> None:
        crossref = FakeSource(
            title_results=[
                make_record(title="Graphene Oxide Membranes for Water Treatment", year=2019),
                make_record(title="Deep Learning for Graphs", year=2024),
            ]
        )
        verifier = ReferenceVerifier(crossref=crossref)

        verification = await verifier.verify(make_candidate(doi=None))

        assert verification.verdict is ReferenceVerdict.VERIFIED
        assert verification.similarity == pytest.approx(1.0)
        assert verification.matched is not None
        assert verification.matched.title == "Deep Learning for Graphs"

    async def test_title_hit_below_threshold_is_not_found(self) -> None:
        crossref = FakeSource(title_results=[make_record(title="完全无关的主题研究")])
        verifier = ReferenceVerifier(crossref=crossref)

        verification = await verifier.verify(make_candidate(doi=None))

        assert verification.verdict is ReferenceVerdict.NOT_FOUND
        assert "相似度" in verification.reason

    async def test_custom_similarity_threshold_is_honoured(self) -> None:
        candidate = make_candidate(
            key="标题键", doi=None, title="Deep Learning for Graphs", year=None, authors=[]
        )
        record = make_record(doi=None, title="Deep Learning for Graph Networks", year=None)
        similarity = title_similarity(candidate.title, record.title)
        assert 0.5 < similarity < DEFAULT_SIMILARITY_THRESHOLD

        strict = ReferenceVerifier(crossref=FakeSource(title_results=[record]))
        loose = ReferenceVerifier(
            crossref=FakeSource(title_results=[record]), similarity_threshold=0.5
        )

        assert (await strict.verify(candidate)).verdict is ReferenceVerdict.NOT_FOUND
        assert (await loose.verify(candidate)).verdict is ReferenceVerdict.VERIFIED

    async def test_no_sources_configured_is_unverified(self) -> None:
        verifier = ReferenceVerifier()
        verification = await verifier.verify(make_candidate(doi=None))
        assert verification.verdict is ReferenceVerdict.UNVERIFIED
        assert verification.reason != ""


# ===== verify：降级语义 =====


class TestDegradation:
    """任一源抛 UpstreamTemporaryError：继续尝试其余源；全部失败 → UNVERIFIED。"""

    async def test_one_source_down_other_source_verifies(self) -> None:
        crossref = FakeSource(
            doi_error=UpstreamTemporaryError("crossref 超时"),
            title_error=UpstreamTemporaryError("crossref 超时"),
        )
        openalex = FakeSource(source="openalex", doi_result=make_record(source="openalex"))
        verifier = ReferenceVerifier(crossref=crossref, openalex=openalex)

        verification = await verifier.verify(make_candidate())

        assert verification.verdict is ReferenceVerdict.VERIFIED
        assert verification.cached is False
        assert crossref.doi_calls == 1
        assert openalex.doi_calls == 1

    async def test_doi_path_down_but_title_path_verifies(self) -> None:
        """DOI 查询因源故障失败 ≠ DOI 无效：标题命中且吻合仍判 VERIFIED。"""
        crossref = FakeSource(
            doi_error=UpstreamTemporaryError("crossref 429"),
            title_results=[make_record(doi=None)],
        )
        verifier = ReferenceVerifier(crossref=crossref)

        verification = await verifier.verify(make_candidate())

        assert verification.verdict is ReferenceVerdict.VERIFIED
        assert crossref.title_calls == 1
        assert "未能核验" in verification.reason

    async def test_partial_doi_failure_is_not_treated_as_invalid_doi(self) -> None:
        """一个源否定、另一个源故障 → DOI 状态未知，不得判 METADATA_MISMATCH。"""
        crossref = FakeSource(doi_result=None, title_results=[make_record(doi=None)])
        openalex = FakeSource(source="openalex", doi_error=UpstreamTemporaryError("openalex 503"))
        verifier = ReferenceVerifier(crossref=crossref, openalex=openalex)

        verification = await verifier.verify(make_candidate())

        assert verification.verdict is ReferenceVerdict.VERIFIED
        assert "未能核验" in verification.reason

    async def test_not_found_reason_distinguishes_unverifiable_doi(self) -> None:
        crossref = FakeSource(doi_error=UpstreamTemporaryError("crossref 超时"), title_results=[])
        verifier = ReferenceVerifier(crossref=crossref)

        verification = await verifier.verify(make_candidate())

        assert verification.verdict is ReferenceVerdict.NOT_FOUND
        assert "未能核验" in verification.reason

    async def test_all_sources_temporary_failure_is_unverified(self) -> None:
        error = UpstreamTemporaryError("上游 503")
        crossref = FakeSource(doi_error=error, title_error=error)
        openalex = FakeSource(source="openalex", doi_error=error, title_error=error)
        verifier = ReferenceVerifier(crossref=crossref, openalex=openalex)

        verification = await verifier.verify(make_candidate())  # 不得抛异常

        assert verification.verdict is ReferenceVerdict.UNVERIFIED
        assert verification.matched is None
        assert verification.reason != ""
        assert crossref.calls == 2
        assert openalex.calls == 2

    async def test_permanent_upstream_error_degrades_to_unverified(self) -> None:
        error = UpstreamError("上游 400")
        crossref = FakeSource(doi_error=error, title_error=error)
        verifier = ReferenceVerifier(crossref=crossref)

        verification = await verifier.verify(make_candidate())

        assert verification.verdict is ReferenceVerdict.UNVERIFIED

    async def test_invalid_upstream_payload_degrades_to_unverified(self) -> None:
        error = ValidationException("响应缺字段")
        crossref = FakeSource(doi_error=error, title_error=error)
        verifier = ReferenceVerifier(crossref=crossref)

        verification = await verifier.verify(make_candidate())

        assert verification.verdict is ReferenceVerdict.UNVERIFIED

    async def test_timeout_error_degrades_to_unverified(self) -> None:
        crossref = FakeSource(doi_error=TimeoutError("超时"), title_error=TimeoutError("超时"))
        verifier = ReferenceVerifier(crossref=crossref)

        verification = await verifier.verify(make_candidate())

        assert verification.verdict is ReferenceVerdict.UNVERIFIED

    async def test_one_source_answered_negative_is_not_unverified(self) -> None:
        """至少一个源给出了明确否定 → 判 NOT_FOUND，而不是降级。"""
        crossref = FakeSource(doi_result=None, title_results=[])
        openalex = FakeSource(
            source="openalex",
            doi_error=UpstreamTemporaryError("openalex 超时"),
            title_error=UpstreamTemporaryError("openalex 超时"),
        )
        verifier = ReferenceVerifier(crossref=crossref, openalex=openalex)

        verification = await verifier.verify(make_candidate())

        assert verification.verdict is ReferenceVerdict.NOT_FOUND

    async def test_unverified_is_not_cached(self) -> None:
        error = UpstreamTemporaryError("上游超时")
        crossref = FakeSource(doi_error=error, title_error=error)
        verifier = ReferenceVerifier(crossref=crossref)

        first = await verifier.verify(make_candidate())
        second = await verifier.verify(make_candidate())

        assert first.verdict is ReferenceVerdict.UNVERIFIED
        assert second.verdict is ReferenceVerdict.UNVERIFIED
        assert second.cached is False
        assert crossref.calls == 4  # 降级态不写缓存，第二次仍会重试外呼


# ===== 撤稿状态传播与 v2 缓存命名空间 =====


async def test_verified_result_preserves_retraction_flag() -> None:
    record = make_record(source="openalex").model_copy(update={"is_retracted": True})
    verifier = ReferenceVerifier(openalex=FakeSource(doi_result=record))

    result = await verifier.verify(make_candidate())

    assert result.verdict is ReferenceVerdict.VERIFIED
    assert result.is_retracted is True


async def test_cache_uses_v2_namespace_and_preserves_unknown_retraction() -> None:
    redis = FakeAsyncRedis()
    cache = ReferenceVerificationCache(redis_client=redis)
    verification = make_verification("k", ReferenceVerdict.VERIFIED).model_copy(
        update={"is_retracted": None}
    )

    await cache.set("k", verification)
    cached = await cache.get("k")

    assert "refverify:v2:k" in redis.store
    assert "refverify:k" not in redis.store
    assert cached is not None
    assert cached.is_retracted is None


async def test_cached_verdict_keeps_retracted_true() -> None:
    """已撤稿结论进缓存再读出仍为 True（Task 3 的徽标依赖该值，不能被丢成 None/False）。"""
    redis = FakeAsyncRedis()
    cache = ReferenceVerificationCache(redis_client=redis)
    verifier = ReferenceVerifier(
        openalex=FakeSource(
            doi_result=make_record(source="openalex").model_copy(update={"is_retracted": True})
        ),
        cache=cache,
    )

    first = await verifier.verify(make_candidate())
    second = await verifier.verify(make_candidate())

    assert first.is_retracted is True
    assert second.cached is True
    assert second.is_retracted is True


async def test_doi_confirmed_title_mismatch_keeps_retraction_flag() -> None:
    """DOI 已确认文献存在但标题不符时，撤稿状态仍须保留（与四态正交）。"""
    record = make_record(source="openalex").model_copy(update={"is_retracted": True})
    verifier = ReferenceVerifier(openalex=FakeSource(doi_result=record))

    result = await verifier.verify(make_candidate(title="完全不相关的标题"))

    assert result.verdict is ReferenceVerdict.METADATA_MISMATCH
    assert result.is_retracted is True


# ===== 缓存命中不重复外呼 =====


class TestVerifierCacheIntegration:
    """缓存命中不得外呼，返回 cached=True。"""

    async def test_second_verify_hits_cache_without_external_call(self) -> None:
        crossref = FakeSource(doi_result=make_record())
        verifier = ReferenceVerifier(crossref=crossref)

        first = await verifier.verify(make_candidate())
        calls_after_first = crossref.calls
        second = await verifier.verify(make_candidate())

        assert first.cached is False
        assert first.verdict is ReferenceVerdict.VERIFIED
        assert second.cached is True
        assert second.verdict is ReferenceVerdict.VERIFIED
        assert crossref.calls == calls_after_first == 1

    async def test_injected_async_cache_is_used(self) -> None:
        redis = FakeAsyncRedis()
        cache = ReferenceVerificationCache(redis_client=redis, ttl=60)
        crossref = FakeSource(doi_result=make_record())
        verifier = ReferenceVerifier(crossref=crossref, cache=cache)

        await verifier.verify(make_candidate())
        second = await verifier.verify(make_candidate())

        assert second.cached is True
        assert crossref.calls == 1
        assert redis.setex_calls[0][0] == "refverify:v2:10.1234/abc"

    async def test_second_verify_hits_fakeredis_without_external_call(self) -> None:
        """注入 fakeredis.aioredis：第二次 verify 零外呼且 cached=True（Redis 路径真生效）。"""
        client = fakeredis.aioredis.FakeRedis(decode_responses=True)
        cache = ReferenceVerificationCache(redis_client=client, ttl=60)
        crossref = FakeSource(doi_result=make_record())
        verifier = ReferenceVerifier(crossref=crossref, cache=cache)

        first = await verifier.verify(make_candidate())
        second = await verifier.verify(make_candidate())

        assert first.cached is False
        assert second.cached is True
        assert crossref.calls == 1
        assert await client.get("refverify:v2:10.1234/abc") is not None

    async def test_different_keys_do_not_collide(self) -> None:
        crossref = FakeSource(doi_result=make_record())
        verifier = ReferenceVerifier(crossref=crossref)

        first = await verifier.verify(
            make_candidate(key="10.1/a", doi="10.1/a", title="Deep Learning for Graphs")
        )
        second = await verifier.verify(
            make_candidate(key="10.1/b", doi="10.1/b", title="Deep Learning for Graphs")
        )

        assert first.cached is False
        assert second.cached is False
        assert crossref.calls == 2

    async def test_not_found_verdict_is_cached(self) -> None:
        crossref = FakeSource(doi_result=None, title_results=[])
        verifier = ReferenceVerifier(crossref=crossref)

        first = await verifier.verify(make_candidate())
        second = await verifier.verify(make_candidate())

        assert first.verdict is ReferenceVerdict.NOT_FOUND
        assert second.cached is True
        assert crossref.calls == 2


# ===== verify_many：去重 / 顺序 / 有界并发 =====


class TestVerifyMany:
    """去重（按 key）→ 缓存查询 → 有界并发（Semaphore）→ 保持入参顺序。"""

    async def test_empty_input(self) -> None:
        assert await ReferenceVerifier().verify_many([]) == []

    async def test_deduplicates_by_key_and_keeps_input_order(self) -> None:
        crossref = FakeSource(doi_result=make_record())
        verifier = ReferenceVerifier(crossref=crossref)
        candidates = [
            make_candidate(key="10.1/a", doi="10.1/a"),
            make_candidate(key="10.1/b", doi="10.1/b"),
            make_candidate(key="10.1/a", doi="10.1/a"),
        ]

        results = await verifier.verify_many(candidates)

        assert [item.reference_key for item in results] == ["10.1/a", "10.1/b", "10.1/a"]
        assert crossref.calls == 2  # 相同 key 只外呼一次
        assert results[0] == results[2]

    async def test_preserves_order_of_distinct_candidates(self) -> None:
        crossref = FakeSource(title_echo=True)
        verifier = ReferenceVerifier(crossref=crossref)
        candidates = [
            make_candidate(key=f"标题-{index}", doi=None, title=f"论文标题 {index}")
            for index in range(6)
        ]

        results = await verifier.verify_many(candidates)

        assert [item.reference_key for item in results] == [item.key for item in candidates]

    async def test_concurrency_is_bounded_by_max_concurrency(self) -> None:
        crossref = FakeSource(title_echo=True, delay=0.02)
        verifier = ReferenceVerifier(crossref=crossref, max_concurrency=2)
        candidates = [
            make_candidate(key=f"标题-{index}", doi=None, title=f"论文标题 {index}")
            for index in range(6)
        ]

        results = await verifier.verify_many(candidates)

        assert all(item.verdict is ReferenceVerdict.VERIFIED for item in results)
        assert crossref.peak_in_flight <= 2
        assert crossref.peak_in_flight == 2  # 并发确实被用满但不超限

    async def test_max_concurrency_one_runs_serially(self) -> None:
        crossref = FakeSource(title_echo=True, delay=0.01)
        verifier = ReferenceVerifier(crossref=crossref, max_concurrency=1)
        candidates = [
            make_candidate(key=f"标题-{index}", doi=None, title=f"论文标题 {index}")
            for index in range(4)
        ]

        await verifier.verify_many(candidates)

        assert crossref.peak_in_flight == 1

    async def test_non_positive_max_concurrency_still_runs(self) -> None:
        crossref = FakeSource(title_echo=True)
        verifier = ReferenceVerifier(crossref=crossref, max_concurrency=0)
        candidates = [
            make_candidate(key=f"标题-{index}", doi=None, title=f"论文标题 {index}")
            for index in range(3)
        ]

        results = await verifier.verify_many(candidates)

        assert len(results) == 3
        assert crossref.peak_in_flight == 1

    async def test_all_sources_down_returns_unverified_for_every_candidate(self) -> None:
        error = UpstreamTemporaryError("上游超时")
        crossref = FakeSource(doi_error=error, title_error=error)
        verifier = ReferenceVerifier(crossref=crossref)
        candidates = [
            make_candidate(key=f"10.1/{index}", doi=f"10.1/{index}") for index in range(3)
        ]

        results = await verifier.verify_many(candidates)  # 不得抛异常

        assert len(results) == 3
        assert all(item.verdict is ReferenceVerdict.UNVERIFIED for item in results)

    async def test_verify_many_stops_touching_broken_redis_after_circuit_opens(self) -> None:
        """R5② 性能回归：Redis 不可达时整批核验只付阈值次数的超时代价。"""
        redis = BrokenRedis()
        cache = ReferenceVerificationCache(redis_client=redis)
        verifier = ReferenceVerifier(crossref=FakeSource(doi_result=make_record()), cache=cache)
        candidates = [
            make_candidate(key=f"10.1/{index}", doi=f"10.1/{index}") for index in range(5)
        ]

        results = await verifier.verify_many(candidates)

        assert len(results) == 5
        assert all(item.verdict is ReferenceVerdict.VERIFIED for item in results)
        # 第 1 个候选：get 失败 1 次 + set 失败 1 次 → 熔断；其余 4 个候选不再触碰 Redis
        assert redis.calls == CACHE_REDIS_FAILURE_THRESHOLD


class TestVerifyManyTotalBudget:
    """R4：整批核验总预算 ``total_timeout``——超预算不抛异常，未完成项补 UNVERIFIED。"""

    async def test_none_budget_keeps_current_behaviour(self) -> None:
        """total_timeout=None 时行为与现在完全一致（慢源也会跑完）。"""
        crossref = FakeSource(title_echo=True, delay=0.05)
        verifier = ReferenceVerifier(crossref=crossref, max_concurrency=2, total_timeout=None)
        candidates = [
            make_candidate(key=f"标题-{index}", doi=None, title=f"论文标题 {index}")
            for index in range(3)
        ]

        results = await verifier.verify_many(candidates)

        assert [item.verdict for item in results] == [ReferenceVerdict.VERIFIED] * 3

    async def test_unfinished_candidates_become_unverified_in_input_order(self) -> None:
        """超预算：已完成项保留原 verdict，未完成项按入参顺序补 UNVERIFIED，全程不抛异常。"""
        crossref = FakeSource(title_echo=True, delay_by_title={"慢 b": 5.0})
        cache = ReferenceVerificationCache()
        verifier = ReferenceVerifier(
            crossref=crossref, cache=cache, max_concurrency=4, total_timeout=0.2
        )
        candidates = [
            make_candidate(key="快-a", doi=None, title="快 a"),
            make_candidate(key="慢-b", doi=None, title="慢 b"),
            make_candidate(key="快-c", doi=None, title="快 c"),
        ]

        started = time.monotonic()
        results = await verifier.verify_many(candidates)  # 不得抛异常
        elapsed = time.monotonic() - started

        assert [item.reference_key for item in results] == ["快-a", "慢-b", "快-c"]
        assert results[0].verdict is ReferenceVerdict.VERIFIED
        assert results[2].verdict is ReferenceVerdict.VERIFIED
        assert results[1].verdict is ReferenceVerdict.UNVERIFIED
        assert results[1].reason == "核验超预算（0.2s）"
        assert results[1].title == "慢 b"  # 用候选自身的身份
        assert results[1].cached is False
        assert elapsed < 2.0  # 预算生效：不等慢源的 5s
        assert await cache.get("慢-b") is None  # 超时降级态不写缓存

    async def test_zero_budget_degrades_everything_without_raising(self) -> None:
        """预算为 0：全部候选立即降级（等价于预算耗尽），且不抛异常。

        用慢源保证没有候选能在预算到期前完成（快源可能在被取消前就跑完）。
        """
        crossref = FakeSource(title_echo=True, delay=5.0)
        verifier = ReferenceVerifier(crossref=crossref, total_timeout=0.0)
        candidates = [
            make_candidate(key=f"标题-{index}", doi=None, title=f"论文标题 {index}")
            for index in range(2)
        ]

        started = time.monotonic()
        results = await verifier.verify_many(candidates)
        elapsed = time.monotonic() - started

        assert [item.reference_key for item in results] == ["标题-0", "标题-1"]
        assert all(item.verdict is ReferenceVerdict.UNVERIFIED for item in results)
        assert all(item.reason.startswith("核验超预算（") for item in results)
        assert elapsed < 1.0  # 立即返回，不等慢源的 5s

    async def test_budget_covers_whole_batch_not_per_candidate(self) -> None:
        """串行时预算作用于整批：第 1 个完成，其余因整批超预算降级。"""
        crossref = FakeSource(title_echo=True, delay=0.3)
        verifier = ReferenceVerifier(crossref=crossref, max_concurrency=1, total_timeout=0.5)
        candidates = [
            make_candidate(key=f"标题-{index}", doi=None, title=f"论文标题 {index}")
            for index in range(3)
        ]

        results = await verifier.verify_many(candidates)

        assert [item.reference_key for item in results] == ["标题-0", "标题-1", "标题-2"]
        assert results[0].verdict is ReferenceVerdict.VERIFIED
        assert results[1].verdict is ReferenceVerdict.UNVERIFIED
        assert results[2].verdict is ReferenceVerdict.UNVERIFIED

    async def test_budget_with_all_cache_hits_returns_immediately(self) -> None:
        """预算路径下缓存命中仍生效（第二次调用全部 cached=True）。"""
        cache = ReferenceVerificationCache()
        crossref = FakeSource(title_echo=True)
        verifier = ReferenceVerifier(crossref=crossref, cache=cache, total_timeout=1.0)
        candidates = [
            make_candidate(key=f"标题-{index}", doi=None, title=f"论文标题 {index}")
            for index in range(3)
        ]

        await verifier.verify_many(candidates)  # 预热写缓存
        results = await verifier.verify_many(candidates)

        assert [item.reference_key for item in results] == [item.key for item in candidates]
        assert all(item.cached is True for item in results)

    async def test_budget_keeps_deduplication(self) -> None:
        """预算路径不破坏按 key 去重与顺序。"""
        crossref = FakeSource(title_echo=True)
        verifier = ReferenceVerifier(crossref=crossref, max_concurrency=4, total_timeout=1.0)
        candidates = [
            make_candidate(key="标题-a", doi=None, title="论文标题 a"),
            make_candidate(key="标题-b", doi=None, title="论文标题 b"),
            make_candidate(key="标题-a", doi=None, title="论文标题 a"),
        ]

        results = await verifier.verify_many(candidates)

        assert [item.reference_key for item in results] == ["标题-a", "标题-b", "标题-a"]
        assert crossref.title_calls == 2


# ===== 构造参数与协议 =====


class TestVerifierOptionsAndProtocol:
    """构造参数、intercept_unverified 属性与 MetadataSource 协议。"""

    def test_default_similarity_threshold_constant(self) -> None:
        assert DEFAULT_SIMILARITY_THRESHOLD == 0.90

    def test_intercept_unverified_property_is_readable(self) -> None:
        assert ReferenceVerifier().intercept_unverified is False
        assert ReferenceVerifier(intercept_unverified=True).intercept_unverified is True

    def test_metadata_source_protocol_declares_two_methods(self) -> None:
        assert "lookup_doi" in dir(MetadataSource)
        assert "search_by_title" in dir(MetadataSource)

    def test_fake_sources_satisfy_protocol_shape(self) -> None:
        """fake 源与真实适配器同形：核验器只依赖协议，不 import 具体客户端。"""
        for source in (FakeSource(), FakeSource(source="openalex")):
            assert hasattr(source, "lookup_doi")
            assert hasattr(source, "search_by_title")

    async def test_verifier_without_cache_still_caches_in_memory(self) -> None:
        crossref = FakeSource(doi_result=make_record())
        verifier = ReferenceVerifier(crossref=crossref)
        await verifier.verify(make_candidate())
        assert (await verifier.verify(make_candidate())).cached is True
