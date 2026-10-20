"""参考文献导入 HTTP adapter（Phase 1 · P1-E 接线）。

    POST /api/v1/references/import   导入 BibTeX / RIS，返回解析后的条目预览

设计约定：
- **纯解析、不落库**：导入只做「文本 → CslItem 列表」的预览，用户确认后再走保存路径；
  避免半成品数据污染引用库，也便于前端做「导入前预览」。
- 格式可显式指定（`bibtex` / `ris`），也可省略由服务端按内容探测。
- 非法输入 → **400** 且 `detail` 可读（带定位信息，绝不静默返回半成品）。
- 解析逻辑全在 `app.tools.csl.importers`（纯函数、无网络、无 IO），本层只做 HTTP 映射。

路由挂载由 Lead 在 `app/api/v1/router.py` 负责，本模块不自挂载。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.core.exceptions import ValidationException
from app.tools.csl.importers import parse_bibtex, parse_ris
from app.tools.csl.models import CslItem

router = APIRouter(prefix="/references", tags=["references"])

__all__ = ["MAX_IMPORT_BYTES", "router"]

#: 导入文本上限（1 MB ≈ 数千条 BibTeX/RIS，足够 Zotero/EndNote 整库导出）
MAX_IMPORT_BYTES = 1024 * 1024

ImportFormat = Literal["bibtex", "ris", "auto"]


class ImportRequest(BaseModel):
    """导入请求：文本内容 + 可选格式（默认自动探测）。

    `format` 用 `str` 而非 `Literal`：非法值要返回 **400**（可读原因），
    而 `Literal` 会被 Pydantic 拦成 422——与本模块「准入失败 → 400」的约定不一致。
    """

    model_config = ConfigDict(extra="forbid")

    content: str = Field(description="BibTeX 或 RIS 文本内容")
    format: str = Field(default="auto", description="bibtex / ris / auto")


class ImportedItemOut(BaseModel):
    """导入预览条目（CslItem 的关键字段，供前端展示与确认）。"""

    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    type: str
    title: str = ""
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    container_title: str | None = None
    volume: str | None = None
    issue: str | None = None
    page: str | None = None
    publisher: str | None = None
    doi: str | None = None
    url: str | None = None


class ImportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["bibtex", "ris"]
    """实际采用的解析格式（`auto` 会被解析成具体值）。"""

    count: int
    items: list[ImportedItemOut] = Field(default_factory=list)


def detect_format(content: str) -> Literal["bibtex", "ris"] | None:
    """按内容探测格式；无法判定返回 None。

    判据（保守，避免误判）：
    - RIS：出现行首 `TY  - `（两空格 + 连字符）；
    - BibTeX：出现 `@<type>{` 或 `@<type>(` 条目起始。
    """
    stripped = content.strip()
    if not stripped:
        return None
    for line in stripped.splitlines():
        if line.lstrip().startswith("TY  -"):
            return "ris"
    import re

    if re.search(r"@[A-Za-z]+\s*[{(]", stripped):
        return "bibtex"
    return None


def _to_out(item: CslItem) -> ImportedItemOut:
    return ImportedItemOut(
        id=item.id,
        type=str(item.type),
        title=item.title or "",
        authors=[_name_to_text(name) for name in (item.author or [])],
        year=item.issued_year,
        container_title=item.container_title,
        volume=item.volume,
        issue=item.issue,
        page=item.page,
        publisher=item.publisher,
        doi=item.doi,
        url=item.url,
    )


def _name_to_text(name: Any) -> str:
    """`CslName` → `"Family, Given"`（与导入器的输出口径一致）。"""
    literal = getattr(name, "literal", None)
    if literal:
        return str(literal)
    family = getattr(name, "family", None) or ""
    given = getattr(name, "given", None) or ""
    if family and given:
        return f"{family}, {given}"
    return str(family or given)


def _bad_request(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)


@router.post(
    "/import",
    response_model=ImportResponse,
    summary="导入 BibTeX / RIS 参考文献（预览，不落库）",
)
async def import_references(payload: ImportRequest) -> ImportResponse:
    """解析 BibTeX/RIS 文本并返回条目预览。

    **不落库**：调用方拿到预览后自行决定是否保存（避免半成品污染引用库）。
    """
    content = payload.content
    if len(content.encode("utf-8")) > MAX_IMPORT_BYTES:
        raise _bad_request(f"导入内容超过大小上限（{MAX_IMPORT_BYTES} 字节）")

    requested = payload.format.strip().lower()
    if requested not in ("bibtex", "ris", "auto"):
        raise _bad_request(f"不支持的导入格式：{payload.format!r}（可选 bibtex / ris / auto）")

    resolved: Literal["bibtex", "ris"]
    if requested == "auto":
        detected = detect_format(content)
        if detected is None:
            raise _bad_request(
                "无法识别内容格式：请显式指定 format=bibtex 或 format=ris，"
                "或检查内容是否为合法 BibTeX/RIS"
            )
        resolved = detected
    else:
        resolved = "bibtex" if requested == "bibtex" else "ris"

    # 空内容 → 空列表（不是错误：允许前端先提交再填充）
    if not content.strip():
        return ImportResponse(format=resolved, count=0, items=[])

    try:
        parsed = parse_bibtex(content) if resolved == "bibtex" else parse_ris(content)
    except ValidationException as exc:
        raise _bad_request(exc.message) from exc

    items = [_to_out(item) for item in parsed]
    return ImportResponse(format=resolved, count=len(items), items=items)


_ = Annotated  # 保留导入以免被 ruff 判为未使用（部分运行期校验会用到）
