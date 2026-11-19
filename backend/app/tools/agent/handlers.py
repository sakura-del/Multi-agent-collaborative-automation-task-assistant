"""12 个工具的处理器（全部复用既有 service 层）。

分组：任务 6 / 引用库 2 / 知识库 2 / 配额 1 / 搜索 1。

## 输出约定

- 只返回**可 JSON 序列化**的紧凑结构（模型要读，不是给人看的全套 ORM）；
- 长文本一律截断（正文 8000 字、知识片段 600 字）——一次工具结果打穿上下文
  比「信息少一点」严重得多；
- 失败抛 `AppException` 子类（域名可读文案），不返回 `{"error": ...}`：
  「失败」必须能被调用方用状态码区分，否则模型会把错误当结果继续编。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.core.exceptions import UpstreamError, ValidationException
from app.knowledge.services.document_service import KnowledgeDocumentService
from app.knowledge.services.factory import get_index_manager, get_kb, get_storage
from app.knowledge.services.reference_library_service import ReferenceLibraryService
from app.orchestration.contracts import STATUS_GROUPS, TaskCommand, TaskControlRequest
from app.orchestration.services.task_submission import submit_review_task
from app.schemas.knowledge import KnowledgeSearchIn
from app.schemas.task import TaskCreate
from app.tools.agent.context import ToolContext
from app.tools.agent.spec import ToolValidationException
from app.tools.csl.importers import parse_bibtex, parse_ris
from app.tools.search import get_search_provider

# ===== 参数模型（JSON Schema 由 Pydantic 生成，永远与校验一致）=====


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListTasksArgs(_Args):
    limit: int = Field(10, ge=1, le=50, description="返回条数")
    status_group: Literal["all", "needs_me", "active", "done"] = "all"
    query: str | None = Field(None, max_length=100, description="标题关键词")


class TaskIdArgs(_Args):
    task_id: UUID


class GetTaskArgs(TaskIdArgs):
    include_output: bool = Field(False, description="是否带上正文（截断 8000 字）")


class CreateTaskArgs(_Args):
    topic: str = Field(min_length=5, max_length=200, description="研究主题")
    keywords: list[str] = Field(
        default_factory=list, max_length=10, description="检索关键词；空则取主题前 30 字"
    )
    max_documents: int = Field(20, ge=1, le=50)
    offline_dataset: bool = Field(False, description="用离线数据集（不外部检索）")
    reference_library: bool = Field(False, description="只用我的引用库作为文献源")
    citation_style: str = Field("gbt7714", min_length=1, max_length=64)


class ApproveTaskArgs(TaskIdArgs):
    revision: int | None = Field(None, ge=1, description="规划版本；不传 = 用当前 plan_version")


class RetryNodeArgs(TaskIdArgs):
    node_id: str = Field(min_length=1, max_length=64, description="要重试的 DAG 节点 id")


class SearchReferencesArgs(_Args):
    query: str | None = Field(None, max_length=200, description="标题/作者/DOI 关键词")
    limit: int = Field(10, ge=1, le=30)


class ImportReferencesArgs(_Args):
    content: str = Field(min_length=1, max_length=200_000, description="BibTeX 或 RIS 文本")
    format: Literal["auto", "bibtex", "ris"] = "auto"


class SearchKnowledgeArgs(_Args):
    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(5, ge=1, le=20)


class ListDocumentsArgs(_Args):
    limit: int = Field(20, ge=1, le=50)


class GetQuotaArgs(_Args):
    pass


class SearchWebArgs(_Args):
    query: str = Field(min_length=2, max_length=300, description="联网搜索查询")


# ===== 辅助 =====


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _authors_text(payload: dict[str, Any]) -> str:
    authors = payload.get("author") or []
    parts: list[str] = []
    for author in authors:
        if isinstance(author, dict):
            name = " ".join(
                str(part) for part in (author.get("family"), author.get("given")) if part
            )
            if name:
                parts.append(name)
    return " ".join(parts)


# ===== 任务域（6）=====


async def list_tasks(ctx: ToolContext, args: ListTasksArgs) -> dict[str, Any]:
    statuses = STATUS_GROUPS[args.status_group]
    total = await ctx.execution.count_tasks(
        ctx.user_id, workspace_ids=ctx.workspace_ids, statuses=statuses, query=args.query
    )
    rows = await ctx.execution.list_tasks(
        ctx.user_id,
        limit=args.limit,
        workspace_ids=ctx.workspace_ids,
        statuses=statuses,
        query=args.query,
    )
    return {
        "total": total,
        "count": len(rows),
        "tasks": [
            {
                "task_id": str(row.task_id),
                "title": row.title,
                "status": row.status,
                "plan_version": row.plan_version,
                "updated_at": _iso(row.updated_at),
            }
            for row in rows
        ],
    }


async def get_task(ctx: ToolContext, args: GetTaskArgs) -> dict[str, Any]:
    snapshot = await ctx.execution.get_snapshot(
        ctx.user_id, args.task_id, workspace_ids=ctx.workspace_ids
    )
    failed = [item for item in snapshot.subtasks if item.status == "failed"]
    result: dict[str, Any] = {
        "task_id": str(snapshot.task_id),
        "title": snapshot.title,
        "status": snapshot.status,
        "plan_version": snapshot.plan_version,
        "revision": snapshot.revision,
        "progress": {
            "total": len(snapshot.subtasks),
            "completed": sum(1 for item in snapshot.subtasks if item.status == "completed"),
            "failed": len(failed),
        },
        "errors": [
            {"node_id": item.node_id, "title": item.title, "error": item.error}
            for item in failed[:5]
        ],
        "has_output": bool(snapshot.final_output),
    }
    if args.include_output and snapshot.final_output:
        result["output_markdown"] = snapshot.final_output[:8000]
    return result


async def create_task(ctx: ToolContext, args: CreateTaskArgs) -> dict[str, Any]:
    payload = TaskCreate(
        topic=args.topic,
        keywords=args.keywords or [args.topic[:30]],
        max_documents=args.max_documents,
        offline_dataset=args.offline_dataset,
        reference_library=args.reference_library,
        citation_style=args.citation_style,
    )
    task_id = await submit_review_task(
        db=ctx.db,
        user_id=ctx.user_id,
        payload=payload,
        execution=ctx.execution,
        plan_service=ctx.plan_service,
        quota=ctx.quota,
    )
    return {
        "task_id": str(task_id),
        "status": "planning",
        "next_step": "规划生成后需确认（approve_task_plan）才会开始执行",
    }


async def approve_task_plan(ctx: ToolContext, args: ApproveTaskArgs) -> dict[str, Any]:
    snapshot = await ctx.execution.get_snapshot(
        ctx.user_id, args.task_id, workspace_ids=ctx.workspace_ids
    )
    revision = args.revision if args.revision is not None else snapshot.plan_version
    updated = await ctx.execution.approve_plan(ctx.user_id, args.task_id, revision)
    return {
        "task_id": str(updated.task_id),
        "status": updated.status,
        "plan_version": updated.plan_version,
    }


async def cancel_task(ctx: ToolContext, args: TaskIdArgs) -> dict[str, Any]:
    updated = await ctx.execution.control(
        ctx.user_id, args.task_id, TaskControlRequest(command=TaskCommand.CANCEL)
    )
    return {"task_id": str(updated.task_id), "status": updated.status}


async def retry_task_node(ctx: ToolContext, args: RetryNodeArgs) -> dict[str, Any]:
    updated = await ctx.execution.control(
        ctx.user_id,
        args.task_id,
        TaskControlRequest(command=TaskCommand.RETRY_NODE, node_id=args.node_id),
    )
    return {
        "task_id": str(updated.task_id),
        "status": updated.status,
        "node_id": args.node_id,
    }


# ===== 引用库域（2）=====


async def search_references(ctx: ToolContext, args: SearchReferencesArgs) -> dict[str, Any]:
    rows = await ReferenceLibraryService(ctx.db).list_items(ctx.user_id, limit=200)
    needle = (args.query or "").strip().lower()
    matched = []
    for row in rows:
        haystack = " ".join(
            filter(
                None,
                [row.title, row.doi or "", str(row.issued_year or ""), _authors_text(row.payload)],
            )
        ).lower()
        if needle and needle not in haystack:
            continue
        matched.append(
            {
                "item_id": str(row.id),
                "ref_id": row.ref_id,
                "title": row.title,
                "doi": row.doi,
                "year": row.issued_year,
            }
        )
        if len(matched) >= args.limit:
            break
    return {"query": args.query, "total_in_library": len(rows), "items": matched}


async def import_references(ctx: ToolContext, args: ImportReferencesArgs) -> dict[str, Any]:
    fmt = args.format
    if fmt == "auto":
        fmt = "ris" if re.search(r"^TY\s{0,3}-", args.content, re.MULTILINE) else "bibtex"
    try:
        items = parse_ris(args.content) if fmt == "ris" else parse_bibtex(args.content)
    except Exception as exc:
        message = exc.message if isinstance(exc, ValidationException) else str(exc)
        # 统一成 TOOL_ARGUMENTS_INVALID：调用方（模型/前端）应按「参数问题」处理，
        # 而不是把它当成服务端故障去重试。
        raise ToolValidationException(f"{fmt.upper()} 解析失败：{message}") from exc
    if not items:
        raise ToolValidationException(
            f"未解析到任何文献条目；请检查内容格式是否为 {fmt.upper()}",
            details={"format": fmt},
        )
    saved = await ReferenceLibraryService(ctx.db).save(ctx.user_id, items)
    return {
        "format": fmt,
        "parsed": len(items),
        "created": saved.created,
        "merged": saved.merged,
        "skipped": saved.skipped,
        "rejected": len(saved.rejected),
        "rejected_details": [
            {"ref_id": item.ref_id, "title": item.title, "reason": item.reason}
            for item in saved.rejected[:5]
        ],
    }


# ===== 知识库域（2）=====


def _knowledge_service(ctx: ToolContext) -> KnowledgeDocumentService:
    return KnowledgeDocumentService(
        ctx.db,
        storage=get_storage(),
        index_manager=get_index_manager(),
        kb=get_kb(),
    )


async def search_knowledge(ctx: ToolContext, args: SearchKnowledgeArgs) -> dict[str, Any]:
    try:
        results = await _knowledge_service(ctx).search(
            ctx.user_id, KnowledgeSearchIn(query=args.query, top_k=args.top_k)
        )
    except Exception as exc:
        raise UpstreamError(f"知识库检索暂不可用：{exc}") from exc
    return {
        "query": args.query,
        "count": len(results),
        "chunks": [
            {
                "chunk_id": item.chunk_id,
                "doc_id": item.doc_id,
                "score": round(float(item.score), 4),
                "text": item.text[:600],
                "source": item.source,
            }
            for item in results
        ],
    }


async def list_knowledge_documents(ctx: ToolContext, args: ListDocumentsArgs) -> dict[str, Any]:
    try:
        docs = await _knowledge_service(ctx).list_documents(ctx.user_id)
    except Exception as exc:
        raise UpstreamError(f"知识库暂不可用：{exc}") from exc
    total = len(docs)
    return {
        "total": total,
        "documents": [
            {
                "doc_id": str(doc.id),
                "title": doc.title,
                "doc_type": doc.doc_type,
                "index_status": doc.index_status,
                "chunk_count": getattr(doc, "chunk_count", None),
            }
            for doc in docs[: args.limit]
        ],
    }


# ===== 平台域（2）=====


async def get_quota(ctx: ToolContext, args: GetQuotaArgs) -> dict[str, Any]:
    del args
    usage = await ctx.plan_service.get_usage(ctx.user_id)
    period_end = usage.get("period_end")
    return {
        "plan": usage.get("plan_name"),
        "period_key": usage.get("period_key"),
        "reviews_used": usage.get("reviews_used"),
        "monthly_reviews": usage.get("monthly_reviews"),
        "reviews_remaining": usage.get("reviews_remaining"),
        "period_end": period_end.isoformat() if isinstance(period_end, datetime) else period_end,
    }


async def search_web(ctx: ToolContext, args: SearchWebArgs) -> dict[str, Any]:
    # 与 `GET /api/v1/search/web` 同一 provider（Tavily，basic 深度）
    provider = get_search_provider()
    results = await provider.search(args.query)
    return {
        "provider": provider.name,
        "query": args.query,
        "count": len(results),
        "results": [
            {
                "title": item.title,
                "url": item.url,
                "snippet": item.snippet,
                "score": item.score,
            }
            for item in results
        ],
    }
