"""Word 模板服务：上传准入、owner-scoped 存储、渲染编排（Phase 1 · P1-A 接线）。

分层约定（与 `CitationStyleService` 一致）：
- 准入与安全校验全部委托 `app.tools.templates.inspector`（模板是不可信输入，坑 #7）；
- 文件落盘 `{storage_root}/{user_id}/{template_id}.docx`，DB 只存元数据；
- 跨用户访问统一 404（与不存在同语义，不泄漏存在性）；
- 渲染**不碰引用引擎**：参考文献行由调用方（工作流/导出层）传入已 CSL 渲染结果。
"""

from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException, ValidationException
from app.core.logging import get_logger
from app.db.models.user_template import UserTemplate
from app.tools.templates.inspector import inspect_template, validate_template_bytes
from app.tools.templates.models import StyleRole, TemplateMeta, TemplateSecurityError
from app.tools.templates.renderer import render_document

logger = get_logger(__name__)

#: 单个模板文件上限（.docx 模板通常几十 KB；留足含图片模板的余量）
MAX_TEMPLATE_BYTES = 5 * 1024 * 1024

#: 允许的扩展名（`.docm` 等一律拒绝——宏模板不可信，坑 #7）
ALLOWED_SUFFIXES = frozenset({".docx"})


class TemplateService:
    """模板上传 / 列表 / 渲染 / 删除（全部 owner-scoped）。"""

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

    async def list_templates(self, user_id: uuid.UUID) -> list[UserTemplate]:
        stmt = (
            select(UserTemplate)
            .where(UserTemplate.user_id == user_id)
            .order_by(UserTemplate.created_at.desc())
        )
        return list((await self._db.execute(stmt)).scalars().all())

    async def get_template(self, user_id: uuid.UUID, template_id: str) -> UserTemplate:
        return await self._get_owned(user_id, template_id)

    # ===== 写入 =====

    async def upload(
        self, user_id: uuid.UUID, *, filename: str, content: bytes, name: str = ""
    ) -> UserTemplate:
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
        return row

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
        """删除模板：owner-scoped；文件与记录一并清理。"""
        row = await self._get_owned(user_id, template_id)
        await self._db.execute(delete(UserTemplate).where(UserTemplate.id == row.id))
        await self._db.commit()
        self.path_for(user_id, template_id).unlink(missing_ok=True)
        logger.info("template_deleted", user_id=str(user_id), template_id=template_id)

    # ===== 渲染 =====

    async def render(
        self,
        user_id: uuid.UUID,
        template_id: str,
        *,
        sections: dict[str, str],
        bibliography: list[str],
    ) -> bytes:
        """用模板渲染成稿。

        `bibliography` 必须是**已 CSL 渲染好的行**——本服务不解析引用，只做模板填充
        （分层：引用引擎在 `app.tools.csl`，模板层只负责排版）。
        """
        row = await self._get_owned(user_id, template_id)
        source = self.path_for(user_id, template_id)
        if not source.is_file():
            raise NotFoundException("模板文件已丢失，请重新上传")

        style_map = {key: _as_role(value) for key, value in (row.style_map or {}).items()}
        try:
            return render_document(
                template=source,
                sections=sections,
                bibliography=bibliography,
                style_map=style_map,
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
