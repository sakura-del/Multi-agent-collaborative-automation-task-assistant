"""预置样式注册表与样式准入校验（Issue #4 M1 §1.2 / ADR 0001 §4.1）。

- `StyleRegistry` 实现 `models.StyleResolver`，供 `renderer.CitationRenderer` 注入；
- 预置样式的元数据是静态表：`numbering` / `ordering` 由应用层负责（引擎会忽略预设编号），
  注册表无需解析 CSL 文件即可回答 `meta()`；
- 自定义 / 上传样式走 `validate_csl_file()`：标准 CSL 1.0.2 结构校验 + CSL-M 构造扫描，
  不合规**显式拒绝**并逐条给出「构造名 + 行号」——绝不静默接受或降级。
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from app.core.exceptions import ValidationException
from app.tools.csl.models import DEFAULT_STYLE_ID, StyleMeta, StyleResolver

CSL_NAMESPACE = "http://purl.org/net/xbiblio/csl"
SUPPORTED_CSL_VERSION_PREFIX = "1.0"

#: CSL-M 构造扫描规则（计划 §1.2 原样）：构造名 → 正则
CSL_M_PATTERNS: tuple[tuple[str, str], ...] = (
    ("layout-locale", r"<layout[^>]*\blocale\s*="),
    ("institution", r"<institution\b"),
    ("has-day", r"has-day\s*="),
    ("citation-range-delimiter", r"citation-range-delimiter"),
    ("term-percent-s", r"<term[^>]*>[^<]*%s[^<]*</term>"),
)

Numbering = Literal["citation-number", "none"]
Ordering = Literal["citation-order", "author-date"]

#: 预置样式静态表：style_id → (人类可读名, numbering, ordering, 缺资产时的 default_locale 回退)
_PRESET_STYLES: tuple[tuple[str, str, Numbering, Ordering, str], ...] = (
    (
        "gbt7714-2025-numeric",
        "GB/T 7714—2025 顺序编码制",
        "citation-number",
        "citation-order",
        "zh-CN",
    ),
    (
        "gbt7714-2015-numeric",
        "GB/T 7714—2015 顺序编码制",
        "citation-number",
        "citation-order",
        "zh-CN",
    ),
    ("apa", "APA 7th Edition 著者-出版年制", "none", "author-date", "en-US"),
    ("ieee", "IEEE 顺序编码制", "citation-number", "citation-order", "en-US"),
    ("nature", "Nature 顺序编码制", "citation-number", "citation-order", "en-GB"),
)

#: 别名 → 归一 style_id（向后兼容旧请求的 `Literal["gbt7714", "apa"]`）
STYLE_ALIASES: dict[str, str] = {
    "gbt7714": DEFAULT_STYLE_ID,
}

#: XML 注释（含跨行）：CSL-M 扫描前剥离，避免注释里的示例片段造成误报
_XML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


class StyleRejected(ValidationException):
    """样式准入拒绝：携带逐条原因（构造名 + 行号），绝不静默接受或降级。"""

    def __init__(self, reasons: Sequence[str]) -> None:
        self._reasons = [reason for reason in reasons if reason] or [
            "样式不符合标准 CSL 1.0.2，且未给出具体原因"
        ]
        super().__init__("；".join(self._reasons))

    @property
    def reasons(self) -> list[str]:
        return list(self._reasons)


class StyleRegistry(StyleResolver):
    """预置样式注册表 + 样式准入校验（可注入 `styles_dir` 便于测试与自定义样式）。"""

    def __init__(self, *, styles_dir: Path | None = None) -> None:
        self._styles_dir = (
            Path(styles_dir) if styles_dir is not None else Path(__file__).parent / "styles"
        )
        # 进程内登记的自定义样式（Issue #4 M2）：style_id → 已通过准入的 StyleMeta。
        # 上传样式落在每用户独立目录，不在 `styles/` 下，故需显式登记才能被渲染器解析。
        self._custom: dict[str, StyleMeta] = {}

    @property
    def styles_dir(self) -> Path:
        """样式资产目录（预置 `.csl` 与自定义样式同目录）。"""
        return self._styles_dir

    # ===== StyleResolver 协议 =====

    def resolve(self, style_id: str) -> str:
        """别名归一 + 未知 id 校验；空值回落到默认样式。"""
        raw = (style_id or "").strip()
        if not raw:
            return DEFAULT_STYLE_ID
        resolved = STYLE_ALIASES.get(raw, raw)
        if resolved not in self._known_ids():
            raise ValidationException(f"未知引用样式：{style_id}")
        return resolved

    def meta(self, style_id: str) -> StyleMeta:
        """返回样式元数据；未知 id → ValueError（协议约定）。

        预置样式用静态表；自定义样式（已登记）用登记时的准入结果；
        目录内的其他 `.csl` 回退到 `validate_csl_file()` 现场推断。
        """
        raw = (style_id or "").strip()
        resolved = STYLE_ALIASES.get(raw, raw)
        preset = self._preset_meta(resolved)
        if preset is not None:
            return preset
        custom = self._custom.get(resolved)
        if custom is not None:
            return custom
        path = self._styles_dir / f"{resolved}.csl"
        if resolved and path.is_file():
            return self.validate_csl_file(path)
        raise ValueError(f"未知引用样式：{style_id}")

    # ===== 自定义样式登记（Issue #4 M2）=====

    def register_custom(self, style_id: str, path: Path) -> StyleMeta:
        """登记一个**已上传**的自定义样式（幂等）。

        登记前跑与预置样式**完全相同**的准入校验（`validate_csl_file`），
        不合规直接抛 `StyleRejected`——不会因为「是用户上传的」就放宽标准。
        预置样式优先：`style_id` 与预置冲突时拒绝登记，避免上传文件覆盖 `apa` 等。
        """
        resolved = (style_id or "").strip()
        if not resolved:
            raise ValidationException("自定义样式缺少 style_id")
        if self._preset_meta(resolved) is not None:
            raise ValidationException(f"自定义样式不得覆盖预置样式：{resolved}")

        source = Path(path)
        meta = self.validate_csl_file(source)  # 不合规 → StyleRejected
        self._custom[resolved] = meta.model_copy(
            update={"style_id": resolved, "path": source, "is_default": False}
        )
        return self._custom[resolved]

    def unregister_custom(self, style_id: str) -> None:
        """注销自定义样式（幂等）；预置样式不受影响。"""
        self._custom.pop((style_id or "").strip(), None)

    def custom_style_ids(self) -> list[str]:
        """当前已登记的自定义样式 ID（供调试/诊断，不参与协议）。"""
        return sorted(self._custom)

    # ===== 注册表查询 =====

    def get(self, style_id: str) -> Path:
        """返回样式文件路径；未知 id 或资产缺失 → ValidationException。"""
        resolved = self.resolve(style_id)
        custom = self._custom.get(resolved)
        if custom is not None:
            if not custom.path.is_file():
                raise ValidationException(
                    f"自定义样式文件不存在：{custom.path}（style_id={resolved}）"
                )
            return custom.path
        path = self._styles_dir / f"{resolved}.csl"
        if not path.is_file():
            raise ValidationException(f"样式文件不存在：{path}（style_id={resolved}）")
        return path

    def list_styles(self) -> list[StyleMeta]:
        """列出预置样式（默认样式在首位）。"""
        metas: list[StyleMeta] = []
        for style_id, *_rest in _PRESET_STYLES:
            meta = self._preset_meta(style_id)
            if meta is not None:
                metas.append(meta)
        return metas

    # ===== 准入校验 =====

    def validate_csl_file(self, path: Path) -> StyleMeta:
        """准入校验：结构 + CSL-M 扫描；不合规 → `StyleRejected(reasons)`。"""
        source = Path(path)
        try:
            text = source.read_text(encoding="utf-8")
        except OSError as exc:
            detail = exc.strerror or str(exc)
            raise StyleRejected([f"{source.name}:1: 样式文件不可读（{detail}）"]) from exc

        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            line = _parse_error_line(exc)
            raise StyleRejected([f"{source.name}:{line}: XML 解析失败（{exc}）"]) from exc

        reasons = _structure_reasons(source.name, text, root)
        # 注释里的 CSL-M 片段不是生效构造（vendored GB/T 7714—2015 样式即大量注释引用），
        # 扫描前先剥离注释并保持换行数不变，使行号仍指向原文。
        reasons.extend(_csl_m_reasons(source.name, _strip_xml_comments(text)))
        if reasons:
            raise StyleRejected(reasons)

        numbering, ordering = _infer_numbering_ordering(root)
        return StyleMeta(
            style_id=source.stem,
            title=_info_title(root) or source.stem,
            numbering=numbering,
            ordering=ordering,
            path=source,
            default_locale=_default_locale(root),
        )

    # ===== 内部实现 =====

    def _preset_meta(self, style_id: str) -> StyleMeta | None:
        for preset_id, title, numbering, ordering, fallback_locale in _PRESET_STYLES:
            if preset_id != style_id:
                continue
            path = self._styles_dir / f"{preset_id}.csl"
            return StyleMeta(
                style_id=preset_id,
                title=title,
                numbering=numbering,
                ordering=ordering,
                path=path,
                # R-M1-1：优先读样式文件声明的 default-locale，资产缺失时按样式族回退
                default_locale=_declared_default_locale(path) or fallback_locale,
                is_default=preset_id == DEFAULT_STYLE_ID,
            )
        return None

    def _known_ids(self) -> set[str]:
        ids = {style_id for style_id, *_rest in _PRESET_STYLES}
        if self._styles_dir.is_dir():
            ids.update(path.stem for path in self._styles_dir.glob("*.csl"))
        ids.update(self._custom)
        return ids


# ===== 校验辅助 =====


def _structure_reasons(filename: str, text: str, root: ET.Element) -> list[str]:
    """① 必需 CSL 1.0.2 结构校验：`<style>` 根 + version + `<info>/<title>` + bibliography|citation。"""
    reasons: list[str] = []
    style_line = _line_of(text, "<style")
    root_tag = _local_name(root.tag)
    if root_tag != "style":
        reasons.append(f"{filename}:1: 根元素必须是 <style>（实际 <{root_tag}>）")

    version = (root.get("version") or "").strip()
    if not version:
        reasons.append(f"{filename}:{style_line}: <style> 缺少 version 属性")
    elif not version.startswith(SUPPORTED_CSL_VERSION_PREFIX):
        reasons.append(
            f"{filename}:{style_line}: 不支持的 CSL 版本 {version}" "（本产品仅支持标准 CSL 1.0.x）"
        )

    info = _find_child(root, "info")
    if info is None:
        reasons.append(f"{filename}:{style_line}: 缺少 <info> 元素")
    elif not (_info_title(root) or "").strip():
        reasons.append(f"{filename}:{_line_of(text, '<info')}: <info> 缺少非空 <title>")

    if _find_child(root, "bibliography") is None and _find_child(root, "citation") is None:
        reasons.append(
            f"{filename}:{style_line}: 缺少 <bibliography> 或 <citation>"
            "（不是可渲染的 CSL 样式）"
        )
    return reasons


def _csl_m_reasons(filename: str, text: str) -> list[str]:
    """② CSL-M 构造扫描：命中逐条给出「构造名 + 行号」。"""
    reasons: list[str] = []
    for name, pattern in CSL_M_PATTERNS:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            line = text.count("\n", 0, match.start()) + 1
            snippet = " ".join(match.group(0).split())
            if len(snippet) > 80:
                snippet = f"{snippet[:77]}..."
            reasons.append(
                f"{filename}:{line}: 检测到 CSL-M 私有构造 `{name}`（{snippet}）"
                "——该样式依赖 CSL-M 扩展，本产品仅支持标准 CSL 1.0.2，已拒绝"
            )
    return reasons


def _infer_numbering_ordering(root: ET.Element) -> tuple[Numbering, Ordering]:
    """从 `<citation>` 推断编号与排序：出现 citation-number → 顺序编码制，否则著者-出版年制。"""
    citation = _find_child(root, "citation")
    if citation is not None and "citation-number" in ET.tostring(citation, encoding="unicode"):
        return "citation-number", "citation-order"
    return "none", "author-date"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find_child(root: ET.Element, name: str) -> ET.Element | None:
    for child in root:
        if _local_name(child.tag) == name:
            return child
    return None


def _info_title(root: ET.Element) -> str | None:
    info = _find_child(root, "info")
    if info is None:
        return None
    for child in info:
        if _local_name(child.tag) == "title":
            return "".join(child.itertext()).strip()
    return None


def _default_locale(root: ET.Element) -> str:
    return (root.get("default-locale") or "").strip() or "en-US"


def _declared_default_locale(path: Path) -> str | None:
    """读取样式文件根元素声明的 `default-locale`；文件缺失或非法 → None（调用方回退）。"""
    try:
        root = ET.fromstring(path.read_text(encoding="utf-8"))
    except (OSError, ET.ParseError):
        return None
    return (root.get("default-locale") or "").strip() or None


def _line_of(text: str, needle: str) -> int:
    index = text.find(needle)
    if index < 0:
        return 1
    return text.count("\n", 0, index) + 1


def _strip_xml_comments(text: str) -> str:
    """把 XML 注释替换为等量换行：注释内容不参与 CSL-M 扫描，行号仍与原文一致。"""

    def _blank(match: re.Match[str]) -> str:
        return "\n" * match.group(0).count("\n")

    return _XML_COMMENT_RE.sub(_blank, text)


def _parse_error_line(exc: ET.ParseError) -> int:
    position = getattr(exc, "position", None)
    if isinstance(position, tuple) and position and isinstance(position[0], int):
        return position[0]
    match = re.search(r"line (\d+)", str(exc))
    return int(match.group(1)) if match else 1
