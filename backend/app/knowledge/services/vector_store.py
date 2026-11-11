"""向量存储门面：对上层保持同步接口不变，内部可切换 Chroma / pgvector。

迁移背景（2026-11）：向量库从 Chroma 迁到 Supabase PostgreSQL + pgvector。
迁移的硬约束是**上层零改动**——`IndexManager`、`HybridRetriever`、
`DocumentService`、`consistency`、`reindex` 都直接依赖本类的同步方法与语义，
其中 `search()` 的得分（`1 - 余弦距离`，非负截断）参与 RRF 融合与阈值判断。

因此结构改成「门面 + 后端协议」：

- 本文件只负责：后端选择、元数据装配、距离→得分换算、日志；
- 具体存储实现在 `app.knowledge.services.vector_backends`：
  - `ChromaVectorBackend`：历史实现，保留作回滚与离线测试；
  - `PgVectorBackend`：Supabase pgvector（见 `RAG__VECTOR_BACKEND=pgvector`）。

后端选择优先级（高 → 低，刻意让测试语义优先）：

1. 显式参数 `backend=`；
2. `persist_directory is not None` → `chroma`
   （保住既有测试 `persist_directory=""` 走 Chroma 内存模式，12 个测试文件零改动）；
3. 配置 `RAG__VECTOR_BACKEND`（默认 `chroma`，部署时显式设 `pgvector`）；
4. 兜底 `chroma`。

**默认保持 chroma 是有意的**：升级本身不改变任何环境的行为，
切到 pgvector 是显式动作（同理回滚也是一个环境变量）。
"""

from __future__ import annotations

import os
from typing import Any

from app.core.logging import get_logger
from app.knowledge.models.document import RetrievalResult, TextChunk
from app.knowledge.services.embedding import BaseEmbeddingService
from app.knowledge.services.vector_backends.base import (
    VectorBackend,
    VectorStoreConfigError,
)

logger = get_logger(__name__)

# 已知后端名（配置写错时立刻报错，而不是悄悄回退到 chroma 导致「以为迁了其实没迁」）
_KNOWN_BACKENDS = ("chroma", "pgvector")


def distance_to_score(distance: float) -> float:
    """余弦距离 → 相似度得分：`1 - distance`，并截断到非负。

    为什么必须截断：余弦距离取值 0~2（完全相反 = 2），`1 - 2 = -1`。
    负分会让混合检索的 RRF 融合与上层阈值判断出现反直觉行为
    （"越不相关分数越低"变成"负分被当成 0 之外的合法值"），
    Chroma 时代就是这么截断的，迁移后必须保持一致。
    """
    return max(0.0, 1.0 - float(distance))


