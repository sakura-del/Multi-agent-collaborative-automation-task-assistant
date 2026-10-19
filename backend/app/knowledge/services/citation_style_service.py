"""引用样式服务：上传落盘 + 准入复用 + owner-scoped 列表/删除（Issue #4 M2-2 / 计划 §1.2）。

设计要点：
- **准入校验只复用 M1**（`StyleRegistry.validate_csl_file`）：本模块不重复实现 XML 结构 /
  CSL-M 扫描，失败时把 `StyleRejected.reasons` 原样放进 `ValidationException.details["reasons"]`，
  由 API 层映射为 400 —— **绝不静默接受**；
- **绝不信任客户端文件名**：`style_id` 由服务端从 CSL `<info><id>` 派生并规范化，
  磁盘文件名固定 `<style_id>.csl`，落盘路径 `resolve()` 后必须仍在 `storage_root` 内（防穿越）；
- **owner 隔离**：全部查询经 `CitationStyleRepository` 的 owner-scoped 方法，
  跨用户与不存在同语义（`NotFoundException`）；
- **幂等**：同用户同内容（`content_sha256` 相同）重复上传直接返回既有条目，不产生重复行；
- 事务边界：本层负责 `commit` / `rollback`（Repository 只 flush）。

存储布局（计划 §1.2）：`{storage_root}/{user_id}/{style_id}.csl`，
`storage_root` 由 API 层按「`artifact_root` 同级目录」注入。
"""

from __future__ import annotations

import hashlib
import re
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Literal, cast, get_args
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException, ValidationException
from app.core.logging import get_logger
from app.db.models.citation_style import CitationStyle
from app.db.repositories.citation_style import CitationStyleRepository
from app.schemas.citation_style import CitationStyleList, CitationStyleOut
from app.tools.csl.models import DEFAULT_STYLE_ID, Numbering, Ordering, StyleMeta
from app.tools.csl.styles import StyleRegistry, StyleRejected

logger = get_logger(__name__)

#: 样式文件大小上限（计划 §1.1：`size_bytes <= 256 KiB`）
MAX_STYLE_BYTES = 256 * 1024

#: `style_id` 规范化后的合法形态：小写字母/数字开头，仅含小写字母、数字与连字符，最长 64
_STYLE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

#: 规范化时被折叠的非法字符（非 `[a-z0-9]` 一律变 `-`，随后压缩重复、去首尾）
_ILLEGAL_ID_CHARS = re.compile(r"[^a-z0-9]+")
_DASH_RUN = re.compile(r"-{2,}")

#: `<info><id>` 缺失或规范化后为空时的兜底标识（计划 §1.2）
FALLBACK_STYLE_ID = "custom-style"

#: `style_id` 最大长度（与 ORM `String(64)`、`ReferenceCandidate` 风格的契约一致）
MAX_STYLE_ID_LENGTH = 64

#: 判定 Literal 的合法取值（用于容忍脏 DB 行：非法值回退，避免整表列不出来）
_NUMBERING_VALUES: tuple[str, ...] = get_args(Numbering)
_ORDERING_VALUES: tuple[str, ...] = get_args(Ordering)


def _local_name(tag: str) -> str:
    """去掉命名空间前缀的标签名（与 `tools.csl.styles` 同规则）。"""
    return tag.rsplit("}", 1)[-1]


def _info_style_id(text: str) -> str | None:
    """提取 CSL `<info><id>` 文本（命名空间无关）；缺失/解析失败 → `None`。

    只做取值，不做准入判定 —— 结构与 CSL-M 校验一律交给 `validate_csl_file`。
    """
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    for child in root:
        if _local_name(child.tag) != "info":
            continue
        for node in child:
            if _local_name(node.tag) == "id":
                value = "".join(node.itertext()).strip()
                return value or None
    return None


def normalize_style_id(raw: str | None) -> str:
    """`<info><id>` → 规范 `style_id`：小写、非法字符折叠为 `-`、压缩重复、去首尾。

    空结果回退 `custom-style`（计划 §1.2）；超长按 `MAX_STYLE_ID_LENGTH` 截断。
    """
    text = (raw or "").strip().lower()
    text = _ILLEGAL_ID_CHARS.sub("-", text)
    text = _DASH_RUN.sub("-", text).strip("-")
    text = text[:MAX_STYLE_ID_LENGTH].strip("-")
    return text or FALLBACK_STYLE_ID


def _coerce_literal(value: Any, allowed: tuple[str, ...], default: str) -> str:
    """DB 字符串收敛到冻结 Literal：非法值回退默认（避免单条脏数据让整个列表 500）。"""
    return value if isinstance(value, str) and value in allowed else default


