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

import asyncio
import mimetypes
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models.knowledge import KnowledgeDoc
from app.db.models.user import User
from app.db.session import get_db
from app.knowledge.models.document import ChunkStrategy
from app.knowledge.services.document_service import KnowledgeDocumentService
from app.knowledge.services.document_storage import DocumentStorage
from app.knowledge.services.factory import get_index_manager, get_kb, get_storage
from app.knowledge.services.vector_backends.base import VectorStoreUnavailableError
from app.schemas.knowledge import (
    KnowledgeDocumentList,
    KnowledgeDocumentOut,
    KnowledgeSearchIn,
    KnowledgeSearchItem,
    KnowledgeSearchOut,
    KnowledgeStatsOut,
)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

logger = get_logger(__name__)


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
    docs = await service.list_documents(current_user.id)
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
    "/documents/{doc_id}/download",
    response_model=None,
    summary="下载文档原文（S3 预签名直链 / 本地流式）",
)
async def download_document(
    doc_id: UUID,
    current_user: User = Depends(get_current_active_user),
    service: KnowledgeDocumentService = Depends(get_document_service),
    storage: DocumentStorage = Depends(get_storage),
) -> Response:
    """下载当前用户某篇文档的原文。

    - S3 后端：**307 跳转**到预签名直链，不占用应用服务器带宽；
    - 本地后端：`FileResponse` 流式返回，文件名取上传时的原名；
    - 跨用户 / 记录没有文件 / 本地路径越出上传根目录：一律 404
      （与「不存在」同语义，避免泄漏资源存在性）。
    """
    doc = await service.get_document(current_user.id, doc_id)
    if not doc.file_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在")

    # 预签名是同步 SDK 调用，放线程避免阻塞事件循环
    presigned = await asyncio.to_thread(storage.presigned_url, doc.file_path)
    if presigned:
        return RedirectResponse(presigned, status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    path = Path(doc.file_path).resolve()
    upload_root = Path(get_settings().app.upload_root).resolve()
    # 防越权读盘：历史脏数据里如果写了 /etc/passwd 之类的路径，这里必须挡住
    if upload_root not in path.parents or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在")

    original_name = (doc.meta_info or {}).get("original_name") or path.name
    media_type = mimetypes.guess_type(original_name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=original_name)


@router.get(
    "/stats",
    response_model=KnowledgeStatsOut,
    summary="当前用户索引统计",
)
async def get_stats(
    current_user: User = Depends(get_current_active_user),
    service: KnowledgeDocumentService = Depends(get_document_service),
) -> KnowledgeStatsOut:
    """统计当前用户的文档数、分块数与**真实**索引状态。

    ## 为什么不再是硬编码

    原先这里写死 `vector_index_ready=True, bm25_index_ready=True` ——
    无论真实索引状况如何都报「就绪」。本会话已踩过同类坑：数据库说
    28344 条向量就绪，Chroma 实际只有 4 条，向量检索对全体用户静默失效。

    现在由 `service.index_health()` 用可验证的事实计算（见其 docstring）。
    """
    health = await service.index_health(current_user.id)
    return KnowledgeStatsOut(**health)


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
    """检索当前用户的知识库（强制 user_id 过滤）。

    ## 必须调 `search_for_context()` 而不是 `search()`

    `search()` 直接返回索引层结果，`chunk_id` 是 **Chroma 内部 id**
    （形如 `9eac9177523794ed`，16 位 hex）—— 前端拿到它**点不回原文**。

    `search_for_context()` 内部走 `_attach_storage_ids()`，按
    `VectorIndex.chroma_id` 把索引 id 换成 `DocChunk.id` / `KnowledgeDoc.id`
    的 UUID，引用才能回溯。

    实测（2026-10-23 巡检）：走 `search()` 时接口返回的 chunk_id 是
    `9eac9177523794ed`，不是 UUID —— 「每条证据可点回原文」的核心能力断裂。
    """
    try:
        results = await service.search_for_context(current_user.id, req.query, top_k=req.top_k)
    except VectorStoreUnavailableError as exc:
        # 向量库不可达是**依赖故障**，不是服务端缺陷：503 让调用方/网关知道可重试，
        # 也避免前端把它展示成「查询语法错误」这类不可重试的提示。
        logger.warning("knowledge_search_vector_unavailable", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="向量库暂不可用，请稍后重试",
        ) from exc

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
