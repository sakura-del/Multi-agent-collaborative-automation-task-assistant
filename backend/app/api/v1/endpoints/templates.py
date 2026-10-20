"""Word 模板 HTTP adapter（Phase 1 · P1-A 接线）。

    POST   /api/v1/templates                上传 .docx 模板（multipart `file`）
    GET    /api/v1/templates                列出当前用户模板
    GET    /api/v1/templates/{id}           模板详情
    DELETE /api/v1/templates/{id}           删除（owner-scoped）
    POST   /api/v1/templates/{id}/render    用模板渲染成稿（返回 .docx 下载）

约定（与 `citation_styles` 一致）：
- 全部端点要求 Bearer access token；跨用户访问统一 404（不泄漏存在性）；
- 准入失败 → **400**，`detail` 为可读字符串（逐条 reasons 优先，绝不静默接受）；
- 业务逻辑全在 `TemplateService`，本层只做 HTTP 校验与响应映射；
- 渲染的参考文献由调用方传入**已 CSL 渲染的行**（本端点不碰引用引擎）。

路由挂载由 Lead 在 `app/api/v1/router.py` 负责，本模块不自挂载。
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user
from app.core.config import get_settings
from app.core.exceptions import ValidationException
from app.db.models.user import User
from app.db.models.user_template import UserTemplate
from app.db.session import get_db
from app.knowledge.services.template_service import MAX_TEMPLATE_BYTES, TemplateService

router = APIRouter(prefix="/templates", tags=["templates"])

__all__ = ["MAX_TEMPLATE_BYTES", "get_template_service", "get_template_storage_root", "router"]


# ===== 响应/请求模型 =====


class PlaceholderOut(BaseModel):
    """占位符（供前端做「模板字段 → 综述章节」映射）。"""

    model_config = ConfigDict(extra="forbid")

    key: str
    label: str = ""
    required: bool = True
    sample: str = ""


class TemplateOut(BaseModel):
    """模板元数据（列表与详情共用）。"""

    model_config = ConfigDict(extra="forbid")

    template_id: str
    name: str
    checksum: str
    size: int
    placeholders: list[PlaceholderOut] = Field(default_factory=list)
    style_map: dict[str, str] = Field(default_factory=dict)


class TemplateListOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[TemplateOut] = Field(default_factory=list)


class RenderRequest(BaseModel):
    """渲染请求：章节内容 + **已 CSL 渲染好的**参考文献行。"""

    model_config = ConfigDict(extra="forbid")

    sections: dict[str, str] = Field(default_factory=dict)
    bibliography: list[str] = Field(default_factory=list)
    filename: str = "review.docx"


# ===== 依赖 =====


def get_template_storage_root() -> Path:
    """模板落盘根目录：`artifact_root` 同级 `templates`（与样式目录同级、互相隔离）。"""
    artifact_root = Path(get_settings().app.artifact_root)
    return (artifact_root.parent / "templates").resolve()


def get_template_service(
    db: Annotated[AsyncSession, Depends(get_db)],
    storage_root: Annotated[Path, Depends(get_template_storage_root)],
) -> TemplateService:
    return TemplateService(db, storage_root)


CurrentUserDep = Annotated[User, Depends(get_current_active_user)]
ServiceDep = Annotated[TemplateService, Depends(get_template_service)]


def _bad_request(exc: ValidationException) -> HTTPException:
    """准入 / 渲染契约失败 → 400，`detail` 为可读字符串（逐条 reasons 优先）。"""
    reasons = exc.details.get("reasons") if isinstance(exc.details, dict) else None
    if isinstance(reasons, list) and reasons:
        detail = "；".join(str(item) for item in reasons)
    else:
        detail = exc.message
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def _to_out(row: UserTemplate) -> TemplateOut:
    """ORM 行 → 响应模型（placeholders 是 JSON 里的 dict 列表）。"""
    placeholders = [
        PlaceholderOut(**item) if isinstance(item, dict) else PlaceholderOut(key=str(item))
        for item in row.placeholders or []
    ]
    return TemplateOut(
        template_id=row.template_id,
        name=row.name,
        checksum=row.checksum,
        size=row.size,
        placeholders=placeholders,
        style_map={str(k): str(v) for k, v in (row.style_map or {}).items()},
    )


# ===== 端点 =====


@router.get("", response_model=TemplateListOut, summary="列出我的 Word 模板")
async def list_templates(service: ServiceDep, current_user: CurrentUserDep) -> TemplateListOut:
    rows = await service.list_templates(current_user.id)
    return TemplateListOut(items=[_to_out(row) for row in rows])


@router.post(
    "",
    response_model=TemplateOut,
    status_code=status.HTTP_201_CREATED,
    summary="上传 Word 模板",
)
async def upload_template(
    file: Annotated[UploadFile, File(description="Word 模板（.docx）")],
    service: ServiceDep,
    current_user: CurrentUserDep,
    name: Annotated[str, Form(description="模板显示名称")] = "",
) -> TemplateOut:
    """只读上限 +1 字节用于超限判定（不把超大文件读进内存）。"""
    content = await file.read(MAX_TEMPLATE_BYTES + 1)
    try:
        row = await service.upload(
            current_user.id,
            filename=file.filename or "",
            content=content,
            name=name,
        )
    except ValidationException as exc:
        raise _bad_request(exc) from exc
    return _to_out(row)


@router.get("/{template_id}", response_model=TemplateOut, summary="模板详情")
async def get_template(
    template_id: str, service: ServiceDep, current_user: CurrentUserDep
) -> TemplateOut:
    row = await service.get_template(current_user.id, template_id)
    return _to_out(row)


@router.delete(
    "/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除模板",
)
async def delete_template(
    template_id: str, service: ServiceDep, current_user: CurrentUserDep
) -> Response:
    await service.delete(current_user.id, template_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{template_id}/render",
    summary="用模板渲染成稿（返回 .docx）",
    response_class=StreamingResponse,
)
async def render_template(
    template_id: str,
    payload: RenderRequest,
    service: ServiceDep,
    current_user: CurrentUserDep,
) -> StreamingResponse:
    """渲染成稿。

    `bibliography` 必须是调用方**已 CSL 渲染好的行**（本端点不解析引用），
    这样「切换引用样式」只需换上游渲染结果，模板层完全不动。
    """
    try:
        data = await service.render(
            current_user.id,
            template_id,
            sections=payload.sections,
            bibliography=payload.bibliography,
        )
    except ValidationException as exc:
        raise _bad_request(exc) from exc

    safe_name = _safe_download_name(payload.filename or "review.docx")
    return StreamingResponse(
        iter([data]),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


def _safe_download_name(raw: str) -> str:
    """下载文件名只保留安全字符（防头注入 / 路径穿越）。"""
    stem = Path(raw).stem
    cleaned = "".join(char for char in stem if char.isalnum() or char in "-_ ").strip()
    return f"{cleaned or 'review'}.docx"


# 让 uuid 的导入在类型标注中可用（部分运行期校验会用到）
_ = uuid
