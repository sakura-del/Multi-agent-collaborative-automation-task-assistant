"""引用库 HTTP adapter（2026-10-26 引用库计划 §3.5）。

    GET    /api/v1/reference-library            列出（limit/offset）
    GET    /api/v1/reference-library/summary    摘要（总条数 / 可作文献源条数）
    POST   /api/v1/reference-library            保存（body: items[]，来自导入预览）
    DELETE /api/v1/reference-library/{item_id}  删除单条
    DELETE /api/v1/reference-library            清空

约定（与 `citation_styles` / `templates` 一致）：
- 全部端点要求 Bearer access token；跨用户访问统一 404（不泄漏存在性）；
- **部分成功如实报告**：`created` / `skipped` / `rejected` 三个数字都给；
- 只有**全部被拒**时才 400，且列出逐条原因；
- 业务逻辑全在 `ReferenceLibraryService`，本层只做 HTTP 映射。

## 为什么保存接口接收 `items` 而不是 `content`

解析在前一步（`POST /references/import`）已完成，用户已经在界面上**看过并确认**
了那些条目。再传一次原始文本会重新解析一遍，可能出现「看到的和存进去的不一致」。
所以这里接收的是**用户确认过的结构化条目**。
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user
from app.core.exceptions import ValidationException
from app.db.models.reference_library import ReferenceLibraryItem
from app.db.models.user import User
from app.db.session import get_db
from app.knowledge.services.reference_library_service import (
    ReferenceLibraryService,
)
from app.tools.csl.models import CslItem

router = APIRouter(prefix="/reference-library", tags=["reference-library"])

__all__ = ["MAX_SAVE_ITEMS", "get_reference_library_service", "router"]

#: 单次保存条数上限（一份 Zotero 整库导出可能有数千条，分批提交更可控）
MAX_SAVE_ITEMS = 500


# ===== 请求/响应模型 =====


class LibraryItemOut(BaseModel):
    """引用库条目（列表与保存响应共用）。"""

    model_config = ConfigDict(extra="forbid")

    item_id: str
    ref_id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    container_title: str | None = None
    doi: str | None = None
    type: str


class LibraryListOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[LibraryItemOut] = Field(default_factory=list)
    total: int


class LibrarySummaryOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int
    usable_as_source: int


class RejectedItemOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref_id: str
    title: str
    reason: str


class SaveRequest(BaseModel):
    """保存请求：**用户确认过的** `CslItem` 列表（来自导入预览）。"""

    model_config = ConfigDict(extra="forbid")

    items: list[dict] = Field(
        default_factory=list,
        description="CslItem 形态的条目（即导入预览里的原始结构）",
    )


class SaveResponse(BaseModel):
    """保存结果：三个数字都必须给，前端才能如实展示部分成功。"""

    model_config = ConfigDict(extra="forbid")

    created: int
    skipped: int
    rejected: list[RejectedItemOut] = Field(default_factory=list)


# ===== 依赖 =====


def get_reference_library_service(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ReferenceLibraryService:
    return ReferenceLibraryService(db)


CurrentUserDep = Annotated[User, Depends(get_current_active_user)]
ServiceDep = Annotated[ReferenceLibraryService, Depends(get_reference_library_service)]


def _to_out(row: ReferenceLibraryItem) -> LibraryItemOut:
    """ORM 行 → 响应模型（从 `payload` 取真源字段）。

    参数类型必须写成 `ReferenceLibraryItem`，不能写成 `object` 再用
    `# type: ignore` 压 `to_csl_item()` —— 那样 `row.id` 这类属性访问压不住，
    CI 上就是这样暴露的（AGENTS §5 #31）。
    """
    item = row.to_csl_item()
    return LibraryItemOut(
        item_id=str(row.id),
        ref_id=item.id,
        title=item.title,
        authors=[
            (name.literal or f"{name.family or ''}{name.given or ''}").strip()
            or (name.family or "")
            for name in item.author
        ],
        year=item.issued_year,
        container_title=item.container_title,
        doi=item.doi,
        type=item.type,
    )


# ===== 端点 =====


@router.get("", response_model=LibraryListOut, summary="列出我的引用库")
async def list_library(
    service: ServiceDep,
    current_user: CurrentUserDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> LibraryListOut:
    rows = await service.list_items(current_user.id, limit=limit, offset=offset)
    return LibraryListOut(
        items=[_to_out(row) for row in rows],
        total=await service.count(current_user.id),
    )


@router.get("/summary", response_model=LibrarySummaryOut, summary="引用库摘要")
async def library_summary(service: ServiceDep, current_user: CurrentUserDep) -> LibrarySummaryOut:
    """总条数 + 可作文献源的条数。

    前端在任务创建表单里用它提示「将从你的 N 篇文献中选取，不外呼」。
    两个数字当前相等（入库准入已保证能存进来的都能用），
    仍分两个字段返回是为了将来放宽准入时前端不用改契约。
    """
    summary = await service.summary(current_user.id)
    return LibrarySummaryOut(total=summary.total, usable_as_source=summary.usable_as_source)


@router.post(
    "",
    response_model=SaveResponse,
    status_code=status.HTTP_201_CREATED,
    summary="保存条目到引用库",
)
async def save_items(
    payload: SaveRequest,
    service: ServiceDep,
    current_user: CurrentUserDep,
) -> SaveResponse:
    """批量保存；按 `ref_id` 幂等。

    ## 部分成功是常态

    用户粘一份 `.bib` 里常有几条缺年份的（预印本、网页引用）。整批 400
    会让用户没法保存另外那些 —— 但他确实需要它们。所以逐条判定，
    能进的进，不能进的带原因返回。

    ## 只有全部被拒才 400

    全部被拒说明用户这批数据根本不能当文献源用，此时回 400 + 逐条原因，
    比回 201 且 `created=0` 更清楚。
    """
    if not payload.items:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="没有要保存的条目",
        )
    if len(payload.items) > MAX_SAVE_ITEMS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"单次最多保存 {MAX_SAVE_ITEMS} 条（收到 {len(payload.items)} 条）",
        )

    try:
        items = [CslItem.model_validate(raw) for raw in payload.items]
    except ValidationException as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.message) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"条目结构不合法：{exc}",
        ) from exc

    result = await service.save(current_user.id, items)

    if result.created == 0 and result.skipped == 0 and result.rejected:
        reasons = "；".join(
            f"{item.title or item.ref_id}：{item.reason}" for item in result.rejected
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"全部条目都无法入库：{reasons}",
        )

    return SaveResponse(
        created=result.created,
        skipped=result.skipped,
        rejected=[
            RejectedItemOut(ref_id=item.ref_id, title=item.title, reason=item.reason)
            for item in result.rejected
        ],
    )


@router.delete("", status_code=status.HTTP_200_OK, summary="清空引用库")
async def clear_library(service: ServiceDep, current_user: CurrentUserDep) -> dict[str, int]:
    deleted = await service.delete_all(current_user.id)
    return {"deleted": deleted}


@router.delete(
    "/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除单条",
)
async def delete_item(
    item_id: uuid.UUID,
    service: ServiceDep,
    current_user: CurrentUserDep,
) -> Response:
    await service.delete(current_user.id, item_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
