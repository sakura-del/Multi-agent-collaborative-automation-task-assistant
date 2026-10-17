"""知识文档服务：上传持久化与 RAG 索引的事务编排。

职责（事务所有者）：
- ingest：受控存储 → 创建 KnowledgeDoc(indexing) → 解析/分块 → 写双索引
  → 持久化 DocChunk/VectorIndex → index_status=ready，单事务提交
- delete：owner 校验后同步移除双索引、物理文件与 ORM 行
- list / stats / search：owner-scoped 查询（检索强制 user_id 过滤）
- rebuild_bm25：从持久化的 ready 分块重建 BM25 内存索引（启动恢复）

失败语义：索引阶段任一环节失败 → 移除部分索引与临时文件 →
文档标记 failed → 提交 → 抛 ValidationException（数据库保留失败记录供排查）。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import NotFoundException, ValidationException
from app.core.logging import get_logger
from app.db.models.knowledge import DocChunk, KnowledgeDoc, VectorIndex
from app.db.repositories.knowledge import KnowledgeRepository
from app.knowledge.models.document import ChunkStrategy, RetrievalResult, TextChunk
from app.knowledge.services.document_storage import DocumentStorage
from app.knowledge.services.index_manager import IndexManager
from app.knowledge.services.knowledge_base import KnowledgeBase
from app.schemas.knowledge import KnowledgeSearchIn

logger = get_logger(__name__)

# 存储扩展名 → ORM doc_type 映射
_EXT_TO_DOC_TYPE: dict[str, str] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "txt",
}


class KnowledgeDocumentService:
    """知识文档事务编排服务。"""

    def __init__(
        self,
        db: AsyncSession,
        *,
        storage: DocumentStorage,
        index_manager: IndexManager,
        kb: KnowledgeBase,
    ) -> None:
        self._db = db
        self._repo = KnowledgeRepository(db)
        self._storage = storage
        self._mgr = index_manager
        self._kb = kb
        settings = get_settings()
        self._embedding_model = settings.rag.embedding_model

    async def ingest(
        self,
        user_id: UUID,
        upload: UploadFile,
        *,
        title: str,
        strategy: ChunkStrategy,
    ) -> KnowledgeDoc:
        """上传并索引文档：存储 → 建索引 → 持久化，失败回滚为 failed 记录。"""
        # 1. 受控存储（路径穿越/签名/大小校验）
        stored = await self._storage.save(user_id, upload)
        doc_type = _EXT_TO_DOC_TYPE.get(stored.extension, "txt")
        doc_id_str = str(stored.doc_uuid)

        # 2. 创建索引中的文档记录
        doc = KnowledgeDoc(
            id=stored.doc_uuid,
            user_id=user_id,
            title=title,
            source="upload",
            doc_type=doc_type,
            file_path=str(stored.path),
            index_status="indexing",
            chunk_count=0,
            meta_info={"original_name": stored.original_name, "file_size": stored.size},
        )
        self._db.add(doc)
        await self._db.flush()

        # 3. 解析 + 分块 + 写双索引（CPU/模型密集，放线程池避免阻塞事件循环）
        try:
            _, chunks = await asyncio.to_thread(
                self._mgr.prepare_document,
                str(stored.path),
                doc_id=doc_id_str,
                strategy=strategy,
                metadata={"user_id": str(user_id)},
            )
            if not chunks:
                raise ValidationException("未能从文档中提取出文本内容")
            await asyncio.to_thread(self._mgr.index_chunks, chunks)

            # 4. 持久化分块行
            chunk_rows: list[DocChunk] = []
            for chunk in chunks:
                row = DocChunk(
                    doc_id=doc.id,
                    chunk_index=chunk.metadata.get("chunk_index", 0),
                    content=chunk.text,
                    char_count=len(chunk.text),
                )
                self._db.add(row)
                chunk_rows.append(row)
            await self._db.flush()

            # 5. 持久化向量索引条目（与 Chroma 中的向量一一对应）
            for chunk, row in zip(chunks, chunk_rows, strict=True):
                self._db.add(
                    VectorIndex(
                        chunk_id=row.id,
                        doc_id=doc.id,
                        collection_name=self._mgr.collection_name,
                        chroma_id=chunk.chunk_id,
                        embedding_model=self._embedding_model,
                        vector_dim=self._mgr.embedding_dimension,
                        has_bm25_index=True,
                        indexed_at=datetime.now(UTC),
                    )
                )

            # 6. 索引完成
            doc.index_status = "ready"
            doc.chunk_count = len(chunks)
            await self._db.commit()
            await self._db.refresh(doc)
            logger.info(
                "knowledge_doc_ingested",
                doc_id=doc_id_str,
                user_id=str(user_id),
                chunks=len(chunks),
            )
            return doc
        except Exception as exc:
            # 失败回滚：移除部分索引与临时文件，保留 failed 记录
            await asyncio.to_thread(self._mgr.remove_document, doc_id_str)
            stored.path.unlink(missing_ok=True)
            await self._db.rollback()
            failed = KnowledgeDoc(
                id=stored.doc_uuid,
                user_id=user_id,
                title=title,
                source="upload",
                doc_type=doc_type,
                file_path=None,
                index_status="failed",
                chunk_count=0,
                meta_info={"original_name": stored.original_name, "error": str(exc)},
            )
            self._db.add(failed)
            await self._db.commit()
            logger.error("knowledge_doc_ingest_failed", doc_id=doc_id_str, error=str(exc))
            if isinstance(exc, ValidationException):
                raise
            raise ValidationException(f"文档索引失败：{exc}") from exc

    async def delete(self, user_id: UUID, doc_id: UUID) -> KnowledgeDoc:
        """删除文档：owner 校验 → 移除双索引 → 删物理文件 → 删 ORM 行。"""
        doc = await self._repo.get_for_user(user_id, doc_id)
        if doc is None:
            raise NotFoundException("文档不存在")

        removed = await asyncio.to_thread(self._mgr.remove_document, str(doc.id))
        if doc.file_path:
            self._storage.delete(user_id, doc.file_path)

        await self._repo.delete(doc)
        await self._db.commit()
        # 只失效该用户的检索缓存，不影响其他用户
        self._kb.invalidate_cache(str(user_id))
        logger.info(
            "knowledge_doc_deleted", doc_id=str(doc.id), user_id=str(user_id), removed=removed
        )
        return doc

    async def list(self, user_id: UUID) -> list[KnowledgeDoc]:
        """列出用户文档。"""
        return await self._repo.list_for_user(user_id)

    async def stats(self, user_id: UUID) -> tuple[int, int]:
        """统计用户文档数与分块数。"""
        return await self._repo.stats_for_user(user_id)

    async def search(self, user_id: UUID, request: KnowledgeSearchIn) -> list[RetrievalResult]:
        """检索：强制 user_id 过滤 + namespace 缓存隔离。"""
        return self._kb.query(
            request.query,
            top_k=request.top_k,
            where={"user_id": str(user_id)},
            namespace=str(user_id),
            skip_cache=request.skip_cache,
            skip_rerank=request.skip_rerank,
        )

    async def search_for_context(
        self,
        user_id: UUID,
        query: str,
        *,
        top_k: int = 8,
    ) -> list[RetrievalResult]:
        """证据上下文检索入口（Plan 3 Task 2）：与 search 同一 owner 隔离语义。"""
        return await self.search(
            user_id,
            KnowledgeSearchIn(query=query, top_k=top_k),
        )

    async def rebuild_bm25(self) -> int:
        """从持久化的 ready 分块重建 BM25 内存索引（启动恢复场景）。"""
        rows = (
            await self._db.execute(
                select(DocChunk, KnowledgeDoc.user_id)
                .join(KnowledgeDoc, KnowledgeDoc.id == DocChunk.doc_id)
                .where(KnowledgeDoc.index_status == "ready")
            )
        ).all()
        chunks: list[TextChunk] = []
        for chunk, user_id in rows:
            chunks.append(
                TextChunk(
                    chunk_id=str(chunk.id),
                    doc_id=str(chunk.doc_id),
                    text=chunk.content,
                    metadata={"user_id": str(user_id), "chunk_index": chunk.chunk_index},
                )
            )
        count = await asyncio.to_thread(self._mgr.rebuild_bm25_from_chunks, chunks)
        logger.info("bm25_rebuilt", chunks=count)
        return count