class VectorStore:
    """向量存储：管理向量索引的增删查。

    用法：
        store = VectorStore(embedding_service=svc)
        store.add_chunks(chunks)
        results = store.search("查询文本", top_k=5)
    """

    def __init__(
        self,
        embedding_service: BaseEmbeddingService,
        *,
        collection_name: str = "knowledge_base",
        persist_directory: str | None = None,
        chroma_host: str | None = None,
        chroma_port: int = 8000,
        backend: str | None = None,
        pgvector_dsn: str | None = None,
    ) -> None:
        self._embedding = embedding_service
        self._collection_name = collection_name
        # 记录 Chroma 侧参数：即使当前后端是 pgvector，回滚时也不需要改调用方
        self._persist_directory = persist_directory
        self._chroma_host = (chroma_host or "").strip()
        self._chroma_port = int(chroma_port)
        # 显式 pgvector DSN（测试/脚本用）；None 时后端自行从配置解析
        self._pgvector_dsn = pgvector_dsn
        self._backend_name = _resolve_backend_name(backend, persist_directory)
        self._backend = _create_backend(
            self._backend_name,
            embedding_dimension=self._embedding.dimension,
            collection_name=collection_name,
            persist_directory=persist_directory,
            chroma_host=self._chroma_host,
            chroma_port=self._chroma_port,
            pgvector_dsn=pgvector_dsn,
        )

    # ------------------------------------------------------------------
    # 兼容点：历史测试直接调用 `_ensure_collection()` 观察连接模式选择
    # ------------------------------------------------------------------
    def _ensure_collection(self) -> None:
        """确保后端已初始化（Chroma 的 collection / pgvector 的 schema 校验）。"""
        ensure = getattr(self._backend, "_ensure_collection", None)
        if callable(ensure):
            ensure()

    @property
    def backend_name(self) -> str:
        """当前后端名（chroma / pgvector / memory），供健康检查与日志使用。"""
        return self._backend_name

    @property
    def collection_name(self) -> str:
        """collection 名称（Chroma collection / pgvector 的 collection 列值）。"""
        return self._collection_name

    @property
    def embedding_dimension(self) -> int:
        """嵌入服务向量维度。"""
        return self._embedding.dimension

    @property
    def size(self) -> int:
        """已索引的向量数。"""
        return self._backend.count()

    @property
    def is_ready(self) -> bool:
        """索引是否可检索。"""
        return self._backend.count() > 0

    def list_ids(self) -> list[str]:
        """返回全部向量 id（用于一致性校验）。

        为什么需要它：`size` 只给数量。**数量相等但 id 完全不同**的漂移
        （实测踩到：Chroma 是 UUID、`vector_index.chroma_id` 是 16 位 hex，
        交集为 0）在数量维度上看起来完全正常。必须比对 id 集合才能发现。
        """
        return self._backend.list_ids()

    def add_chunks(self, chunks: list[TextChunk]) -> int:
        """批量添加分块到向量索引。返回实际添加数量。"""
        if not chunks:
            return 0

        # 批量生成嵌入向量
        texts = [c.text for c in chunks]
        vectors = self._embedding.embed(texts)

        ids = [c.chunk_id for c in chunks]
        metadatas = [
            {
                "doc_id": c.doc_id,
                "chunk_index": c.metadata.get("chunk_index", 0),
                "doc_source": c.metadata.get("doc_source", ""),
                "start_offset": c.start_offset,
                "end_offset": c.end_offset,
                # user_id 随 chunk 元数据落库，供检索时 where 过滤（多用户隔离）
                "user_id": c.metadata.get("user_id", ""),
            }
            for c in chunks
        ]

        self._backend.upsert(ids=ids, vectors=vectors, documents=texts, metadatas=metadatas)
        logger.info("vector_chunks_added", backend=self._backend_name, count=len(chunks))
        return len(chunks)

    def remove_doc(self, doc_id: str) -> int:
        """删除指定文档的所有向量。返回删除数量。"""
        removed = self._backend.delete_by_doc(doc_id)
        logger.info(
            "vector_doc_removed",
            backend=self._backend_name,
            doc_id=doc_id,
            removed=removed,
        )
        return removed

    def remove_chunks(self, chunk_ids: list[str]) -> int:
        """按 chunk ID 精确删除，供影子索引对账清理孤儿。"""
        if not chunk_ids:
            return 0
        removed = self._backend.delete_by_ids(chunk_ids)
        logger.info("vector_chunks_removed", backend=self._backend_name, count=removed)
        return removed

    def clear(self) -> None:
        """清空当前 collection（重建）。"""
        self._backend.reset()
        logger.info("vector_store_cleared", backend=self._backend_name)

    def search(
        self,
        query: str,
        top_k: int = 10,
        *,
        where: dict[str, Any] | None = None,
    ) -> list[RetrievalResult]:
        """向量检索：返回与查询语义最相似的 top_k 条结果。"""
        if not self.is_ready:
            return []

        # 生成查询向量
        query_vec = self._embedding.embed_one(query)
        hits = self._backend.query(query_vec, top_k, where)

        results: list[RetrievalResult] = []
        for hit in hits:
            metadata = hit.metadata or {}
            results.append(
                RetrievalResult(
                    chunk_id=hit.chunk_id,
                    doc_id=metadata.get("doc_id", ""),
                    text=hit.text,
                    # 余弦距离越小越相似，转换为得分（1 - distance）；
                    # Chroma 与 pgvector 的 `<=>` 都返回余弦距离，换算只在这一处。
                    score=distance_to_score(hit.distance),
                    metadata=metadata,
                    source="vector",
                )
            )
        return results


def _resolve_backend_name(backend: str | None, persist_directory: str | None) -> str:
    """解析后端名，见模块文档中的优先级。"""
    if backend:
        name = backend.strip().lower()
    elif persist_directory is not None:
        # 显式传了存储路径（含 ""）→ 调用方自己控制，沿用 Chroma 语义。
        # 这条分支是 12 个既有测试文件零改动的前提。
        name = "chroma"
    else:
        name = _configured_backend()
    if name not in _KNOWN_BACKENDS:
        raise VectorStoreConfigError(
            f"未知的向量后端：{name!r}（可选：{', '.join(_KNOWN_BACKENDS)}）"
        )
    return name


def _configured_backend() -> str:
    """读配置里的后端名；配置模块不可用时回退 chroma。"""
    try:
        from app.core.config import get_settings

        value = getattr(get_settings().rag, "vector_backend", "") or "chroma"
    except Exception:  # pragma: no cover - 仅在配置损坏时触发
        return "chroma"
    return str(value).strip().lower() or "chroma"


def _create_backend(
    name: str,
    *,
    embedding_dimension: int,
    collection_name: str,
    persist_directory: str | None,
    chroma_host: str,
    chroma_port: int,
    pgvector_dsn: str | None,
) -> VectorBackend:
    """按名字构造后端实例（延迟导入，避免未用到的驱动成为硬依赖）。"""
    if name == "chroma":
        from app.knowledge.services.vector_backends.chroma_backend import ChromaVectorBackend

        # 默认持久化路径仍由环境变量控制（与迁移前一致）
        persist = persist_directory
        if persist is None:
            persist = os.getenv("RAG_CHROMA_PATH", ".chroma_db")
        return ChromaVectorBackend(
            collection_name=collection_name,
            persist_directory=persist,
            chroma_host=chroma_host,
            chroma_port=chroma_port,
        )

    if name == "pgvector":
        from app.knowledge.services.vector_backends.pgvector_backend import PgVectorBackend

        return PgVectorBackend(
            embedding_dimension=embedding_dimension,
            collection_name=collection_name,
            dsn=pgvector_dsn,
        )

    raise VectorStoreConfigError(f"未知的向量后端：{name!r}")
