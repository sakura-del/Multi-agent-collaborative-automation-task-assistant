"""SiliconFlow BGE-M3 适配器：批量、契约、限流与重试测试。"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.knowledge.services.embedding_rate_limiter import UsageRecord
from app.knowledge.services.siliconflow_embedding import SiliconFlowEmbeddingService
from app.knowledge.services.vector_backends.base import (
    VectorStoreConfigError,
    VectorStoreUnavailableError,
)


class RecordingLimiter:
    def __init__(self) -> None:
        self.acquired: list[int] = []
        self.actual: list[int] = []

    def acquire(self, tokens: int) -> UsageRecord:
        self.acquired.append(tokens)
        return UsageRecord(at=0.0, tokens=tokens)

    def record_actual(self, record: UsageRecord, actual_tokens: int) -> None:
        self.actual.append(actual_tokens)


def _payload(count: int, *, reverse: bool = False, model: str = "BAAI/bge-m3") -> dict[str, Any]:
    data = [{"index": index, "embedding": [float(index)] + [0.0] * 1023} for index in range(count)]
    if reverse:
        data.reverse()
    return {
        "object": "list",
        "model": model,
        "data": data,
        "usage": {"prompt_tokens": count * 10, "completion_tokens": 0, "total_tokens": count * 10},
    }


def make_service(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    limiter: RecordingLimiter | None = None,
    sleeps: list[float] | None = None,
    batch_size: int = 64,
    tpm: int = 400000,
) -> SiliconFlowEmbeddingService:
    return SiliconFlowEmbeddingService(
        api_key="test-key",
        model="BAAI/bge-m3",
        base_url="https://api.siliconflow.cn/v1",
        batch_size=batch_size,
        tpm=tpm,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        limiter=limiter or RecordingLimiter(),
        sleep=(sleeps.append if sleeps is not None else (lambda _seconds: None)),
    )


def test_empty_input_sends_no_request() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=_payload(1))

    service = make_service(handler)
    assert service.embed([]) == []
    assert calls == []


def test_batch_response_is_reordered_by_index() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["model"] == "BAAI/bge-m3"
        assert payload["encoding_format"] == "float"
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(200, json=_payload(2, reverse=True))

    result = make_service(handler).embed(["第一条", "第二条"])

    assert [vector[0] for vector in result] == [0.0, 1.0]
    assert len(result) == 2
    assert all(len(vector) == 1024 for vector in result)


def test_65_items_use_64_plus_one_and_record_actual_usage() -> None:
    sizes: list[int] = []
    limiter = RecordingLimiter()

    def handler(request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.content)["input"]
        sizes.append(len(texts))
        return httpx.Response(200, json=_payload(len(texts)))

    result = make_service(handler, limiter=limiter).embed([f"文本{i}" for i in range(65)])

    assert sizes == [64, 1]
    assert len(result) == 65
    assert limiter.actual == [640, 10]


def test_token_budget_splits_batch() -> None:
    sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.content)["input"]
        sizes.append(len(texts))
        return httpx.Response(200, json=_payload(len(texts)))

    service = make_service(handler, tpm=100)
    service.embed(["x" * 60, "y" * 60])

    assert sizes == [1, 1]


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_non_retryable_status_fails_once(status: int) -> None:
    calls = 0
    limiter = RecordingLimiter()

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, json={"detail": "invalid"})

    with pytest.raises(VectorStoreConfigError):
        make_service(handler, limiter=limiter).embed(["文本"])

    assert calls == 1
    assert len(limiter.acquired) == 1


def test_429_retry_after_reenters_limiter_then_succeeds() -> None:
    calls = 0
    limiter = RecordingLimiter()
    sleeps: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, json={"detail": "limited"})
        return httpx.Response(200, json=_payload(1))

    result = make_service(handler, limiter=limiter, sleeps=sleeps).embed(["文本"])

    assert len(result) == 1
    assert calls == 2
    assert len(limiter.acquired) == 2
    assert sleeps == [2.0]


def test_503_uses_bounded_backoff_then_succeeds() -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, json={"detail": "temporary"})
        return httpx.Response(200, json=_payload(1))

    result = make_service(handler, sleeps=sleeps).embed(["文本"])

    assert len(result) == 1
    assert sleeps == [1.0]


def test_repeated_429_stops_after_five_retries() -> None:
    calls = 0
    limiter = RecordingLimiter()
    sleeps: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, json={"detail": "limited"})

    with pytest.raises(VectorStoreUnavailableError):
        make_service(handler, limiter=limiter, sleeps=sleeps).embed(["文本"])

    assert calls == 6
    assert len(limiter.acquired) == 6
    assert sleeps == [1.0, 2.0, 4.0, 8.0, 16.0]


def test_network_error_retries_then_succeeds() -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(200, json=_payload(1))

    result = make_service(handler, sleeps=sleeps).embed(["文本"])

    assert len(result) == 1
    assert sleeps == [1.0]


@pytest.mark.parametrize(
    "payload",
    [
        {"object": "list", "model": "BAAI/bge-m3", "usage": {"total_tokens": 1}},
        {"object": "list", "model": "BAAI/bge-m3", "data": [], "usage": {"total_tokens": 1}},
        {
            "object": "list",
            "model": "BAAI/bge-m3",
            "data": [{"index": 0, "embedding": [0.0] * 1023}],
            "usage": {"total_tokens": 1},
        },
        {
            "object": "list",
            "model": "BAAI/bge-m3",
            "data": [{"index": 0, "embedding": [float("nan")] + [0.0] * 1023}],
            "usage": {"total_tokens": 1},
        },
        _payload(1, model="BAAI/other"),
    ],
)
def test_invalid_response_contract_fails(payload: dict[str, Any]) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        body = json.dumps(payload, allow_nan=True).encode("utf-8")
        return httpx.Response(200, headers={"Content-Type": "application/json"}, content=body)

    with pytest.raises(VectorStoreConfigError):
        make_service(handler).embed(["文本"])


def test_missing_key_fails_without_http() -> None:
    with pytest.raises(VectorStoreConfigError, match="API key"):
        SiliconFlowEmbeddingService(api_key="", model="BAAI/bge-m3")
