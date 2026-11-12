"""Chroma 连接模式（CI smoke 失败根因回归）。

## 根因

`VectorStore._ensure_collection()` 只有两条路径：

```python
if self._persist_directory:
    chromadb.PersistentClient(path=...)   # 本地嵌入式
else:
    chromadb.Client()                     # 纯内存
```

而 compose **明确提供了独立的 chroma 服务**（`RAG__CHROMA_HOST=chroma` /
`RAG__CHROMA_PORT=8000`），`config.RAGConfig` 也定义了这两个字段——但代码**从不读取**，
于是容器内始终走本地嵌入模式：
- 在 `/app` 下创建 `.chroma_db`，与 compose 的 chroma 服务数据完全脱节；
- chromadb 1.x 的 Rust 绑定在部分环境（CI smoke 的全新容器）下崩溃
  （`KeyError: '.chroma_db'` / `'RustBindingsAPI' object has no attribute 'bindings'`），
  文档入库返回 422，smoke 断言失败。

## 正确行为

1. **显式给出 host/port 时优先用 `HttpClient`**（服务端模式，与 compose 一致）；
2. 未给 host 时保持既有行为（本地持久化 / 内存），不破坏测试与单机开发；
3. host 模式下 `persist_directory` 不参与（数据由服务端持久化）。

本文件锁定这三条，防止回退到「有服务却用本地嵌入」。

> chromadb 自 2026-11-12 起是可选依赖 `[chroma]`（生产镜像不装，见
> `docs/security/dependency-risk.md`）。本文件测的是它的连接模式选择，
> 因此缺包时整体跳过，而不是装一个带未修复 CVE 的包到生产镜像里。
"""

from __future__ import annotations

import pytest

pytest.importorskip("chromadb", reason="chromadb 是可选依赖 [chroma]，CI/生产镜像可不装")

from typing import Any
from unittest.mock import patch

from app.knowledge.services.vector_store import VectorStore


class _FakeEmbedding:
    """最小嵌入替身（VectorStore 只要求 `.dimension` 与 embed 方法）。"""

    dimension = 8

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * self.dimension for _ in texts]


def _store(**kwargs: Any) -> VectorStore:
    return VectorStore(embedding_service=_FakeEmbedding(), **kwargs)  # type: ignore[arg-type]


class TestConnectionModeSelection:
    def test_http_client_used_when_host_given(self) -> None:
        """给出 host/port 时必须用 HttpClient（服务端模式），而不是 PersistentClient。"""
        store = _store(chroma_host="chroma", chroma_port=8000, persist_directory=".chroma_db")

        with (
            patch("chromadb.HttpClient") as http_client,
            patch("chromadb.PersistentClient") as persistent_client,
        ):
            store._ensure_collection()

        http_client.assert_called_once()
        persistent_client.assert_not_called()
        # 必须把 host/port 真正传下去
        kwargs = http_client.call_args.kwargs
        assert kwargs.get("host") == "chroma"
        assert kwargs.get("port") == 8000

    def test_local_persistent_used_without_host(self) -> None:
        """未给 host 时保持既有本地持久化行为（单机开发/测试）。"""
        store = _store(chroma_host="", persist_directory=".chroma_db")

        with (
            patch("chromadb.HttpClient") as http_client,
            patch("chromadb.PersistentClient") as persistent_client,
        ):
            store._ensure_collection()

        persistent_client.assert_called_once()
        http_client.assert_not_called()

    def test_in_memory_used_without_host_and_without_path(self) -> None:
        """既无 host 也无路径 → 内存模式（测试用）。"""
        store = _store(chroma_host="", persist_directory="")

        with (
            patch("chromadb.HttpClient") as http_client,
            patch("chromadb.PersistentClient") as persistent_client,
            patch("chromadb.Client") as memory_client,
        ):
            store._ensure_collection()

        memory_client.assert_called_once()
        http_client.assert_not_called()
        persistent_client.assert_not_called()

    def test_host_mode_ignores_persist_directory(self) -> None:
        """服务端模式下本地路径不参与（数据由服务端持久化）。"""
        store = _store(chroma_host="chroma", chroma_port=8000, persist_directory="/app/.chroma_db")

        with patch("chromadb.HttpClient") as http_client:
            store._ensure_collection()

        assert "path" not in http_client.call_args.kwargs


class TestConfigWiring:
    @pytest.fixture(autouse=True)
    def _force_chroma_backend(self, monkeypatch) -> None:
        """本类测的是 chroma 相关接线，必须显式指定后端。

        2026-11-12（Issue #11 M-5）后默认后端是 pgvector（生产真实值，且镜像不再装
        chromadb）；不显式指回 chroma，工厂会去构造 pgvector 并因缺 DSN 报
        `VectorStoreConfigError` —— 那是「默认值变了」，不是接线断了。
        """
        from app.core.config import settings

        monkeypatch.setattr(settings.rag, "vector_backend", "chroma", raising=False)

    def test_settings_expose_chroma_host_and_port(self) -> None:
        """配置层必须暴露这两个字段（compose 已通过 RAG__CHROMA_* 注入）。"""
        from app.core.config import get_settings

        rag = get_settings().rag
        assert hasattr(rag, "chroma_host")
        assert hasattr(rag, "chroma_port")
        assert isinstance(rag.chroma_port, int)

    @staticmethod
    def _init_with_spy(**kwargs: Any) -> dict[str, Any]:
        """用 Spy 替换 VectorStore 调一次 init_kb，返回它收到的 kwargs。

        每个用例前后都 `reset_kb()`：`init_kb` 有全局单例缓存，
        不重置会让后续用例拿到上一次的实例、`captured` 为空（测试间污染）。
        """
        from app.knowledge.services import factory

        captured: dict[str, Any] = {}

        class _SpyStore:
            # 工厂会在初始化日志里读 `backend_name`：替身必须满足被替代对象的接口
            backend_name = "chroma"

            def __init__(self, *args: Any, **inner_kwargs: Any) -> None:
                captured.update(inner_kwargs)

            def clear(self) -> None:
                return None

        factory.reset_kb()
        try:
            with (
                patch.object(factory, "VectorStore", _SpyStore),
                patch.object(factory, "create_embedding_service", return_value=_FakeEmbedding()),
                patch.object(factory, "create_reranker", return_value=object()),
            ):
                factory.init_kb(force_mock_embedding=True, force_mock_reranker=True, **kwargs)
        finally:
            factory.reset_kb()
        return captured

    def test_factory_passes_chroma_host_from_settings(self) -> None:
        """工厂必须把配置里的 host/port 传给 VectorStore（否则配置形同虚设）。

        仅在 `chroma_persist_dir is None`（未显式指定存储模式）时套用服务端地址；
        显式传 `chroma_persist_dir` 表示调用方自行控制（测试传 "" 走内存）。
        """
        captured = self._init_with_spy()

        assert "chroma_host" in captured, f"VectorStore 未收到 chroma_host：{captured}"
        assert "chroma_port" in captured

    def test_factory_ignores_host_when_persist_dir_explicit(self) -> None:
        """显式 `chroma_persist_dir`（测试场景）不得套用服务端地址，否则会连不存在的 chroma。"""
        captured = self._init_with_spy(chroma_persist_dir="")

        assert captured.get("chroma_host") == ""
        assert captured.get("persist_directory") == ""