class CitationStyleService:
    """引用样式服务：预置样式 + 用户自定义样式的读取、上传与删除。"""

    def __init__(
        self,
        db: AsyncSession,
        storage_root: Path,
        *,
        registry: StyleRegistry | None = None,
    ) -> None:
        self._db = db
        self._storage_root = Path(storage_root).resolve()
        # 共享样式注册表：渲染器与上传入口必须持有**同一实例**，上传的样式才能被渲染期解析
        # （`StyleRegistry.resolve/meta/get` 会查 `_custom`）。未注入时退化为本服务自持实例，
        # 列表/上传/删除行为不变，只是渲染进程看不到登记结果。
        self._registry = registry if registry is not None else StyleRegistry()

    # ===== 查询 =====

    async def list_all(self, user_id: UUID) -> CitationStyleList:
        """预置样式（`StyleRegistry` 静态表）+ 当前用户自定义样式；预置在前。"""
        styles = [self._preset_out(meta) for meta in self._registry.list_styles()]
        styles.extend(
            self._custom_out(row) for row in await self._repository().list_for_user(user_id)
        )
        return CitationStyleList(styles=styles, default_style_id=DEFAULT_STYLE_ID)

    # ===== 上传 =====

    async def upload(self, user_id: UUID, *, filename: str, content: bytes) -> CitationStyleOut:
        """上传自定义 CSL 样式：大小/编码 → 准入 → 落盘 → 入库（同内容幂等）。

        `filename` 只用于日志（客户端可伪造），**绝不参与落盘路径**。
        """
        data = bytes(content or b"")
        if not data:
            raise ValidationException("样式文件为空")
        if len(data) > MAX_STYLE_BYTES:
            raise ValidationException(
                f"样式文件超过大小上限（{MAX_STYLE_BYTES} 字节，实际 {len(data)} 字节）"
            )
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationException("样式文件必须是 UTF-8 编码的 CSL 文件") from exc

        repository = self._repository()
        digest = hashlib.sha256(data).hexdigest()
        # 幂等：同用户同内容重复上传 → 直接返回既有条目（不新增行、不覆盖文件）
        for row in await repository.list_for_user(user_id):
            if row.content_sha256 == digest:
                logger.info("citation_style_upload_duplicate", style_id=row.style_id)
                return self._custom_out(row)

        base_id = normalize_style_id(_info_style_id(text))
        meta = self._validate_admission(data, base_id)
        style_id = await self._allocate_style_id(repository, user_id, base_id)

        path = self.path_for(user_id, style_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

        row = CitationStyle(
            user_id=user_id,
            style_id=style_id,
            title=meta.title,
            numbering=meta.numbering,
            ordering=meta.ordering,
            default_locale=meta.default_locale,
            filename=path.name,
            content_sha256=digest,
            size_bytes=len(data),
        )
        out = self._out_from_meta(style_id, meta, source="custom")
        try:
            await repository.create(row)
            await self._db.commit()
        except IntegrityError as exc:
            # 并发下同 style_id 撞唯一约束：回滚 + 清理刚落盘文件，让调用方重试
            await self._db.rollback()
            path.unlink(missing_ok=True)
            raise ValidationException(f"样式标识冲突，请重试：{style_id}") from exc
        except Exception:
            await self._db.rollback()
            path.unlink(missing_ok=True)
            raise

        logger.info(
            "citation_style_uploaded",
            style_id=style_id,
            size_bytes=len(data),
            client_filename=Path(filename or "").name[:128],
        )
        self._register_custom(style_id, path)
        return out

    # ===== 删除 =====

    async def delete(self, user_id: UUID, style_id: str) -> None:
        """删除当前用户的自定义样式；预置样式不可删；不存在/跨用户 → 404 语义。"""
        if self._is_preset(self._registry, style_id):
            raise ValidationException(f"预置样式不可删除：{style_id}")

        repository = self._repository()
        row = await repository.get_for_user(user_id, style_id)
        if row is None:
            # 不存在与跨用户同语义：不泄漏资源存在性
            raise NotFoundException("样式不存在")

        path = self.path_for(user_id, style_id)
        await repository.delete(row)
        await self._db.commit()
        # 文件缺失不报错（DB 与磁盘可能因历史故障不同步）
        path.unlink(missing_ok=True)
        self._registry.unregister_custom(style_id)
        logger.info("citation_style_deleted", style_id=style_id)

    # ===== 路径 =====

    def path_for(self, user_id: UUID, style_id: str) -> Path:
        """解析样式文件绝对路径；标识非法或越出用户目录 → `ValidationException`。

        双重防护：先按 `style_id` 合法形态白名单校验，再断言 `resolve()` 后仍在
        `storage_root/{user_id}` 内（符号链接 / `..` 都无法逃逸）。
        """
        if not isinstance(style_id, str) or not _STYLE_ID_RE.fullmatch(style_id):
            raise ValidationException(f"样式标识非法：{style_id!r}")
        root = self._storage_root
        user_dir = (root / str(user_id)).resolve()
        resolved = (user_dir / f"{style_id}.csl").resolve()
        if resolved.parent != user_dir or not resolved.is_relative_to(root):
            raise ValidationException(f"样式路径非法：{style_id!r}")
        return resolved

    # ===== 内部实现 =====

    def _repository(self) -> CitationStyleRepository:
        return CitationStyleRepository(self._db)

    def _register_custom(self, style_id: str, path: Path) -> None:
        """把上传样式登记进注册表（best-effort，幂等）。

        只有注入**共享**注册表时渲染器才看得到；登记失败只记 warning，
        不推翻「已落盘 + 已入库」的成功状态（准入已通过，失败理论上不可达）。
        """
        try:
            self._registry.register_custom(style_id, path)
        except Exception as exc:
            logger.warning("citation_style_register_failed", style_id=style_id, error=str(exc))

    def _validate_admission(self, data: bytes, base_id: str) -> StyleMeta:
        """写临时文件并调用 M1 准入校验；失败 → `ValidationException(details.reasons)`。

        临时文件名用规范后的 `base_id`（而非客户端文件名 / 随机名），
        使 reasons 里的文件名可读且不泄漏服务器临时路径。
        """
        with tempfile.TemporaryDirectory(prefix="csl-admission-") as tmp_dir:
            candidate = Path(tmp_dir) / f"{base_id}.csl"
            candidate.write_bytes(data)
            try:
                return self._registry.validate_csl_file(candidate)
            except StyleRejected as exc:
                reasons = list(exc.reasons)
                logger.warning("citation_style_rejected", style_id=base_id, reasons=reasons)
                raise ValidationException("；".join(reasons), details={"reasons": reasons}) from exc

    async def _allocate_style_id(
        self, repository: CitationStyleRepository, user_id: UUID, base_id: str
    ) -> str:
        """分配不与预置 / 该用户已有样式（含磁盘残留文件）冲突的 `style_id`。"""
        taken = {meta.style_id for meta in self._registry.list_styles()}
        taken.update(row.style_id for row in await repository.list_for_user(user_id))
        if base_id not in taken and not self.path_for(user_id, base_id).exists():
            return base_id
        suffix = 2
        while True:
            head = base_id[: MAX_STYLE_ID_LENGTH - len(str(suffix)) - 1].strip("-")
            candidate = f"{head or FALLBACK_STYLE_ID}-{suffix}"
            if candidate not in taken and not self.path_for(user_id, candidate).exists():
                return candidate
            suffix += 1

    @staticmethod
    def _is_preset(registry: StyleRegistry, style_id: str) -> bool:
        """是否指向预置样式（含 `gbt7714` 这类别名）。"""
        try:
            resolved = registry.resolve(style_id)
        except ValidationException:
            resolved = style_id
        return resolved in {meta.style_id for meta in registry.list_styles()}

    @staticmethod
    def _preset_out(meta: StyleMeta) -> CitationStyleOut:
        return CitationStyleOut(
            style_id=meta.style_id,
            title=meta.title,
            numbering=meta.numbering,
            ordering=meta.ordering,
            default_locale=meta.default_locale,
            source="preset",
            is_default=meta.is_default or meta.style_id == DEFAULT_STYLE_ID,
        )

    @staticmethod
    def _out_from_meta(
        style_id: str, meta: StyleMeta, *, source: Literal["preset", "custom"]
    ) -> CitationStyleOut:
        return CitationStyleOut(
            style_id=style_id,
            title=meta.title,
            numbering=meta.numbering,
            ordering=meta.ordering,
            default_locale=meta.default_locale,
            source=source,
            is_default=False,
        )

    @staticmethod
    def _custom_out(row: CitationStyle) -> CitationStyleOut:
        return CitationStyleOut(
            style_id=row.style_id,
            title=row.title,
            numbering=cast(Numbering, _coerce_literal(row.numbering, _NUMBERING_VALUES, "none")),
            ordering=cast(Ordering, _coerce_literal(row.ordering, _ORDERING_VALUES, "author-date")),
            default_locale=row.default_locale or "en-US",
            source="custom",
            is_default=False,
        )
