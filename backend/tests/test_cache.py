"""RetrievalCache 测试：Redis 优先、内存回退、序列化与 TTL。

覆盖缓存键生成、读写、失效、清空、Redis 异常降级等行为。
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.knowledge.models.document import RetrievalResult
from app.knowledge.services.cache import RetrievalCache


def _make_results(n: int = 2) -> list[RetrievalResult]:
    """构造测试用检索结果列表。"""
    return [
        RetrievalResult(
            chunk_id=f"c{i}",
            doc_id="d1",
            text=f"内容片段 {i}",
            score=0.9 - i * 0.1,
            metadata={"doc_source": "test.txt"},
            source="hybrid",
        )
        for i in range(n)
    ]


class TestCacheKey:
    """缓存键生成：确定性 + top_k 隔离。"""

    def test_same_query_same_key(self) -> None:
        """相同 query+top_k 应生成相同键。"""
        cache = RetrievalCache()
        k1 = cache._make_key("查询", top_k=5)
        k2 = cache._make_key("查询", top_k=5)
        assert k1 == k2

    def test_different_query_different_key(self) -> None:
        """不同 query 应生成不同键。"""
        cache = RetrievalCache()
        assert cache._make_key("查询A", top_k=5) != cache._make_key("查询B", top_k=5)

    def test_different_top_k_different_key(self) -> None:
        """相同 query 不同 top_k 应生成不同键。"""
        cache = RetrievalCache()
        assert cache._make_key("查询", top_k=5) != cache._make_key("查询", top_k=10)


class TestMemoryCacheFallback:
    """无 Redis 时使用内存回退缓存。"""

    def test_get_miss_returns_none(self) -> None:
        """未命中返回 None。"""
        cache = RetrievalCache()
        assert cache.get("未缓存的查询", top_k=5) is None

    def test_set_then_get_returns_results(self) -> None:
        """写入后读取应返回原结果。"""
        cache = RetrievalCache()
        results = _make_results()
        cache.set("查询", results, top_k=5)
        cached = cache.get("查询", top_k=5)
        assert cached is not None
        assert len(cached) == len(results)
        assert cached[0].chunk_id == results[0].chunk_id

    def test_invalidate_removes_entry(self) -> None:
        """invalidate 应使缓存失效。"""
        cache = RetrievalCache()
        cache.set("查询", _make_results(), top_k=5)
        cache.invalidate("查询", top_k=5)
        assert cache.get("查询", top_k=5) is None

    def test_clear_removes_all(self) -> None:
        """clear 应清空全部缓存。"""
        cache = RetrievalCache()
        cache.set("q1", _make_results(), top_k=5)
        cache.set("q2", _make_results(), top_k=5)
        cache.clear()
        assert cache.get("q1", top_k=5) is None
        assert cache.get("q2", top_k=5) is None

    def test_is_available_always_true(self) -> None:
        """内存回退保证缓存始终可用。"""
        cache = RetrievalCache()
        assert cache.is_available is True

    def test_roundtrip_preserves_fields(self) -> None:
        """序列化/反序列化应保留所有字段。"""
        cache = RetrievalCache()
        original = _make_results(1)[0]
        cache.set("q", [original], top_k=5)
        cached = cache.get("q", top_k=5)
        assert cached is not None
        r = cached[0]
        assert r.chunk_id == original.chunk_id
        assert r.doc_id == original.doc_id
        assert r.text == original.text
        assert r.score == original.score
        assert r.source == original.source
        assert r.metadata["doc_source"] == original.metadata["doc_source"]


class TestRedisCache:
    """有 Redis 客户端时优先使用 Redis。"""

    def test_set_uses_redis_setex(self) -> None:
        """set 应调用 redis.setex 写入带 TTL 的键。"""
        redis_mock = MagicMock()
        cache = RetrievalCache(redis_client=redis_mock, ttl=120)
        cache.set("查询", _make_results(), top_k=5)
        redis_mock.setex.assert_called_once()
        args = redis_mock.setex.call_args[0]
        # (key, ttl, value)
        assert args[1] == 120

    def test_get_uses_redis(self) -> None:
        """get 应从 redis 读取。"""
        redis_mock = MagicMock()
        # 先写入再读取
        cache = RetrievalCache(redis_client=redis_mock, ttl=60)
        cache.set("查询", _make_results(), top_k=5)
        # 模拟 Redis 返回缓存值
        redis_mock.get.return_value = cache._serialize(_make_results())
        cached = cache.get("查询", top_k=5)
        assert cached is not None
        redis_mock.get.assert_called_once()

    def test_get_redis_miss_returns_none(self) -> None:
        """Redis 返回 None 时表示未命中。"""
        redis_mock = MagicMock()
        redis_mock.get.return_value = None
        cache = RetrievalCache(redis_client=redis_mock)
        assert cache.get("未命中", top_k=5) is None

    def test_invalidate_uses_redis_delete(self) -> None:
        """invalidate 应调用 redis.delete。"""
        redis_mock = MagicMock()
        cache = RetrievalCache(redis_client=redis_mock)
        cache.invalidate("查询", top_k=5)
        redis_mock.delete.assert_called_once()

    def test_clear_uses_scan_iter(self) -> None:
        """clear 应扫描 rag:retrieval:* 前缀并删除。"""
        redis_mock = MagicMock()
        redis_mock.scan_iter.return_value = iter([b"rag:retrieval:k1", b"rag:retrieval:k2"])
        cache = RetrievalCache(redis_client=redis_mock)
        cache.clear()
        assert redis_mock.delete.call_count == 2


class TestRedisFailureFallback:
    """Redis 异常时降级处理。"""

    def test_get_failure_returns_none(self) -> None:
        """Redis get 异常时返回 None 而非抛出。"""
        redis_mock = MagicMock()
        redis_mock.get.side_effect = RuntimeError("redis down")
        cache = RetrievalCache(redis_client=redis_mock)
        assert cache.get("q", top_k=5) is None

    def test_set_failure_silent(self) -> None:
        """Redis set 异常时不抛出。"""
        redis_mock = MagicMock()
        redis_mock.setex.side_effect = RuntimeError("redis down")
        cache = RetrievalCache(redis_client=redis_mock)
        # 不抛出异常
        cache.set("q", _make_results(), top_k=5)

    def test_clear_failure_silent(self) -> None:
        """Redis clear 异常时不抛出。"""
        redis_mock = MagicMock()
        redis_mock.scan_iter.side_effect = RuntimeError("redis down")
        cache = RetrievalCache(redis_client=redis_mock)
        cache.clear()
