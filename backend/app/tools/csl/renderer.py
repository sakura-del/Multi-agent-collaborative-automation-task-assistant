"""渲染核心（Issue #4 M1 计划 §1.3）：逐条 locale + 应用层编号/排序 + 内联占位替换。

渲染流程（严格按 §1.3 的 ①–⑥）：

① 归一 ``style_id``（``resolver.resolve``）；
② 全局排序（``StyleMeta.ordering``：``citation-order`` / ``author-date``）；
③ ``numbering="citation-number"`` 时按排序结果分配 ``1..n``；
④ **按 ``item.language`` 分组、每组用对应 locale（zh-CN / en-US）逐条渲染**
   （实测：整批单一 locale 会把英文条目写成「等」、中文条目写成 "et al." → 必须分组）；
⑤ 剥离引擎自带编号 ``^\\[\\d+\\]\\s*``（实测：引擎忽略预设 citation-number，单条渲染恒输出 ``[1]``）
   后前置应用层编号；
⑥ 按排序结果合并条目、marker 与 ``item_order``。

引擎适配注意事项（本机实测 citeproc-py 0.11.1）：

- ``bibliography()`` 的元素既可能是 ``str`` 子类也可能是 ``MixedString``（list 子类），必须 ``str()`` 归一；
- ``cite()`` 返回值同理（作者-出版年制的 marker 常是 ``MixedString``）；
- 样式对象按 ``(路径, locale)`` 缓存，避免逐条重复解析 XML。

失败兜底（ADR §4.1「绝不静默输出半成品」）：单条渲染异常或输出为空时，记录可观测 warning
并回退 :func:`fallback_bibliography_entry` 的内置极简格式化器；`:meth:`CitationRenderer.resolver``
缺省时**在方法内**导入 ``styles.StyleRegistry``，避免 models ↔ styles ↔ renderer 循环依赖。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from citeproc import (
    Citation,
    CitationItem,
    CitationStylesBibliography,
    CitationStylesStyle,
    formatter,
)
from citeproc.source.json import CiteProcJSON

from app.tools.csl.models import (
    DEFAULT_STYLE_ID,
    TYPE_IDENTIFIERS,
    CslItem,
    StyleResolver,
    author_sort_text,
    normalize_title,
    sort_key_text,
)

#: 引擎自带的 bibliography 编号前缀（冻结规则，计划 §1.3；裁决 R-M1-3① 放宽）：
#: 既覆盖 ``[1] ``（GB/T、IEEE），也覆盖 nature 的裸 ``1.``；**仅在行首、仅剥离一次**。
ENGINE_NUMBER_PATTERN = re.compile(r"^(?:\[\d+\]\s*|\d+[.)]\s*)")

#: 正文引用占位（冻结规则，计划 §1.3；裁决 R-M1-2 放宽字符类）：
#: 「非空白且非 `]`」——占位以 `]]` 收尾，语义无歧义，且不会再因 ref_id 形态
#:（DOI 后缀可含 `/ + # ? & =` 等）漂移而失配。
INLINE_PATTERN = re.compile(r"\[\[cite:([^\s\]]+)\]\]")

#: 未被 INLINE_PATTERN 覆盖的残留占位（语法不符），用于给出可观测 warning。
LEFTOVER_CITE_MARKER = "[[cite:"


@dataclass(frozen=True)
class RenderedBibliography:
    """一次渲染的结果（计划 §1.3）。

    ``warnings`` 是**加法字段**（计划的字段清单只有前四个）：渲染降级与内联替换的未知 id
    必须可观测（任务验收点 3/ADR §4.1），因此挂在结果对象上由调用方读取。
    """

    style_id: str
    entries: list[str]
    """最终参考文献行（序号 + 内容，按 ordering 排序、已编号）。"""

    marker_by_item_id: dict[str, str]
    """ref_id → 正文引用标记（``[1]`` 或 ``(牛耕田 等, 2024)``）。"""

    item_order: list[str]
    """排序后的 ref_id 顺序（与 ``entries`` 一一对应）。"""

    warnings: list[str] = field(default_factory=list)
    """渲染降级 / 未知引用 id / 重复 ref_id 等可观测告警（按消息去重）。"""


def locale_for_language(language: str | None, *, default: str = "en-US") -> str:
    """``item.language`` → citeproc locale：``zh*`` → zh-CN、``en*`` → en-US，其它用兜底值。"""
    text = str(language or "").strip().replace("_", "-")
    head = text.split("-", 1)[0].casefold()
    if head == "zh":
        return "zh-CN"
    if head == "en":
        return "en-US"
    return default or "en-US"


def sort_items(items: Sequence[CslItem], ordering: str) -> list[CslItem]:
    """应用层全局排序（计划 §1.3 ②）。

    - ``citation-order``：保持入参顺序（即正文首次引用顺序）；
    - ``author-date``：按（第一作者 → 年份 → 归一题名）排序。⚠️ 无拼音库，
      中文姓名按 Unicode 码位排序（已知限制，M2 可接入拼音）；
    - 未知 ordering → ``ValueError``（绝不静默按输入顺序输出）。
    """
    if ordering == "citation-order":
        return list(items)
    if ordering == "author-date":
        return sorted(items, key=_author_date_key)
    raise ValueError(f"未知 ordering：{ordering!r}（仅支持 citation-order / author-date）")


def _author_date_key(item: CslItem) -> tuple[str, str, int, str]:
    """作者-出版年排序键：第一作者（literal 优先）→ 年份 → 归一题名。

    第一作者按 `sort_key_text` 取键：**中文按拼音**（`赵` → `zhao`），
    西文按 casefold；同音不同字回落到原文码位保证确定性排序。
    """
    first_text = author_sort_text(item.author[0]) if item.author else ""
    return (
        sort_key_text(first_text),
        first_text,
        item.issued_year if item.issued_year is not None else 0,
        normalize_title(item.title),
    )


def fallback_bibliography_entry(item: CslItem) -> str:
    """ADR §4.1 的运行时兜底：样式渲染失败时的内置极简格式化器（确定性、可读）。"""
    segments: list[str] = []
    authors = ", ".join(text for text in (author_sort_text(name) for name in item.author) if text)
    if authors:
        segments.append(f"{authors}.")
    if item.title:
        identifier = TYPE_IDENTIFIERS.get(item.type, "")
        segments.append(f"{item.title}[{identifier}]." if identifier else f"{item.title}.")
    if item.container_title:
        segments.append(f"{item.container_title},")
    if item.publisher:
        segments.append(f"{item.publisher},")
    if item.issued_year is not None:
        segments.append(f"{item.issued_year}.")
    return " ".join(segments) or item.id


def fallback_citation_marker(
    item: CslItem,
    *,
    numbering: str,
    number: int | None = None,
    locale: str = "en-US",
) -> str:
    """内置极简 marker（仅渲染降级时使用）：数字制 ``[n]``，著者-出版年制 ``(作者, 年)``。"""
    if numbering == "citation-number":
        return f"[{number}]" if number is not None else "[?]"
    if item.author:
        name = author_sort_text(item.author[0])
        if len(item.author) > 3:
            name = f"{name} {'等' if locale == 'zh-CN' else 'et al.'}"
    else:
        name = item.title[:20]
    year = str(item.issued_year) if item.issued_year is not None else "n.d."
    return f"({name}, {year})" if name else f"({year})"


def _add_warning(bibliography: RenderedBibliography, message: str) -> None:
    """写入可观测告警（同一消息只记一次）。"""
    if message not in bibliography.warnings:
        bibliography.warnings.append(message)


class CitationRenderer:
    """CSL 渲染器：CSL-JSON → 逐条 locale 渲染 → 应用层编号/排序 → 内联标记。"""

    def __init__(self, *, resolver: StyleResolver | None = None) -> None:
        """``resolver`` 缺省时惰性构造 ``styles.StyleRegistry()``（方法内导入，避免循环依赖）。"""
        self._resolver = resolver

    @property
    def resolver(self) -> StyleResolver:
        """样式解析器（缺省时惰性构造默认注册表）。"""
        if self._resolver is None:
            from app.tools.csl.styles import StyleRegistry

            self._resolver = StyleRegistry()
        return self._resolver

    def render(
        self,
        items: Sequence[CslItem],
        *,
        style_id: str = DEFAULT_STYLE_ID,
    ) -> RenderedBibliography:
        """按计划 §1.3 的 ①–⑥ 渲染参考文献；空输入 → 空结果。"""
        resolved = self.resolver.resolve(style_id)
        if not items:
            return RenderedBibliography(
                style_id=resolved, entries=[], marker_by_item_id={}, item_order=[]
            )

        meta = self.resolver.meta(resolved)
        ordered = sort_items(items, meta.ordering)
        numbers = (
            {item.id: index for index, item in enumerate(ordered, start=1)}
            if meta.numbering == "citation-number"
            else {}
        )
        bibliography = RenderedBibliography(
            style_id=resolved,
            entries=[],
            marker_by_item_id={},
            item_order=[item.id for item in ordered],
        )

        seen: set[str] = set()
        for item in ordered:
            if item.id in seen:
                _add_warning(bibliography, f"重复的 ref_id：{item.id}（marker 以首个为准）")
            seen.add(item.id)

        style_cache: dict[tuple[str, str], CitationStylesStyle] = {}
        for item in ordered:
            locale = locale_for_language(item.language, default=meta.default_locale)
            number = numbers.get(item.id)
            try:
                entry_text, engine_marker = self._render_item(
                    meta.path, locale, item, style_cache, bibliography
                )
            except Exception as exc:  # 样式解析 / 引擎渲染任何异常都不得让整篇参考文献失败
                _add_warning(
                    bibliography,
                    f"样式 {resolved} 渲染条目 {item.id} 失败"
                    f"（{type(exc).__name__}: {exc}），已回退内置极简格式化器",
                )
                entry_text, engine_marker = "", ""
            if not entry_text.strip():
                entry_text = fallback_bibliography_entry(item)
                engine_marker = ""

            if meta.numbering == "citation-number":
                body = ENGINE_NUMBER_PATTERN.sub("", entry_text, count=1)
                bibliography.entries.append(f"[{number}] {body}" if body else f"[{number}]")
            else:
                bibliography.entries.append(entry_text)

            if meta.numbering == "citation-number":
                marker = f"[{number}]"
            else:
                marker = engine_marker.strip() or fallback_citation_marker(
                    item, numbering=meta.numbering, number=number, locale=locale
                )
            bibliography.marker_by_item_id.setdefault(item.id, marker)

        return bibliography

    def substitute_inline(self, text: str, bibliography: RenderedBibliography) -> str:
        """把 ``[[cite:ref_id]]`` 替换为 marker；未知 ref_id **保留原样**并计入 warnings。"""

        def _replace(match: re.Match[str]) -> str:
            ref_id = match.group(1)
            marker = bibliography.marker_by_item_id.get(ref_id)
            if marker is None:
                _add_warning(
                    bibliography,
                    f"未知引用 id：{ref_id}（已保留原占位 {match.group(0)}）",
                )
                return match.group(0)
            return marker

        substituted = INLINE_PATTERN.sub(_replace, text)
        # 只有「语法不符、正则压根匹配不到」的残留才额外告警；未知 id 已被保留并单独告警，
        # 不重复计一次（占位起始数 > 有效占位数 ⇒ 存在语法不符的残留）。
        marker_starts = substituted.count(LEFTOVER_CITE_MARKER)
        if marker_starts > len(INLINE_PATTERN.findall(substituted)):
            _add_warning(
                bibliography,
                f"存在无法解析的 {LEFTOVER_CITE_MARKER}...]] 占位（不符合 INLINE_PATTERN 语法），已保留原样",
            )
        return substituted

    def _render_item(
        self,
        style_path: Path,
        locale: str,
        item: CslItem,
        style_cache: dict[tuple[str, str], CitationStylesStyle],
        bibliography: RenderedBibliography,
    ) -> tuple[str, str]:
        """渲染单条条目与行内 marker；异常向上抛（由 :meth:`render` 兜底并记 warning）。"""
        key = (str(style_path), locale)
        style = style_cache.get(key)
        if style is None:
            style = CitationStylesStyle(str(style_path), locale=locale, validate=False)
            style_cache[key] = style

        source = CiteProcJSON([item.to_csl_json()])
        engine = CitationStylesBibliography(style, source, formatter.plain)
        citation = Citation([CitationItem(item.id)])
        engine.register(citation)

        raw_entries = [str(entry) for entry in engine.bibliography()]
        raw_marker = engine.cite(citation, lambda _missing: None)

        entry = raw_entries[0] if raw_entries else ""
        marker = str(raw_marker) if raw_marker else ""
        if not entry.strip():
            _add_warning(
                bibliography,
                f"样式 {bibliography.style_id} 对条目 {item.id} 输出为空，已回退内置极简格式化器",
            )
        return entry, marker
