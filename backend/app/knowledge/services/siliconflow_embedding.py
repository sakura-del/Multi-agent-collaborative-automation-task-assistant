"""SiliconFlow Embeddings API 适配器（默认 BGE-M3，1024 维）。"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterator
from typing import Any

import httpx

from app.core.logging import get_logger
from app.knowledge.services.embedding import BaseEmbeddingService
from app.knowledge.services.embedding_rate_limiter import SlidingWindowRateLimiter
from app.knowledge.services.vector_backends.base import (
    VectorStoreConfigError,
    VectorStoreUnavailableError,
)

logger = get_logger(__name__)

_DIMENSION = 1024
_MAX_RETRIES = 5
_MAX_RETRY_AFTER_SECONDS = 60.0


class SiliconFlowEmbeddingService(BaseEmbeddingService):
    """同步批量调用 SiliconFlow；每次 HTTP 尝试都经过 RPM/TPM 双限流。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        batch_size: int | None = None,
        rpm: int | None = None,
        tpm: int | None = None,
        client: httpx.Client | None = None,
        limiter: Any = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        from app.core.config import get_settings

        rag = get_settings().rag
        self._api_key = (api_key if api_key is not None else rag.siliconflow_api_key) or ""
        self._model_name = model or rag.embedding_model
        self._base_url = (base_url or rag.siliconflow_base_url).rstrip("/")
        self._batch_size = batch_size if batch_size is not None else rag.embedding_batch_size
        effective_rpm = rpm if rpm is not None else rag.embedding_rpm
        effective_tpm = tpm if tpm is not None else rag.embedding_tpm
        self._tpm = effective_tpm
        if not self._api_key:
            raise VectorStoreConfigError("SiliconFlow API key 未配置")
        self._client = client or httpx.Client(timeout=60.0)
        self._limiter = limiter or SlidingWindowRateLimiter(effective_rpm, effective_tpm)
        self._sleep = sleep

    @property
    def dimension(self) -> int:
        return _DIMENSION

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for batch in self._batches(texts):
            vectors.extend(self._embed_batch(batch))
        return vectors

    def _batches(self, texts: list[str]) -> Iterator[list[str]]:
        batch: list[str] = []
        estimated = 0
        for text in texts:
            tokens = len(text.encode("utf-8")) + 4
            if tokens > self._tpm:
                raise VectorStoreConfigError("单文本预估 token 超过配置的 TPM 上限")
            if batch and (len(batch) >= self._batch_size or estimated + tokens > self._tpm):
                yield batch
                batch = []
                estimated = 0
            batch.append(text)
            estimated += tokens
        if batch:
            yield batch

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        estimated = sum(len(text.encode("utf-8")) + 4 for text in texts)
        backoff = 1.0
        for attempt in range(_MAX_RETRIES + 1):
            record = self._limiter.acquire(estimated)
            started = time.perf_counter()
            try:
                response = self._client.post(
                    f"{self._base_url}/embeddings",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={
                        "model": self._model_name,
                        "input": texts,
                        "encoding_format": "float",
                    },
                )
                if response.status_code in {400, 401, 403, 404}:
                    raise VectorStoreConfigError(
                        f"SiliconFlow 请求/鉴权/模型配置错误：HTTP {response.status_code}"
                    )
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt >= _MAX_RETRIES:
                        raise VectorStoreUnavailableError(
                            f"SiliconFlow 暂时不可用：HTTP {response.status_code}，重试耗尽"
                        )
                    delay = self._retry_delay(response, backoff)
                    logger.warning(
                        "siliconflow_embedding_retry",
                        status=response.status_code,
                        attempt=attempt + 1,
                        delay_seconds=delay,
                        trace_id=response.headers.get("x-siliconcloud-trace-id", ""),
                    )
                    self._sleep(delay)
                    backoff = min(backoff * 2, 16.0)
                    continue
                if response.status_code != 200:
                    raise VectorStoreConfigError(
                        f"SiliconFlow 未支持的状态码：HTTP {response.status_code}"
                    )

                payload = response.json()
                usage = payload.get("usage") if isinstance(payload, dict) else None
                actual = usage.get("total_tokens") if isinstance(usage, dict) else None
                if isinstance(actual, int) and actual >= 0:
                    self._limiter.record_actual(record, actual)
                vectors = self._validate_payload(payload, len(texts))
                logger.info(
                    "siliconflow_embeddings",
                    count=len(texts),
                    estimated_tokens=estimated,
                    actual_tokens=actual if isinstance(actual, int) else estimated,
                    attempts=attempt + 1,
                    elapsed_ms=int((time.perf_counter() - started) * 1000),
                    trace_id=response.headers.get("x-siliconcloud-trace-id", ""),
                )
                return vectors
            except httpx.RequestError as exc:
                if attempt >= _MAX_RETRIES:
                    raise VectorStoreUnavailableError(
                        f"SiliconFlow 网络请求失败且重试耗尽：{type(exc).__name__}"
                    ) from exc
                logger.warning(
                    "siliconflow_embedding_network_retry",
                    attempt=attempt + 1,
                    delay_seconds=backoff,
                    error=type(exc).__name__,
                )
                self._sleep(backoff)
                backoff = min(backoff * 2, 16.0)

        raise VectorStoreUnavailableError("SiliconFlow 重试耗尽")

    def _retry_delay(self, response: httpx.Response, fallback: float) -> float:
        raw = response.headers.get("retry-after", "").strip()
        if raw:
            try:
                delay = float(raw)
            except ValueError:
                delay = fallback
            if delay >= 0:
                if delay > _MAX_RETRY_AFTER_SECONDS:
                    raise VectorStoreUnavailableError(
                        f"SiliconFlow Retry-After={delay:g}s 超过 60 秒上限"
                    )
                return delay
        return fallback

    def _validate_payload(self, payload: Any, expected_count: int) -> list[list[float]]:
        if not isinstance(payload, dict) or payload.get("model") != self._model_name:
            raise VectorStoreConfigError("SiliconFlow 响应模型与请求不一致")
        data = payload.get("data")
        if not isinstance(data, list) or len(data) != expected_count:
            raise VectorStoreConfigError("SiliconFlow 响应向量数量与输入不一致")
        try:
            ordered = sorted(data, key=lambda item: int(item["index"]))
        except (TypeError, KeyError, ValueError) as exc:
            raise VectorStoreConfigError("SiliconFlow 响应缺少合法 index") from exc
        if [int(item["index"]) for item in ordered] != list(range(expected_count)):
            raise VectorStoreConfigError("SiliconFlow 响应 index 不连续")

        vectors: list[list[float]] = []
        for item in ordered:
            vector = item.get("embedding")
            if not isinstance(vector, list) or len(vector) != _DIMENSION:
                raise VectorStoreConfigError("SiliconFlow 向量维度不是 1024")
            if not all(
                isinstance(value, (int, float)) and math.isfinite(value) for value in vector
            ):
                raise VectorStoreConfigError("SiliconFlow 向量包含非有限数值")
            vectors.append([float(value) for value in vector])
        return vectors
