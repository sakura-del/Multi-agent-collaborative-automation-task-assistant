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
import re
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
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

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def resolve_retrieval_ids(
    results: list[RetrievalResult],
    mapping: dict[str, tuple[str, str]],
) -> list[RetrievalResult]:
    """把检索结果的 chunk_id 解析为可回溯的 DocChunk/Doc UUID（Plan 3 证据链）。

    - 已经是 UUID 的结果（如 BM25 路径）原样保留；
    - 索引侧合成 id（Chroma/BGE 分块 id）按 `chroma_id → (chunk_id, doc_id)` 映射替换；
    - 映射缺失的结果直接丢弃：无法回溯到存储行的片段不能作为证据。
    """
    resolved: list[RetrievalResult] = []
    for item in results:
        if _UUID_RE.match(str(item.chunk_id)) and _UUID_RE.match(str(item.doc_id)):
            resolved.append(item)
            continue
        mapped = mapping.get(str(item.chunk_id))
        if mapped is None:
            continue
        resolved.append(replace(item, chunk_id=mapped[0], doc_id=mapped[1]))
    return resolved


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
            # as_posix()：S3 模式下 path 是对象键，Windows 的 `\` 会让 key 变形
            file_path=stored.path.as_posix(),
            index_status="indexing",
            chunk_count=0,
            meta_info={"original_name": stored.original_name, "file_size": stored.size},
        )
        self._db.add(doc)
        await self._db.flush()

        # 3. 解析 + 分块 + 写双索引（CPU/模型密集，放线程池避免阻塞事件循环）
        try:
            # 解析器只认本地文件：S3 后端在 localize 里下载到临时文件，退出即删除。
            # local 后端直接返回原路径，行为与改造前一致。
            async with self._storage.localize(stored.path.as_posix()) as local_path:
                _, chunks = await asyncio.to_thread(
                    self._mgr.prepare_document,
                    str(local_path),
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

    async def list_documents(self, user_id: UUID) -> list[KnowledgeDoc]:
        """列出用户名下文档。

        方法名不使用 `list`：它会遮蔽内置类型，使同类的 `-> list[...]` 注解
        被解析成方法引用（mypy `valid-type` 报错）。
        """
        """列出用户文档。"""
        return await self._repo.list_for_user(user_id)

    async def get_document(self, user_id: UUID, doc_id: UUID) -> KnowledgeDoc:
        """取单个文档（含归属校验）；不存在或不属于该用户一律 404 语义。"""
        doc = await self._repo.get_for_user(user_id, doc_id)
        if doc is None:
            raise NotFoundException("文档不存在")
        return doc

    async def stats(self, user_id: UUID) -> tuple[int, int]:
        """统计用户文档数与分块数。"""
        return await self._repo.stats_for_user(user_id)

    async def index_health(self, user_id: UUID) -> dict[str, Any]:
        """计算该用户知识库的**真实**索引健康状态（2026-10-23）。

        ## 为什么需要它

        `/knowledge/stats` 原先把两个 ready 标志硬编码为 `True`。那是把
        「未知」伪装成「就绪」—— 本会话已因同类谎言（数据库说向量就绪、
        Chroma 只有 4 条）导致向量检索静默失效，最后靠实验异常才追查到。

        现在一切来自可验证的事实：

        - `vector_index_ready`：向量索引行数 == 分块数，且分块数 > 0；
        - `bm25_index_ready`：进程内 BM25 索引确实就绪（查不到就报 False）。

        `index_failed_count` / `indexing_count` 供页面提示「哪些需要你处理」。
        """
        total_docs, total_chunks = await self._repo.stats_for_user(user_id)
        vector_rows = await self._repo.vector_rows_for_user(user_id)
        status_counts = await self._repo.index_status_counts_for_user(user_id)

        # 向量索引完整：有分块，且每个分块都有对应的向量索引行
        vector_ready = total_chunks > 0 and vector_rows == total_chunks

        return {
            "total_docs": total_docs,
            "total_chunks": total_chunks,
            "vector_index_ready": vector_ready,
            "bm25_index_ready": self._bm25_ready(),
            "vector_index_rows": vector_rows,
            "index_failed_count": status_counts.get("failed", 0),
            "indexing_count": status_counts.get("indexing", 0),
        }

    @staticmethod
    def _bm25_ready() -> bool:
        """进程内 BM25 索引是否**确实**就绪。

        取不到索引管理器（未初始化）时返回 False —— 「确认不了」不能显示成
        「就绪」，否则又回到硬编码 `True` 的老问题。
        """
        try:
            from app.knowledge.services.factory import get_index_manager

            return bool(get_index_manager().get_stats().bm25_index_ready)
        except Exception:  # 未初始化 / 实现差异：一律视为「无法确认」
            return False

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
        """证据上下文检索入口（Plan 3 Task 2）：与 search 同一 owner 隔离语义。

        检索后把索引侧 chunk_id 解析为存储行 UUID（`VectorIndex.chroma_id` 映射），
        保证每条证据都能回溯到 `DocChunk` / `KnowledgeDoc`。
        """
        results = await self.search(
            user_id,
            KnowledgeSearchIn(query=query, top_k=top_k),
        )
        return await self._attach_storage_ids(results)

    async def _attach_storage_ids(
        self,
        results: list[RetrievalResult],
    ) -> list[RetrievalResult]:
        """按 `VectorIndex.chroma_id` 映射把结果 id 换成存储层 UUID。"""
        pending = [item.chunk_id for item in results if not _UUID_RE.match(str(item.chunk_id))]
        if not pending:
            return results
        rows = (
            await self._db.execute(
                select(VectorIndex.chroma_id, VectorIndex.chunk_id, VectorIndex.doc_id).where(
                    VectorIndex.chroma_id.in_(pending)
                )
            )
        ).all()
        mapping = {
            str(chroma_id): (str(chunk_id), str(doc_id)) for chroma_id, chunk_id, doc_id in rows
        }
        return resolve_retrieval_ids(results, mapping)

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
