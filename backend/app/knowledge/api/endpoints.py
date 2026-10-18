"""知识库管理 API：鉴权 multipart 上传/删除/统计/检索（owner 隔离）。

端点设计：
    POST   /api/v1/knowledge/documents        上传文档（multipart file + title）
    GET    /api/v1/knowledge/documents        列出当前用户文档
    DELETE /api/v1/knowledge/documents/{doc_id} 删除当前用户文档
    GET    /api/v1/knowledge/stats            当前用户索引统计
    POST   /api/v1/knowledge/search           当前用户检索

设计要点：
- 全部端点经 get_current_active_user 鉴权，业务调用一律传 current_user.id
- 跨用户访问返回 404（与不存在同语义，避免泄漏资源存在性）
- 上传为 multipart file，不再接受服务端 file_path 或裸 text（P0 路径读取修复）
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user
from app.db.models.knowledge import KnowledgeDoc
from app.db.models.user import User
from app.db.session import get_db
from app.knowledge.models.document import ChunkStrategy
from app.knowledge.services.document_service import KnowledgeDocumentService
from app.knowledge.services.factory import get_index_manager, get_kb, get_storage
from app.schemas.knowledge import (
    KnowledgeDocumentList,
    KnowledgeDocumentOut,
    KnowledgeSearchIn,
    KnowledgeSearchItem,
    KnowledgeSearchOut,
    KnowledgeStatsOut,
)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


# ===== 依赖注入 =====


def get_document_service(db: AsyncSession = Depends(get_db)) -> KnowledgeDocumentService:
    """构造文档服务：数据库会话 + 全局 RAG 单例。"""
    return KnowledgeDocumentService(
        db,
        storage=get_storage(),
        index_manager=get_index_manager(),
        kb=get_kb(),
    )


def _to_out(doc: KnowledgeDoc) -> KnowledgeDocumentOut:
    """ORM → 响应体：附带列表页需要的来源、原始文件名与失败原因（截断、不带栈）。"""
    meta = doc.meta_info or {}
    reason = meta.get("error")
    return KnowledgeDocumentOut(
        id=doc.id,
        title=doc.title,
        doc_type=doc.doc_type,
        index_status=doc.index_status,
        chunk_count=doc.chunk_count,
        created_at=doc.created_at,
        source=doc.source,
        original_name=meta.get("original_name"),
        failure_reason=str(reason)[:200] if reason else None,
    )


# ===== 端点 =====


@router.post(
    "/documents",
    response_model=KnowledgeDocumentOut,
    status_code=201,
    summary="上传文档（multipart）",
)
async def upload_document(
    file: Annotated[UploadFile, File(description="pdf/docx/md/txt 文件，≤30MB")],
    title: Annotated[str, Form(min_length=1, max_length=256)],
    strategy: Annotated[ChunkStrategy, Form()] = ChunkStrategy.RECURSIVE,
    current_user: User = Depends(get_current_active_user),
    service: KnowledgeDocumentService = Depends(get_document_service),
) -> KnowledgeDocumentOut:
    """上传文档：受控存储 → 解析分块 → 双索引 → 持久化。"""
    doc = await service.ingest(current_user.id, file, title=title, strategy=strategy)
    return _to_out(doc)


@router.get(
    "/documents",
    response_model=KnowledgeDocumentList,
    summary="列出当前用户文档",
)
async def list_documents(
    current_user: User = Depends(get_current_active_user),
    service: KnowledgeDocumentService = Depends(get_document_service),
) -> KnowledgeDocumentList:
    """列出当前用户名下的全部文档。"""
    docs = await service.list(current_user.id)
    return KnowledgeDocumentList(documents=[_to_out(doc) for doc in docs])


@router.delete(
    "/documents/{doc_id}",
    response_model=KnowledgeDocumentOut,
    summary="删除当前用户文档",
)
async def delete_document(
    doc_id: UUID,
    current_user: User = Depends(get_current_active_user),
    service: KnowledgeDocumentService = Depends(get_document_service),
) -> KnowledgeDocumentOut:
    """删除文档；跨用户或不存在的 doc_id 返回 404。"""
    doc = await service.delete(current_user.id, doc_id)
    return _to_out(doc)


@router.get(
    "/stats",
    response_model=KnowledgeStatsOut,
    summary="当前用户索引统计",
)
async def get_stats(
    current_user: User = Depends(get_current_active_user),
    service: KnowledgeDocumentService = Depends(get_document_service),
) -> KnowledgeStatsOut:
    """统计当前用户的文档数与分块数。"""
    total_docs, total_chunks = await service.stats(current_user.id)
    return KnowledgeStatsOut(
        total_docs=total_docs,
        total_chunks=total_chunks,
        vector_index_ready=True,
        bm25_index_ready=True,
    )


@router.post(
    "/search",
    response_model=KnowledgeSearchOut,
    summary="当前用户知识库检索",
)
async def search(
    req: KnowledgeSearchIn,
    current_user: User = Depends(get_current_active_user),
    service: KnowledgeDocumentService = Depends(get_document_service),
) -> KnowledgeSearchOut:
    """检索当前用户的知识库（强制 user_id 过滤）。"""
    results = await service.search(current_user.id, req)
    return KnowledgeSearchOut(
        query=req.query,
        total=len(results),
        results=[
            KnowledgeSearchItem(
                chunk_id=r.chunk_id,
                doc_id=r.doc_id,
                text=r.text,
                score=r.score,
                source=r.source,
            )
            for r in results
        ],
    )
