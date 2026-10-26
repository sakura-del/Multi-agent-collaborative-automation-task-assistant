"""引用样式 HTTP adapter（Issue #4 M2-2 / 计划 §1.3）。

    GET    /api/v1/citation-styles              列出（预置 + 当前用户自定义）
    GET    /api/v1/citation-styles/previews     每个样式一条**真实渲染**预览（2026-10-26 新增）
    POST   /api/v1/citation-styles              上传 .csl（multipart `file`）
    DELETE /api/v1/citation-styles/{style_id}   删除当前用户的自定义样式

约定：
- 全部端点要求 Bearer access token；跨用户访问统一 404（与不存在同语义，不泄漏存在性）；
- 上传 / 删除的准入失败 → **400**，`detail` 为可读字符串（逐条 reasons 优先，绝不静默接受）；
- 业务逻辑全在 `CitationStyleService` / `StylePreviewService`，本层只做 HTTP 校验与响应映射。

## ⚠️ 路由顺序：`/previews` 必须定义在 `/{style_id}` 之前

`DELETE /{style_id}` 虽然是 DELETE 方法、与 GET 不冲突，但**将来若新增
`GET /{style_id}`，`/previews` 会被当成 style_id="previews"**。
把固定路径放在动态路径之前是 FastAPI 的既定约定，这里显式遵守并留下注释。

路由挂载由 Lead 在 `app/api/v1/router.py` 负责，本模块不自挂载。
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user
from app.core.config import get_settings
from app.core.exceptions import ValidationException
from app.db.models.user import User
from app.db.session import get_db
from app.knowledge.services.citation_style_service import (
    MAX_STYLE_BYTES,
    CitationStyleService,
)
from app.knowledge.services.style_preview_service import StylePreviewService, _PreviewTarget
from app.schemas.citation_style import (
    CitationStyleList,
    CitationStyleOut,
    CitationStylePreviews,
)
from app.tools.csl.styles import StyleRegistry

router = APIRouter(prefix="/citation-styles", tags=["citation-styles"])

__all__ = [
    "MAX_STYLE_BYTES",
    "get_citation_style_service",
    "get_csl_storage_root",
    "get_style_preview_service",
    "get_style_registry",
    "router",
]


def get_csl_storage_root() -> Path:
    """自定义样式落盘根目录：`artifact_root` 同级 `csl_styles`（计划 §1.2）。

    与任务产物目录分离，避免样式文件混进 artifact 的下载/清理路径。
    """
    artifact_root = Path(get_settings().app.artifact_root)
    return (artifact_root.parent / "csl_styles").resolve()


def get_style_registry(request: Request) -> StyleRegistry:
    """共享样式注册表（可选接线点，计划 §1.4）。

    上传的样式要能被**渲染期**解析（`StyleRegistry.resolve/meta/get`），
    渲染器与上传入口必须使用同一实例：应用启动时把实例放进
    `app.state.style_registry` 即可；未设置时退化为每次请求独立实例
    （列表/上传/删除本身不受影响）。
    """
    registry = getattr(request.app.state, "style_registry", None)
    if isinstance(registry, StyleRegistry):
        return registry
    return StyleRegistry()


def get_citation_style_service(
    db: Annotated[AsyncSession, Depends(get_db)],
    storage_root: Annotated[Path, Depends(get_csl_storage_root)],
    registry: Annotated[StyleRegistry, Depends(get_style_registry)],
) -> CitationStyleService:
    """按请求构造样式服务（session 由 `get_db` 提供，事务边界在 service 层）。"""
    return CitationStyleService(db, storage_root, registry=registry)


def get_style_preview_service(
    registry: Annotated[StyleRegistry, Depends(get_style_registry)],
) -> StylePreviewService:
    """预览服务：**与列表/上传共用同一个 registry**。

    为什么不新建 `StyleRegistry()`：自定义样式是登记在 registry 实例里的
    （`_custom` 字典），换实例会导致「刚上传的样式在预览里查不到」。
    """
    return StylePreviewService(registry=registry)


CurrentUserDep = Annotated[User, Depends(get_current_active_user)]
ServiceDep = Annotated[CitationStyleService, Depends(get_citation_style_service)]
PreviewDep = Annotated[StylePreviewService, Depends(get_style_preview_service)]


def _bad_request(exc: ValidationException) -> HTTPException:
    """准入 / 参数失败 → 400，`detail` 为可读字符串（逐条 reasons 优先）。"""
    reasons = exc.details.get("reasons") if isinstance(exc.details, dict) else None
    if isinstance(reasons, list) and reasons:
        detail = "；".join(str(item) for item in reasons)
    else:
        detail = exc.message
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


@router.get("", response_model=CitationStyleList, summary="列出可用引用样式")
async def list_citation_styles(
    service: ServiceDep,
    current_user: CurrentUserDep,
) -> CitationStyleList:
    """预置样式（在前）+ 当前用户自定义样式；`default_style_id` 为产品默认样式。"""
    return await service.list_all(current_user.id)


@router.get(
    "/previews",
    response_model=CitationStylePreviews,
    summary="每个样式的真实渲染预览",
)
async def preview_citation_styles(
    service: ServiceDep,
    preview: PreviewDep,
    current_user: CurrentUserDep,
) -> CitationStylePreviews:
    """为当前用户可见的每个样式渲染一条样例预览（2026-10-26 样式页重设计）。

    ## 为什么是「当前用户可见的样式」

    预置 6 个 + 该用户自己上传的自定义样式 —— 与 `GET ""` 同一口径，
    预览顺序也与之**完全一致**（前端按下标对齐，不靠二次查找）。

    ## 为什么不在前端渲染

    预览必须与**导出结果**一致，而导出走 `CitationRenderer`（citeproc + 真实
    `.csl`）。前端另写一套格式化器 = 第二份 CSL 实现，必然漂移，
    用户会看到「预览长这样、下载出来不是」——比没有预览更糟。

    ## `warnings` 非空 = 该预览不可信

    `CitationRenderer` 在 citeproc 失败时会**静默回退**极简格式化器。
    实测踩过：探针脚本 sys.path 注入导致 `app` 解析到 site-packages 的副本
    （缺 `styles/` 资产），6 个样式全部走兜底，输出只有 2 种 ——
    但 `render()` 依然返回非空条目，**看起来完全成功**。
    所以这里把 warning 透出来，让前端能显式提示而不是当正常预览展示。
    """
    listed = await service.list_all(current_user.id)
    targets = [
        _PreviewTarget(
            style_id=style.style_id,
            title=style.title,
            source=style.source,
            is_default=style.is_default,
        )
        for style in listed.styles
    ]
    return preview.build(targets)


@router.post(
    "",
    response_model=CitationStyleOut,
    status_code=status.HTTP_201_CREATED,
    summary="上传自定义 CSL 样式",
)
async def upload_citation_style(
    file: Annotated[UploadFile, File(description="标准 CSL 1.0.2 样式文件（.csl）")],
    service: ServiceDep,
    current_user: CurrentUserDep,
) -> CitationStyleOut:
    """上传 .csl：只读上限 +1 字节用于超限判定（不把超大文件读进内存）。"""
    content = await file.read(MAX_STYLE_BYTES + 1)
    try:
        return await service.upload(
            current_user.id,
            filename=file.filename or "",
            content=content,
        )
    except ValidationException as exc:
        raise _bad_request(exc) from exc


@router.delete(
    "/{style_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除自定义引用样式",
)
async def delete_citation_style(
    style_id: str,
    service: ServiceDep,
    current_user: CurrentUserDep,
) -> Response:
    """owner-scoped 删除：预置样式 400，不存在 / 跨用户 404。"""
    try:
        await service.delete(current_user.id, style_id)
    except ValidationException as exc:
        raise _bad_request(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
