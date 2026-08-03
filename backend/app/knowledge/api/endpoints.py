"""知识库管理 API：文档上传/删除/重建/检索测试。

端点设计：
    POST   /api/v1/knowledge/documents        上传文档（文本或文件路径）
    DELETE /api/v1/knowledge/documents/{doc_id} 删除文档
    POST   /api/v1/knowledge/documents/rebuild  全量重建索引
    GET    /api/v1/knowledge/stats              索引统计
    POST   /api/v1/knowledge/search             检索测试

设计要点：
- 依赖注入：通过 get_knowledge_base 提供单例，便于测试替换
- 轻量封装：API 层仅做参数校验与结果序列化，业务逻辑在服务层
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.knowledge.models.document import ChunkStrategy
from app.knowledge.services.knowledge_base import KnowledgeBase

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


# ===== 请求/响应模型 =====


class DocumentUploadRequest(BaseModel):
    """文档上传请求：文本内容或文件路径。"""

    text: str | None = None  # 直接上传文本
    file_path: str | None = None  # 上传文件路径
    doc_id: str | None = None
    strategy: str = "recursive"  # recursive / semantic / sliding_window


class SearchRequest(BaseModel):
    """检索测试请求。"""

    query: str
    top_k: int = 5
    skip_cache: bool = False
    skip_rerank: bool = False


class SearchResponseItem(BaseModel):
    """单条检索结果。"""

    chunk_id: str
    doc_id: str
    text: str
    score: float
    source: str


class SearchResponse(BaseModel):
    """检索测试响应。"""

    query: str
    total: int
    results: list[SearchResponseItem]


class StatsResponse(BaseModel):
    """索引统计响应。"""

    total_docs: int
    total_chunks: int
    vector_index_ready: bool
    bm25_index_ready: bool
    last_update: str | None = None


class DocumentListResponse(BaseModel):
    """文档列表响应。"""

    documents: list[dict[str, Any]]


# ===== 依赖注入 =====


def get_knowledge_base() -> KnowledgeBase:
    """获取知识库单例。

    生产环境由应用启动时初始化并注入；
    此处提供懒加载回退，确保 API 可独立调用。
    """
    from app.knowledge.services.factory import get_kb

    return get_kb()


# ===== 端点 =====


@router.post("/documents", response_model=dict)
async def upload_document(
    req: DocumentUploadRequest,
    kb: KnowledgeBase = Depends(get_knowledge_base),
) -> dict[str, Any]:
    """上传文档到知识库：自动解析→分块→索引。"""
    if not req.text and not req.file_path:
        raise HTTPException(status_code=400, detail="需提供 text 或 file_path")

    from app.knowledge.services.factory import get_index_manager

    mgr = get_index_manager()
    try:
        strategy = ChunkStrategy(req.strategy)
    except ValueError:
        strategy = ChunkStrategy.RECURSIVE

    if req.text:
        document = mgr.add_text(req.text, doc_id=req.doc_id, strategy=strategy)
    else:
        document = mgr.add_document(req.file_path, doc_id=req.doc_id, strategy=strategy)

    # 文档更新后清空检索缓存
    kb.invalidate_cache()
    return {
        "doc_id": document.doc_id,
        "doc_type": document.doc_type.value,
        "text_length": len(document.text),
        "sections": len(document.sections),
    }


@router.delete("/documents/{doc_id}", response_model=dict)
async def delete_document(
    doc_id: str,
    kb: KnowledgeBase = Depends(get_knowledge_base),
) -> dict[str, Any]:
    """从知识库删除指定文档。"""
    from app.knowledge.services.factory import get_index_manager

    mgr = get_index_manager()
    removed = mgr.remove_document(doc_id)
    if removed == 0:
        raise HTTPException(status_code=404, detail=f"文档 {doc_id} 不存在")
    kb.invalidate_cache()
    return {"doc_id": doc_id, "removed_chunks": removed}


@router.post("/documents/rebuild", response_model=dict)
async def rebuild_index(
    kb: KnowledgeBase = Depends(get_knowledge_base),
) -> dict[str, Any]:
    """清空并全量重建索引（需重新上传文档）。"""
    from app.knowledge.services.factory import get_index_manager

    mgr = get_index_manager()
    mgr.clear()
    kb.invalidate_cache()
    return {"status": "index_cleared", "message": "索引已清空，请重新上传文档"}


@router.get("/stats", response_model=StatsResponse)
async def get_stats(
    kb: KnowledgeBase = Depends(get_knowledge_base),
) -> StatsResponse:
    """获取索引统计信息。"""
    from app.knowledge.services.factory import get_index_manager

    mgr = get_index_manager()
    stats = mgr.get_stats()
    return StatsResponse(
        total_docs=stats.total_docs,
        total_chunks=stats.total_chunks,
        vector_index_ready=stats.vector_index_ready,
        bm25_index_ready=stats.bm25_index_ready,
        last_update=stats.last_update,
    )


@router.get("/documents", response_model=DocumentListResponse)
async def list_documents(
    kb: KnowledgeBase = Depends(get_knowledge_base),
) -> DocumentListResponse:
    """列出已入库文档。"""
    from app.knowledge.services.factory import get_index_manager

    mgr = get_index_manager()
    return DocumentListResponse(documents=mgr.list_documents())


@router.post("/search", response_model=SearchResponse)
async def search(
    req: SearchRequest,
    kb: KnowledgeBase = Depends(get_knowledge_base),
) -> SearchResponse:
    """检索测试：返回与查询最相关的文档片段。"""
    results = kb.query(
        req.query,
        top_k=req.top_k,
        skip_cache=req.skip_cache,
        skip_rerank=req.skip_rerank,
    )
    return SearchResponse(
        query=req.query,
        total=len(results),
        results=[
            SearchResponseItem(
                chunk_id=r.chunk_id,
                doc_id=r.doc_id,
                text=r.text,
                score=r.score,
                source=r.source,
            )
            for r in results
        ],
    )
