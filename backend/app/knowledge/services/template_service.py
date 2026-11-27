"""Word 模板服务：内置标准母版 + 上传准入 + owner-scoped 存储 + 渲染编排（Phase 1 · P1-A）。

分层约定（与 `CitationStyleService` 一致）：
- 准入与安全校验全部委托 `app.tools.templates.inspector`（模板是不可信输入，坑 #7）；
- 用户模板文件落盘 `{storage_root}/{user_id}/{template_id}.docx`，DB 只存元数据；
- 内置标准母版位于 `app/tools/templates/builtin/*.docx`（只读、不可删除）；
- 跨用户访问统一 404（与不存在同语义，不泄漏存在性）；
- 渲染**不碰引用引擎**：参考文献行由调用方传入已 CSL 渲染结果；若传入结构化
  `bibliography_entries`（≥3 篇含年份），自动生成真实年份分布图（300 DPI PNG）落版。
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException, ValidationException
from app.core.logging import get_logger
from app.db.models.knowledge import KnowledgeDoc
from app.db.models.user_template import UserTemplate
from app.tools.literature.exporter import prepare_template_export
from app.tools.templates.charts import (
    build_tech_roadmap_figure,
    build_year_distribution_figure,
    extract_roadmap_steps,
)
from app.tools.templates.inspector import inspect_template, validate_template_bytes
from app.tools.templates.models import StyleRole, TemplateMeta, TemplateSecurityError
from app.tools.templates.renderer import render_document
from app.tools.templates.rich_blocks import FigureAsset

logger = get_logger(__name__)

#: 单个模板文件上限（.docx 模板通常几十 KB；留足含图片模板的余量）
MAX_TEMPLATE_BYTES = 5 * 1024 * 1024

#: 允许的扩展名（`.docm` 等一律拒绝——宏模板不可信，坑 #7）
ALLOWED_SUFFIXES = frozenset({".docx"})

#: 内置高校标准「文献综述」母版 ID
BUILTIN_TEMPLATE_ID = "builtin:literature_review_cn_standard"
#: 内置高校标准「开题报告」母版 ID（Phase 2）
BUILTIN_PROPOSAL_TEMPLATE_ID = "builtin:thesis_proposal_cn_standard"
_BUILTIN_DIR = Path(__file__).resolve().parents[2] / "tools" / "templates" / "builtin"


@dataclass(frozen=True)
class BuiltinTemplateSpec:
    template_id: str
    name: str
    path: Path


BUILTIN_TEMPLATES: dict[str, BuiltinTemplateSpec] = {
    BUILTIN_TEMPLATE_ID: BuiltinTemplateSpec(
        template_id=BUILTIN_TEMPLATE_ID,
        name="高校标准文献综述母版（中仿宋+TNR · 三线表 · 奇数页起章）",
        path=_BUILTIN_DIR / "literature_review_cn_standard.docx",
    ),
    BUILTIN_PROPOSAL_TEMPLATE_ID: BuiltinTemplateSpec(
        template_id=BUILTIN_PROPOSAL_TEMPLATE_ID,
        name="高校标准开题报告母版（中仿宋+TNR · 技术路线图 · 奇数页起章）",
        path=_BUILTIN_DIR / "thesis_proposal_cn_standard.docx",
    ),
}


@dataclass(frozen=True)
class TemplateRecord:
    """模板列表/详情的统一读模型（兼容内置母版与用户上传模板）。"""

    template_id: str
    name: str
    checksum: str
    size: int
    placeholders: list[dict[str, Any]]
    style_map: dict[str, str]
    source: Literal["builtin", "user"] = "user"


_BUILTIN_RECORD_CACHE: dict[str, TemplateRecord] = {}


def _load_builtin_record(spec: BuiltinTemplateSpec) -> TemplateRecord:
    cached = _BUILTIN_RECORD_CACHE.get(spec.template_id)
    if cached is not None:
        return cached
    if not spec.path.is_file():
        raise NotFoundException(f"内置模板文件缺失：{spec.path.name}")
    meta = inspect_template(spec.path)
    record = TemplateRecord(
        template_id=spec.template_id,
        name=spec.name,
        checksum=meta.checksum,
        size=spec.path.stat().st_size,
        placeholders=[item.model_dump(mode="json") for item in meta.placeholders],
        style_map={key: str(role) for key, role in meta.style_map.items()},
        source="builtin",
    )
    _BUILTIN_RECORD_CACHE[spec.template_id] = record
    return record


def _user_to_record(row: UserTemplate) -> TemplateRecord:
    return TemplateRecord(
        template_id=row.template_id,
        name=row.name,
        checksum=row.checksum,
        size=row.size,
        placeholders=list(row.placeholders or []),
        style_map={str(k): str(v) for k, v in (row.style_map or {}).items()},
        source="user",
    )


def _append_figure_to_review_body(body_text: str, *, caption: str, asset_id: str) -> str:
    """若正文尚未引用该图，优先插入「研究现状」章节末尾，否则插在首个主章节末尾（P0-2）。"""
    marker = f"![{caption}](fig://{asset_id})"
    if f"fig://{asset_id}" in body_text:
        return body_text
    lines = body_text.splitlines()
    target_idx: int | None = None
    target_level = 0
    first_section_idx: int | None = None
    first_section_level = 0
    for idx, line in enumerate(lines):
        m = re.match(r"^(#{1,6})\s+(.+)$", line.strip())
        if not m:
            continue
        level = len(m.group(1))
        heading_title = m.group(2).strip()
        if "研究现状" in heading_title:
            target_idx = idx
            target_level = level
            break
        if (
            first_section_idx is None
            and level <= 2
            and heading_title not in {"摘要", "参考文献", "引用核验"}
        ):
            first_section_idx = idx
            first_section_level = level

    if target_idx is None:
        if first_section_idx is not None:
            target_idx = first_section_idx
            target_level = first_section_level
        else:
            sep = "\n\n" if body_text.strip() else ""
            return f"{body_text.rstrip()}{sep}{marker}"

    insert_at = len(lines)
    for idx in range(target_idx + 1, len(lines)):
        m = re.match(r"^(#{1,6})\s+", lines[idx].strip())
        if m and len(m.group(1)) <= target_level:
            insert_at = idx
            break
    updated = [*lines[:insert_at], "", marker, "", *lines[insert_at:]]
    return "\n".join(updated).strip()


def _insert_roadmap_marker(body_text: str, *, caption: str, asset_id: str) -> str:
    """把技术路线图标记插入「技术路线」章节末尾（Phase 2）；已存在则不重复插。"""
    marker = f"![{caption}](fig://{asset_id})"
    if f"fig://{asset_id}" in body_text:
        return body_text
    lines = body_text.splitlines()
    target_idx: int | None = None
    target_level = 0
    for idx, line in enumerate(lines):
        m = re.match(r"^(#{1,6})\s+(.+)$", line.strip())
        if m and "技术路线" in m.group(2):
            target_idx = idx
            target_level = len(m.group(1))
            break
    if target_idx is None:
        sep = "\n\n" if body_text.strip() else ""
        return f"{body_text.rstrip()}{sep}{marker}"

    insert_at = len(lines)
    for idx in range(target_idx + 1, len(lines)):
        m = re.match(r"^(#{1,6})\s+", lines[idx].strip())
        if m and len(m.group(1)) <= target_level:
            insert_at = idx
            break
    updated = [*lines[:insert_at], "", marker, "", *lines[insert_at:]]
    return "\n".join(updated).strip()


def _strip_figure_marker(body_text: str, *, asset_id: str) -> str:
    """移除指定 fig:// 占位行（图无法生成时避免死链占位）。"""
    pattern = re.compile(rf"^\s*!\[[^\]]*\]\(fig://{re.escape(asset_id)}\)\s*$")
    kept = [line for line in body_text.splitlines() if not pattern.match(line)]
    return "\n".join(kept).strip()


