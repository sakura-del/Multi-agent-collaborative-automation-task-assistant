"""Chroma 后端：历史实现原样保留，用于回滚与离线测试。

三种连接模式（优先级从高到低，与迁移前完全一致）：

1. **服务端**：给了 `chroma_host` → `HttpClient`（compose 的独立 chroma 服务）；
2. **本地持久化**：有 `persist_directory` → `PersistentClient`（单机开发）；
3. **内存**：两者都空 → `Client()`（测试）。

为什么留着：`RAG__VECTOR_BACKEND=chroma` 是 pgvector 的回滚开关。
验收期内 chroma 容器与 `maa-chroma-data` 卷都不删，出问题改一个环境变量即可切回。
"""

from __future__ import annotations

import os
from typing import Any

from app.core.logging import get_logger
from app.knowledge.services.vector_backends.base import VectorMatch

logger = get_logger(__name__)


class ChromaVectorBackend:
    """Chroma collection 的增删查实现。"""

    name = "chroma"

    def __init__(
        self,
        *,
        collection_name: str = "knowledge_base",
        persist_directory: str | None = None,
        chroma_host: str | None = None,
        chroma_port: int = 8000,
    ) -> None:
        self._collection_name = collection_name
        # 默认持久化路径：可由环境变量覆盖；测试时显式传 "" 使用内存模式
        # 注意：用 `is None` 判断而非 `or`，因为空字符串表示显式请求内存模式
        if persist_directory is None:
            persist_directory = os.getenv("RAG_CHROMA_PATH", ".chroma_db")
        self._persist_directory = persist_directory
        # 服务端模式（compose 提供独立 chroma 服务）：给出 host 时优先走 HTTP，
        # 与 docker-compose 的 RAG__CHROMA_HOST / RAG__CHROMA_PORT 对齐。
        self._chroma_host = (chroma_host or "").strip()
        self._chroma_port = int(chroma_port)
        self._client: Any = None
        self._collection: Any = None

    @property
    def collection_name(self) -> str:
        return self._collection_name

    def _ensure_collection(self) -> None:
        """延迟初始化 Chroma 客户端与 collection。

        延迟的好处：导入本模块不需要安装/连接 chroma，
        且测试可以在调用前 patch `chromadb.HttpClient` 观察模式选择。
        """
        if self._collection is not None:
            return

        try:
            import chromadb
        except ModuleNotFoundError as exc:  # pragma: no cover - 生产镜像不装 chromadb
            # Issue #11 M-5：chromadb 已移到可选依赖 `[chroma]`（4 个上游未修复 CVE），
            # 生产镜像不安装它。这里给一句能直接照做的提示，而不是裸 ModuleNotFoundError。
            raise RuntimeError(
                "chroma 向量后端需要可选依赖：pip install -e '.[chroma]'。"
                "生产环境请使用 pgvector（RAG__VECTOR_BACKEND=pgvector）。"
            ) from exc

        if self._chroma_host:
            # 服务端模式：与 compose 的 chroma 服务通信，不在容器本地落库
            self._client = chromadb.HttpClient(host=self._chroma_host, port=self._chroma_port)
        elif self._persist_directory:
            # 持久化模式：数据落盘
            self._client = chromadb.PersistentClient(path=self._persist_directory)
        else:
            # 内存模式：测试用，进程结束即丢失
            self._client = chromadb.Client()

        # get_or_create：已存在则复用，避免重复创建
        self._collection = self._client.get_or_create_collection(
            name=self._collection_name,
            metadata={"hnsw:space": "cosine"},  # 余弦相似度
        )
        logger.info(
            "vector_store_initialized",
            backend=self.name,
            collection=self._collection_name,
            mode=(
                f"http:{self._chroma_host}:{self._chroma_port}"
                if self._chroma_host
                else ("persist" if self._persist_directory else "memory")
            ),
        )

    def count(self) -> int:
        """已索引的向量数。"""
        self._ensure_collection()
        return int(self._collection.count())

    def list_ids(self) -> list[str]:
        """返回全部向量 id（排序后返回，保证排障输出可复现）。"""
        self._ensure_collection()
        payload = self._collection.get(include=[])
        return sorted(str(i) for i in (payload.get("ids") or []))

    def upsert(
        self,
        ids: list[str],
        vectors: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        """按 id 幂等写入（Chroma 的 upsert 语义）。"""
        if not ids:
            return
        self._ensure_collection()
        self._collection.upsert(
            ids=ids,
            embeddings=vectors,
            documents=documents,
            metadatas=metadatas,
        )

    def delete_by_doc(self, doc_id: str) -> int:
        """删除指定文档的所有向量。

        Chroma 的 delete 不返回精确数，用 before/after 差值估算（历史行为）。
        """
        self._ensure_collection()
        before = self._collection.count()
        self._collection.delete(where={"doc_id": doc_id})
        after = self._collection.count()
        return int(before - after)

    def delete_by_ids(self, ids: list[str]) -> int:
        """按 chunk ID 精确删除；空列表不访问 Chroma。"""
        if not ids:
            return 0
        self._ensure_collection()
        before = self._collection.count()
        self._collection.delete(ids=ids)
        return int(before - self._collection.count())

    def reset(self) -> None:
        """重建 collection（删除后重新创建）。"""
        self._ensure_collection()
        self._client.delete_collection(self._collection_name)
        self._collection = self._client.get_or_create_collection(
            name=self._collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def query(
        self,
        vector: list[float],
        top_k: int,
        where: dict[str, Any] | None = None,
    ) -> list[VectorMatch]:
        """余弦距离升序查询。"""
        self._ensure_collection()
        query_params: dict[str, Any] = {
            "query_embeddings": [vector],
            "n_results": top_k,
        }
        if where:
            query_params["where"] = where

        raw = self._collection.query(**query_params)

        # Chroma 返回结构：{ids: [[...]], documents: [[...]], metadatas: [[...]], distances: [[...]]}
        ids_batch = raw.get("ids", [[]])
        docs_batch = raw.get("documents", [[]])
        meta_batch = raw.get("metadatas", [[]])
        dist_batch = raw.get("distances", [[]])

        if not ids_batch or not ids_batch[0]:
            return []

        hits: list[VectorMatch] = []
        for i, chunk_id in enumerate(ids_batch[0]):
            hits.append(
                VectorMatch(
                    chunk_id=str(chunk_id),
                    text=docs_batch[0][i] if i < len(docs_batch[0]) else "",
                    metadata=meta_batch[0][i] if i < len(meta_batch[0]) else {},
                    distance=float(dist_batch[0][i]) if i < len(dist_batch[0]) else 0.0,
                )
            )
        return hits
