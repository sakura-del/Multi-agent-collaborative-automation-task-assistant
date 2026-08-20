"""检索缓存层：基于 Redis 缓存高频查询结果，降低重复检索延迟。

设计要点：
- query hash 为键：MD5(query + top_k) 生成确定性缓存键，避免长字符串键
- TTL 自动过期：默认 300 秒，可配置
- 序列化：RetrievalResult → JSON 存储，反序列化还原
- 内存回退：Redis 不可用时降级为进程内字典缓存，不阻断服务
"""

from __future__ import annotations

import json
from typing import Any

from app.core.logging import get_logger
from app.knowledge.models.document import RetrievalResult

logger = get_logger(__name__)

# 默认缓存 TTL（秒）
_DEFAULT_TTL = 300


class RetrievalCache:
    """检索结果缓存：Redis 优先，不可用时回退到内存。

    用法：
        cache = RetrievalCache(redis_client=client)
        cached = cache.get(query, top_k=5)
        if cached is None:
            results = retriever.search(query, top_k=5)
            cache.set(query, results, top_k=5)
    """

    def __init__(
        self,
        redis_client: Any | None = None,
        ttl: int = _DEFAULT_TTL,
    ) -> None:
        self._redis = redis_client
        self._ttl = ttl
        # 内存回退缓存：Redis 不可用时使用
        self._memory: dict[str, str] = {}

    @property
    def is_available(self) -> bool:
        """缓存是否可用（Redis 或内存）。"""
        return True  # 内存回退保证始终可用

    def _make_key(self, namespace: str, query: str, top_k: int) -> str:
        """生成确定性缓存键。

        采用带前缀的原始键（非 md5）：namespace（通常为 user_id）参与键构造，
        既保证不同用户同名查询不串缓存，又让 Redis 可按
        ``rag:retrieval:{namespace}:*`` 前缀扫描以精确定向清除某用户的缓存。
        """
        return f"rag:retrieval:{namespace}:{query}:{top_k}"

    def get(self, namespace: str, query: str, top_k: int = 5) -> list[RetrievalResult] | None:
        """查询缓存。未命中返回 None。"""
        key = self._make_key(namespace, query, top_k)
        try:
            if self._redis is not None:
                data = self._redis.get(key)
            else:
                data = self._memory.get(key)
            if data is None:
                return None
            return self._deserialize(data)
        except Exception as exc:
            logger.warning("cache_get_failed", error=str(exc))
            return None

    def set(
        self,
        namespace: str,
        query: str,
        results: list[RetrievalResult],
        top_k: int = 5,
    ) -> None:
        """写入缓存。"""
        key = self._make_key(namespace, query, top_k)
        data = self._serialize(results)
        try:
            if self._redis is not None:
                self._redis.setex(key, self._ttl, data)
            else:
                self._memory[key] = data
        except Exception as exc:
            logger.warning("cache_set_failed", error=str(exc))

    def invalidate(self, namespace: str, query: str, top_k: int = 5) -> None:
        """使指定 namespace 下指定查询的缓存失效。"""
        key = self._make_key(namespace, query, top_k)
        try:
            if self._redis is not None:
                self._redis.delete(key)
            else:
                self._memory.pop(key, None)
        except Exception as exc:
            logger.warning("cache_invalidate_failed", error=str(exc))

    def clear(self, namespace: str | None = None) -> None:
        """清空缓存。

        传入 namespace 时只删除该 namespace 的键（文档更新后仅失效该用户）；
        不传时删除全部（重建索引等全局场景）。
        """
        try:
            if self._redis is not None:
                # 键为带前缀原始键，可按 namespace 前缀扫描精确定向清除
                pattern = (
                    f"rag:retrieval:{namespace}:*" if namespace is not None else "rag:retrieval:*"
                )
                for key in self._redis.scan_iter(pattern):
                    self._redis.delete(key)
            else:
                if namespace is not None:
                    prefix = f"rag:retrieval:{namespace}:"
                    for key in [k for k in self._memory if k.startswith(prefix)]:
                        self._memory.pop(key, None)
                else:
                    self._memory.clear()
        except Exception as exc:
            logger.warning("cache_clear_failed", error=str(exc))

    @staticmethod
    def _serialize(results: list[RetrievalResult]) -> str:
        """序列化检索结果列表为 JSON。"""
        return json.dumps(
            [
                {
                    "chunk_id": r.chunk_id,
                    "doc_id": r.doc_id,
                    "text": r.text,
                    "score": r.score,
                    "metadata": r.metadata,
                    "source": r.source,
                }
                for r in results
            ],
            ensure_ascii=False,
        )

    @staticmethod
    def _deserialize(data: str | bytes) -> list[RetrievalResult]:
        """反序列化 JSON 为检索结果列表。"""
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        items = json.loads(data)
        return [
            RetrievalResult(
                chunk_id=item["chunk_id"],
                doc_id=item["doc_id"],
                text=item["text"],
                score=item["score"],
                metadata=item.get("metadata", {}),
                source=item.get("source", "hybrid"),
            )
            for item in items
        ]
