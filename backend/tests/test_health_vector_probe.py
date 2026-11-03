"""就绪探针的向量后端分派。

为什么需要：迁移到 pgvector 后，探针若仍只探 Chroma 会给出**假就绪**——
chroma 容器活着、向量库其实连不上，容器却报 healthy。
反过来，只探 Chroma 也会在彻底移除 Chroma 后让服务永远 not_ready。
"""

from __future__ import annotations

import json
from typing import Any

from app.api.v1.endpoints import health
from app.core.config import settings


async def _true() -> bool:
    return True


async def _false() -> bool:
    return False


def _body(response: Any) -> dict[str, Any]:
    return json.loads(response.body)


class TestProbeDispatch:
    async def test_pgvector_backend_probes_pgvector(self, monkeypatch) -> None:
        monkeypatch.setattr(settings.rag, "vector_backend", "pgvector")
        monkeypatch.setattr(health, "probe_database", _true)
        monkeypatch.setattr(health, "probe_redis", _true)
        monkeypatch.setattr(health, "probe_pgvector", _true)
        # 若错误地走了 Chroma，这里会让用例失败
        monkeypatch.setattr(health, "probe_chroma", _false)

        response = await health.readiness_check()
        body = _body(response)

        assert response.status_code == 200
        assert body["checks"]["vector"] == "ok"
        assert "chroma" not in body["checks"]

    async def test_pgvector_failure_is_not_ready(self, monkeypatch) -> None:
        monkeypatch.setattr(settings.rag, "vector_backend", "pgvector")
        monkeypatch.setattr(health, "probe_database", _true)
        monkeypatch.setattr(health, "probe_redis", _true)
        monkeypatch.setattr(health, "probe_pgvector", _false)

        response = await health.readiness_check()
        body = _body(response)

        assert response.status_code == 503
        assert body["checks"]["vector"] == "failed"

    async def test_chroma_backend_keeps_legacy_key(self, monkeypatch) -> None:
        """chroma 模式必须保留 `checks.chroma`：既有脚本/看板读的是这个键。"""
        monkeypatch.setattr(settings.rag, "vector_backend", "chroma")
        monkeypatch.setattr(health, "probe_database", _true)
        monkeypatch.setattr(health, "probe_redis", _true)
        monkeypatch.setattr(health, "probe_chroma", _true)
        monkeypatch.setattr(health, "probe_pgvector", _false)

        response = await health.readiness_check()
        body = _body(response)

        assert response.status_code == 200
        assert body["checks"]["vector"] == "ok"
        assert body["checks"]["chroma"] == "ok"


class TestPgvectorProbeRobustness:
    async def test_returns_false_when_kb_not_initialized(self, monkeypatch) -> None:
        """知识库未初始化时探针应报 not_ready，而不是把异常抛给健康端点。"""
        from app.knowledge.services import factory

        def _not_initialized() -> Any:
            raise factory.KnowledgeBaseNotInitializedError()

        monkeypatch.setattr(factory, "get_vector_store", _not_initialized)
        assert await health.probe_pgvector() is False

    async def test_returns_false_when_ping_fails(self, monkeypatch) -> None:
        from app.knowledge.services import factory

        class _Store:
            backend_name = "pgvector"

            class _Backend:
                @staticmethod
                def ping() -> bool:
                    return False

            _backend = _Backend()

        monkeypatch.setattr(factory, "get_vector_store", lambda: _Store())
        assert await health.probe_pgvector() is False
