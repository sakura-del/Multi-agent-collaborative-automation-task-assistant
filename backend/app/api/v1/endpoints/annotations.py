"""导师批注端点（Phase 2 · 团队工作区 Task 4）。

## 路由设计

挂在任务下（`/tasks/{task_id}/annotations`），因为批注的生命周期完全依附任务：
任务删除 → 批注级联删除（外键 `ondelete="CASCADE"`）。

## 错误映射（与既有口径一致）

| 服务层异常 | HTTP | 说明 |
| --- | --- | --- |
| `AnnotationError("任务不存在")` | **404** | 非成员/非 owner 一律「不存在」，不泄漏存在性 |
| `AnnotationError(其他)` | **400** | 内容为空、锚点类型非法等 |
| `AnnotationPermissionError` | **403** | 成员但角色不足（如 member 删他人批注） |

## 为什么单条批注操作也带 `task_id`

`PATCH /tasks/{task_id}/annotations/{annotation_id}` 里的 `task_id` 不只是装饰：
它在服务层参与可见性校验，防止「拿到别人工作区的 annotation_id 就能改」。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user
from app.collaboration.services.annotation_service import (
    AnnotationError,
    AnnotationPermissionError,
    AnnotationService,
)
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.annotation import (
    AnnotationCreate,
    AnnotationOut,
    AnnotationUpdate,
    ParagraphMatchOut,
)

router = APIRouter(prefix="/tasks/{task_id}/annotations", tags=["annotations"])

CurrentUserDep = Annotated[User, Depends(get_current_active_user)]
DbDep = Annotated[AsyncSession, Depends(get_db)]


def _service(db: AsyncSession) -> AnnotationService:
    """构造服务（复用请求级 session 的 sessionmaker 语义）。

    `AnnotationService` 需要的是 `session_factory`，而端点拿到的是 session。
    这里用「单例 session 工厂」适配：每次调用返回同一个 session，
    由 FastAPI 的 `get_db` 负责关闭。
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _factory():  # type: ignore[no-untyped-def]
        yield db

    return AnnotationService(_factory)  # type: ignore[arg-type]


def _to_out(annotation, author_name: str | None = None) -> AnnotationOut:  # type: ignore[no-untyped-def]
    return AnnotationOut(
        annotation_id=annotation.id,
        task_id=annotation.task_id,
        author_id=annotation.author_id,
        author_name=author_name,
        anchor_type=annotation.anchor_type,
        anchor_key=annotation.anchor_key,
        content=annotation.content,
        resolved_at=annotation.resolved_at,
        created_at=annotation.created_at,
    )


@router.get("", response_model=list[AnnotationOut], summary="列出任务批注")
async def list_annotations(
    task_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> list[AnnotationOut]:
    """列出任务全部批注（含已解决，按创建时间正序）。"""
    service = _service(db)
    try:
        items = await service.list_for_task(current_user.id, task_id)
    except AnnotationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [_to_out(item) for item in items]


@router.post("", response_model=AnnotationOut, status_code=201, summary="新建批注")
async def create_annotation(
    task_id: UUID, payload: AnnotationCreate, current_user: CurrentUserDep, db: DbDep
) -> AnnotationOut:
    """新建批注。

    `anchor_type="paragraph"` 时传 `anchor_text`（段落原文），服务端算指纹；
    `anchor_type="task"` 时两者都不传（任务级批注）。
    """
    service = _service(db)
    try:
        annotation = await service.create(
            current_user.id,
            task_id,
            anchor_type=payload.anchor_type,
            content=payload.content,
            anchor_text=payload.anchor_text,
            anchor_key=payload.anchor_key,
        )
    except AnnotationError as exc:
        message = str(exc)
        status = 404 if "不存在" in message else 400
        raise HTTPException(status_code=status, detail=message) from exc
    return _to_out(annotation)


@router.get(
    "/paragraph-matches",
    response_model=list[ParagraphMatchOut],
    summary="段落批注锚点匹配状态",
)
async def paragraph_matches(
    task_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> list[ParagraphMatchOut]:
    """把段落级批注与当前产出匹配。

    `matched=false` 表示**原文已变更**（任务重跑后段落被改写）——
    前端必须显示这个状态，不能把批注挂到别的段落上。
    """
    service = _service(db)
    try:
        rows = await service.match_paragraphs(current_user.id, task_id)
    except AnnotationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [ParagraphMatchOut(**row) for row in rows]


@router.patch("/{annotation_id}", response_model=AnnotationOut, summary="修改批注正文")
async def update_annotation(
    task_id: UUID,
    annotation_id: UUID,
    payload: AnnotationUpdate,
    current_user: CurrentUserDep,
    db: DbDep,
) -> AnnotationOut:
    """改正文（仅作者本人；admin 也不改别人的话——那是篡改）。"""
    service = _service(db)
    try:
        # 先校验 task_id 与批注归属（防止跨任务改）
        annotation = await service.update_content(
            current_user.id, annotation_id, content=payload.content
        )
        if annotation.task_id != task_id:
            raise HTTPException(status_code=404, detail="批注不存在")
    except AnnotationPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except AnnotationError as exc:
        message = str(exc)
        status = 404 if "不存在" in message else 400
        raise HTTPException(status_code=status, detail=message) from exc
    return _to_out(annotation)


@router.post("/{annotation_id}/resolve", response_model=AnnotationOut, summary="标记已解决")
async def resolve_annotation(
    task_id: UUID, annotation_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> AnnotationOut:
    """标记已解决（幂等；**不删除**批注——导师要能看到历史意见）。"""
    service = _service(db)
    try:
        annotation = await service.resolve(current_user.id, annotation_id)
        if annotation.task_id != task_id:
            raise HTTPException(status_code=404, detail="批注不存在")
    except AnnotationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _to_out(annotation)


@router.post("/{annotation_id}/reopen", response_model=AnnotationOut, summary="重新打开")
async def reopen_annotation(
    task_id: UUID, annotation_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> AnnotationOut:
    """重新打开批注（清空已解决标记）。"""
    service = _service(db)
    try:
        annotation = await service.reopen(current_user.id, annotation_id)
        if annotation.task_id != task_id:
            raise HTTPException(status_code=404, detail="批注不存在")
    except AnnotationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _to_out(annotation)


@router.delete("/{annotation_id}", status_code=204, summary="删除批注")
async def delete_annotation(
    task_id: UUID, annotation_id: UUID, current_user: CurrentUserDep, db: DbDep
) -> None:
    """删除批注（本人可删；他人批注需工作区 admin+）。"""
    service = _service(db)
    try:
        await service.delete(current_user.id, annotation_id)
    except AnnotationPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except AnnotationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
