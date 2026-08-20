"""增量索引管理器：协调解析→分块→嵌入→入库的完整流水线。

职责：
- 文档入库：解析文档 → 分块 → 同时写入向量库与 BM25 索引
- 文档删除：从向量库与 BM25 索引同步移除
- 增量更新：新增文档自动触发增量索引，无需全量重建
- 索引重建：清空并全量重建（数据修正场景）

设计要点：
- 流水线封装：上层只需调用 add_document(source)，内部自动完成全流程
- 统计追踪：维护文档数、分块数、最后更新时间等统计信息
- 失败容错：单文档入库失败不影响其他文档，记录错误日志
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.logging import get_logger
from app.knowledge.models.document import (
    ChunkStrategy,
    IndexStats,
    ParsedDocument,
    TextChunk,
)
from app.knowledge.services.bm25_index import BM25Index
from app.knowledge.services.chunker import DocumentChunker
from app.knowledge.services.document_parser import DocumentParser
from app.knowledge.services.vector_store import VectorStore

logger = get_logger(__name__)


class IndexManager:
    """索引管理器：协调 RAG 索引的构建、更新与维护。

    用法：
        manager = IndexManager(parser, chunker, vector_store, bm25_index)
        manager.add_document("report.pdf")
        stats = manager.get_stats()
    """

    def __init__(
        self,
        parser: DocumentParser,
        chunker: DocumentChunker,
        vector_store: VectorStore,
        bm25_index: BM25Index,
        *,
        default_strategy: ChunkStrategy = ChunkStrategy.RECURSIVE,
    ) -> None:
        self._parser = parser
        self._chunker = chunker
        self._vector_store = vector_store
        self._bm25_index = bm25_index
        self._default_strategy = default_strategy
        # 已入库文档记录：doc_id → {chunks: int, source: str, time: str}
        self._docs: dict[str, dict[str, Any]] = {}

    def add_document(
        self,
        source: str,
        *,
        doc_id: str | None = None,
        strategy: ChunkStrategy | None = None,
    ) -> ParsedDocument:
        """文档入库：解析→分块→写入双索引。返回解析后的文档。"""
        # 1. 解析
        document = self._parser.parse(source, doc_id=doc_id)

        # 2. 分块
        chunks = self._chunker.chunk(document, strategy=strategy or self._default_strategy)
        if not chunks:
            logger.warning("document_no_chunks", doc_id=document.doc_id)
            return document

        # 3. 写入向量索引
        self._vector_store.add_chunks(chunks)

        # 4. 写入 BM25 索引
        self._bm25_index.add_chunks(chunks)

        # 5. 记录入库信息
        self._docs[document.doc_id] = {
            "source": source,
            "chunks": len(chunks),
            "time": datetime.now().isoformat(),
        }

        logger.info(
            "document_indexed",
            doc_id=document.doc_id,
            chunks=len(chunks),
            strategy=(strategy or self._default_strategy).value,
        )
        return document

    @property
    def collection_name(self) -> str:
        """Chroma collection 名称（供 VectorIndex 表落库）。"""
        return self._vector_store.collection_name

    @property
    def embedding_dimension(self) -> int:
        """当前嵌入模型向量维度（供 VectorIndex 表落库）。"""
        return self._vector_store.embedding_dimension

    def prepare_document(
        self,
        source: str,
        *,
        doc_id: str,
        strategy: ChunkStrategy,
        metadata: dict[str, Any],
    ) -> tuple[ParsedDocument, list[TextChunk]]:
        """解析 + 分块（不写索引）：供 service 层在自管事务中编排持久化。

        metadata 会合并进文档与每个分块（如 user_id），
        供 Chroma where 过滤与 BM25 元数据过滤使用。
        """
        document = self._parser.parse(source, doc_id=doc_id)
        document.metadata.update(metadata)
        chunks = self._chunker.chunk(document, strategy=strategy)
        return document, chunks

    def index_chunks(self, chunks: list[TextChunk]) -> None:
        """把分块写入向量与 BM25 双索引（不持久化 ORM 行）。"""
        if not chunks:
            return
        self._vector_store.add_chunks(chunks)
        self._bm25_index.add_chunks(chunks)
        for chunk in chunks:
            info = self._docs.setdefault(
                chunk.doc_id,
                {"source": "<service>", "chunks": 0, "time": datetime.now().isoformat()},
            )
            info["chunks"] += 1

    def rebuild_bm25_from_chunks(self, chunks: list[TextChunk]) -> int:
        """从持久化的分块重建 BM25 索引（启动恢复场景）。返回重建条数。"""
        self._bm25_index.clear()
        self._bm25_index.add_chunks(chunks)
        logger.info("bm25_rebuilt_from_db", chunks=len(chunks))
        return len(chunks)

    def add_text(
        self,
        text: str,
        *,
        doc_id: str | None = None,
        strategy: ChunkStrategy | None = None,
    ) -> ParsedDocument:
        """文本入库（便捷方法）：直接解析文本内容并索引。"""
        document = self._parser.parse_text(text, doc_id=doc_id)
        chunks = self._chunker.chunk(document, strategy=strategy or self._default_strategy)
        if chunks:
            self._vector_store.add_chunks(chunks)
            self._bm25_index.add_chunks(chunks)
            self._docs[document.doc_id] = {
                "source": "<raw_text>",
                "chunks": len(chunks),
                "time": datetime.now().isoformat(),
            }
        return document

    def remove_document(self, doc_id: str) -> int:
        """删除文档：从双索引同步移除。返回移除的分块数。"""
        vec_removed = self._vector_store.remove_doc(doc_id)
        bm25_removed = self._bm25_index.remove_doc(doc_id)
        self._docs.pop(doc_id, None)
        logger.info("document_removed", doc_id=doc_id, removed=max(vec_removed, bm25_removed))
        return max(vec_removed, bm25_removed)

    def rebuild_all(
        self,
        sources: list[str],
        *,
        strategy: ChunkStrategy | None = None,
    ) -> int:
        """全量重建：清空索引后重新入库所有文档。返回入库文档数。"""
        self.clear()
        count = 0
        for source in sources:
            try:
                self.add_document(source, strategy=strategy)
                count += 1
            except Exception as exc:
                logger.error("rebuild_doc_failed", source=source, error=str(exc))
        logger.info("index_rebuilt", docs=count)
        return count

    def clear(self) -> None:
        """清空全部索引。"""
        self._vector_store.clear()
        self._bm25_index.clear()
        self._docs.clear()

    def get_stats(self) -> IndexStats:
        """返回索引统计信息。"""
        last_update = None
        if self._docs:
            last_update = max(info["time"] for info in self._docs.values())
        return IndexStats(
            total_docs=len(self._docs),
            total_chunks=self._vector_store.size,
            vector_index_ready=self._vector_store.is_ready,
            bm25_index_ready=self._bm25_index.is_ready,
            last_update=last_update,
        )

    def list_documents(self) -> list[dict[str, Any]]:
        """列出已入库文档信息。"""
        return [{"doc_id": doc_id, **info} for doc_id, info in self._docs.items()]
