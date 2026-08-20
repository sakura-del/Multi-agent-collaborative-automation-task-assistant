"""Chroma 向量数据库：持久化存储与余弦相似度检索。

封装 ChromaDB 的 collection 管理与 CRUD，提供统一的向量检索接口。

设计要点：
- 双模式：持久化模式（开发/生产）与内存模式（测试/CI），通过 persist_directory 控制
- 延迟连接：首次操作时才建立连接，避免启动阻塞
- 批量插入：支持一次添加多个分块，减少 IO 开销
- 元数据过滤：检索时支持按 doc_id 等字段过滤
"""

from __future__ import annotations

import os
from typing import Any

from app.core.logging import get_logger
from app.knowledge.models.document import RetrievalResult, TextChunk
from app.knowledge.services.embedding import BaseEmbeddingService

logger = get_logger(__name__)


class VectorStore:
    """Chroma 向量存储：管理向量索引的增删查。

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
    ) -> None:
        self._embedding = embedding_service
        self._collection_name = collection_name
        # 默认持久化路径：可由环境变量覆盖；测试时显式传 "" 使用内存模式
        # 注意：用 `is None` 判断而非 `or`，因为空字符串表示显式请求内存模式
        if persist_directory is None:
            persist_directory = os.getenv("RAG_CHROMA_PATH", ".chroma_db")
        self._persist_directory = persist_directory
        self._client: Any = None
        self._collection: Any = None

    def _ensure_collection(self) -> None:
        """延迟初始化 Chroma 客户端与 collection。"""
        if self._collection is not None:
            return

        import chromadb

        if self._persist_directory:
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
            collection=self._collection_name,
            persist=bool(self._persist_directory),
        )

    @property
    def collection_name(self) -> str:
        """Chroma collection 名称。"""
        return self._collection_name

    @property
    def embedding_dimension(self) -> int:
        """嵌入服务向量维度。"""
        return self._embedding.dimension

    @property
    def size(self) -> int:
        """已索引的向量数。"""
        self._ensure_collection()
        return self._collection.count()

    @property
    def is_ready(self) -> bool:
        """索引是否可检索。"""
        self._ensure_collection()
        return self._collection.count() > 0

    def add_chunks(self, chunks: list[TextChunk]) -> int:
        """批量添加分块到向量索引。返回实际添加数量。"""
        if not chunks:
            return 0
        self._ensure_collection()

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

        # Chroma 的 upsert：已存在则更新，避免重复
        self._collection.upsert(
            ids=ids,
            embeddings=vectors,
            documents=texts,
            metadatas=metadatas,
        )
        logger.info("vector_chunks_added", count=len(chunks))
        return len(chunks)

    def remove_doc(self, doc_id: str) -> int:
        """删除指定文档的所有向量。返回删除数量（Chroma 不返回精确数，用 before/after 差值估算）。"""
        self._ensure_collection()
        before = self._collection.count()
        # Chroma 按 where 条件批量删除
        self._collection.delete(where={"doc_id": doc_id})
        after = self._collection.count()
        removed = before - after
        logger.info("vector_doc_removed", doc_id=doc_id, removed=removed)
        return removed

    def clear(self) -> None:
        """清空 collection（重建）。"""
        self._ensure_collection()
        self._client.delete_collection(self._collection_name)
        self._collection = self._client.get_or_create_collection(
            name=self._collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info("vector_store_cleared")

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
        # Chroma 查询
        query_params: dict[str, Any] = {
            "query_embeddings": [query_vec],
            "n_results": top_k,
        }
        if where:
            query_params["where"] = where

        raw = self._collection.query(**query_params)

        results: list[RetrievalResult] = []
        # Chroma 返回结构：{ids: [[...]], documents: [[...]], metadatas: [[...]], distances: [[...]]}
        ids_batch = raw.get("ids", [[]])
        docs_batch = raw.get("documents", [[]])
        meta_batch = raw.get("metadatas", [[]])
        dist_batch = raw.get("distances", [[]])

        if not ids_batch:
            return results

        for i, chunk_id in enumerate(ids_batch[0]):
            text = docs_batch[0][i] if i < len(docs_batch[0]) else ""
            meta = meta_batch[0][i] if i < len(meta_batch[0]) else {}
            dist = dist_batch[0][i] if i < len(dist_batch[0]) else 0.0
            # Chroma 余弦距离越小越相似，转换为得分（1 - distance）
            score = max(0.0, 1.0 - float(dist))
            results.append(
                RetrievalResult(
                    chunk_id=chunk_id,
                    doc_id=meta.get("doc_id", ""),
                    text=text,
                    score=score,
                    metadata=meta,
                    source="vector",
                )
            )
        return results