class TemplateService:
    """模板上传 / 列表 / 渲染 / 删除（全部 owner-scoped，兼容内置只读母版）。"""

    def __init__(self, db: AsyncSession, storage_root: Path) -> None:
        self._db = db
        self._root = storage_root

    # ===== 路径 =====

    def path_for(self, user_id: uuid.UUID, template_id: str) -> Path:
        """模板文件落盘路径（用户目录隔离）。"""
        return self._root / str(user_id) / f"{template_id}.docx"

    # ===== 准入 =====

    @staticmethod
    def _validate_admission(*, filename: str, content: bytes) -> None:
        """上传准入：扩展名 + 大小 + 内容安全校验（复用 inspector，逐条可读原因）。"""
        suffix = Path(filename).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            raise ValidationException(
                f"只支持 .docx 模板（收到 {suffix or '无扩展名'}）",
                details={"reasons": [f"扩展名 {suffix or '(空)'} 不被接受，仅支持 .docx"]},
            )
        if not content:
            raise ValidationException("模板文件为空", details={"reasons": ["上传的模板文件为空"]})
        if len(content) > MAX_TEMPLATE_BYTES:
            raise ValidationException(
                f"模板超过大小上限（{MAX_TEMPLATE_BYTES} 字节）",
                details={"reasons": [f"模板 {len(content)} 字节超过上限 {MAX_TEMPLATE_BYTES}"]},
            )
        # 委托 inspector：.docm 内容类型 / 宏 / OLE / zip 炸弹 / 路径穿越 / 坏包
        try:
            validate_template_bytes(content)
        except TemplateSecurityError as exc:
            raise ValidationException(exc.message, details=exc.details) from exc
        except ValidationException as exc:
            raise ValidationException(exc.message, details=exc.details) from exc

    # ===== 查询 =====

    async def _get_owned(self, user_id: uuid.UUID, template_id: str) -> UserTemplate:
        """取当前用户拥有的模板；不存在或跨用户 → 404（同语义）。"""
        stmt = select(UserTemplate).where(
            UserTemplate.user_id == user_id,
            UserTemplate.template_id == template_id,
        )
        row = (await self._db.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise NotFoundException("模板不存在")
        return row

    async def list_templates(self, user_id: uuid.UUID) -> list[TemplateRecord]:
        builtins = [_load_builtin_record(spec) for spec in BUILTIN_TEMPLATES.values()]
        stmt = (
            select(UserTemplate)
            .where(UserTemplate.user_id == user_id)
            .order_by(UserTemplate.created_at.desc())
        )
        rows = list((await self._db.execute(stmt)).scalars().all())
        return [*builtins, *(_user_to_record(row) for row in rows)]

    async def get_template(self, user_id: uuid.UUID, template_id: str) -> TemplateRecord:
        spec = BUILTIN_TEMPLATES.get(template_id)
        if spec is not None:
            return _load_builtin_record(spec)
        return _user_to_record(await self._get_owned(user_id, template_id))

    # ===== 写入 =====

    async def upload(
        self, user_id: uuid.UUID, *, filename: str, content: bytes, name: str = ""
    ) -> TemplateRecord:
        """上传模板：准入 → 落盘 → 解析占位符/style_map → 落库（同内容幂等）。"""
        self._validate_admission(filename=filename, content=content)

        target_dir = self._root / str(user_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        # 先写临时文件再解析：inspector 需要路径
        staging = target_dir / f".staging-{uuid.uuid4().hex}.docx"
        staging.write_bytes(content)
        try:
            meta: TemplateMeta = inspect_template(staging)
        finally:
            staging.unlink(missing_ok=True)

        final_path = self.path_for(user_id, meta.template_id)
        final_path.write_bytes(content)

        display_name = (name or "").strip() or Path(filename).stem or meta.template_id
        row = await self._upsert(user_id, meta, display_name, size=len(content))
        logger.info(
            "template_uploaded",
            user_id=str(user_id),
            template_id=meta.template_id,
            placeholders=len(meta.placeholders),
        )
        return _user_to_record(row)

    async def _upsert(
        self, user_id: uuid.UUID, meta: TemplateMeta, name: str, *, size: int
    ) -> UserTemplate:
        """同用户同 template_id 视为同一模板：更新名称与元数据，不重复建行。"""
        stmt = select(UserTemplate).where(
            UserTemplate.user_id == user_id,
            UserTemplate.template_id == meta.template_id,
        )
        row = (await self._db.execute(stmt)).scalar_one_or_none()
        placeholders = [item.model_dump(mode="json") for item in meta.placeholders]
        style_map = {key: str(role) for key, role in meta.style_map.items()}
        if row is None:
            row = UserTemplate(
                user_id=user_id,
                template_id=meta.template_id,
                name=name,
                checksum=meta.checksum,
                placeholders=placeholders,
                style_map=style_map,
                size=size,
            )
            self._db.add(row)
        else:
            row.name = name
            row.checksum = meta.checksum
            row.placeholders = placeholders
            row.style_map = style_map
            row.size = size
        await self._db.commit()
        await self._db.refresh(row)
        return row

    async def delete(self, user_id: uuid.UUID, template_id: str) -> None:
        """删除模板：内置只读模板拒绝删除（400）；用户模板 owner-scoped 删除。"""
        if template_id in BUILTIN_TEMPLATES or template_id.startswith("builtin:"):
            raise ValidationException(
                "内置标准模板不支持删除",
                details={"reasons": ["内置标准模板为系统预置只读母版，不支持删除"]},
            )
        row = await self._get_owned(user_id, template_id)
        await self._db.execute(delete(UserTemplate).where(UserTemplate.id == row.id))
        await self._db.commit()
        self.path_for(user_id, template_id).unlink(missing_ok=True)
        logger.info("template_deleted", user_id=str(user_id), template_id=template_id)

    # ===== 渲染 =====

    async def _load_doc_titles(
        self, user_id: uuid.UUID, entries: Sequence[dict[str, Any]] | None
    ) -> dict[str, str]:
        """批量加载知识库文档真实标题（owner-scoped），用于修正分块碎片标题（P0-5）。"""
        if not entries or self._db is None:
            return {}
        doc_ids: list[uuid.UUID] = []
        seen: set[uuid.UUID] = set()
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("doc_id"):
                continue
            try:
                did = uuid.UUID(str(entry["doc_id"]))
            except (TypeError, ValueError):
                continue
            if did not in seen:
                seen.add(did)
                doc_ids.append(did)
        if not doc_ids:
            return {}
        stmt = select(KnowledgeDoc.id, KnowledgeDoc.title).where(
            KnowledgeDoc.user_id == user_id,
            KnowledgeDoc.id.in_(doc_ids),
        )
        rows = (await self._db.execute(stmt)).all()
        return {
            str(doc_id): str(title).strip() for doc_id, title in rows if str(title or "").strip()
        }

    async def render(
        self,
        user_id: uuid.UUID,
        template_id: str,
        *,
        sections: dict[str, str],
        bibliography: list[str],
        bibliography_entries: Sequence[dict[str, Any]] | None = None,
        citation_style: str | None = None,
    ) -> bytes:
        """用模板渲染成稿（支持内置只读母版与用户模板，及真实参考文献清洗+插图生成）。"""
        spec = BUILTIN_TEMPLATES.get(template_id)
        if spec is not None:
            record = _load_builtin_record(spec)
            source = spec.path
        else:
            row = await self._get_owned(user_id, template_id)
            record = _user_to_record(row)
            source = self.path_for(user_id, template_id)
            if not source.is_file():
                raise NotFoundException("模板文件已丢失，请重新上传")

        render_sections = dict(sections)
        effective_bib = list(bibliography)
        clean_entries: Sequence[dict[str, Any]] = list(bibliography_entries or [])

        is_proposal = template_id == BUILTIN_PROPOSAL_TEMPLATE_ID
        if "body" in render_sections:
            doc_titles = await self._load_doc_titles(user_id, bibliography_entries)
            normalized_body, normalized_bib, merged_entries = prepare_template_export(
                render_sections["body"],
                bibliography_entries,
                fallback_bibliography=effective_bib,
                citation_style=citation_style,
                doc_titles_by_id=doc_titles,
                add_heading_numbers=(spec is not None and not is_proposal),
            )
            render_sections["body"] = normalized_body
            if normalized_bib:
                effective_bib = normalized_bib
            if merged_entries:
                clean_entries = merged_entries

        figures: dict[str, FigureAsset] | None = None
        if is_proposal:
            # 开题报告：从「技术路线」章节抽取阶段并生成纵向技术路线图（Phase 2）
            body_text = render_sections.get("body", "")
            steps = extract_roadmap_steps(body_text)
            roadmap_fig = build_tech_roadmap_figure(steps) if steps else None
            if roadmap_fig is not None:
                figures = {roadmap_fig.asset_id: roadmap_fig}
                if "body" in render_sections:
                    render_sections["body"] = _insert_roadmap_marker(
                        render_sections["body"],
                        caption=roadmap_fig.caption_zh,
                        asset_id=roadmap_fig.asset_id,
                    )
            elif "body" in render_sections:
                # 阶段不足：静默移除占位行，绝不落死链
                render_sections["body"] = _strip_figure_marker(
                    render_sections["body"], asset_id="tech_roadmap"
                )
        elif clean_entries:
            year_fig = build_year_distribution_figure(clean_entries)
            if year_fig is not None:
                figures = {year_fig.asset_id: year_fig}
                if "body" in render_sections:
                    render_sections["body"] = _append_figure_to_review_body(
                        render_sections["body"],
                        caption=year_fig.caption_zh,
                        asset_id=year_fig.asset_id,
                    )

        style_map = {key: _as_role(value) for key, value in record.style_map.items()}
        try:
            return render_document(
                template=source,
                sections=render_sections,
                bibliography=effective_bib,
                style_map=style_map,
                figures=figures,
                chapter_no=2 if is_proposal else 1,
            )
        except ValidationException:
            raise
        except Exception as exc:  # pragma: no cover - 兜底，避免 500 泄漏内部细节
            logger.warning("template_render_failed", template_id=template_id, error=str(exc))
            raise ValidationException(f"模板渲染失败：{exc}") from exc


def _as_role(value: str) -> StyleRole:
    """把 DB 里的字符串还原为 `StyleRole`（无法识别时按 body 处理）。"""
    try:
        return StyleRole(value)
    except ValueError:
        return StyleRole.BODY
