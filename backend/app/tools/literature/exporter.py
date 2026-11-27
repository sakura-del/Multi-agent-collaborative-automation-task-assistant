"""文献导出适配器：ReviewReport → Markdown / DOCX 产物（Plan 3 Task 5）。

安全约定：
- artifact_id 由服务端 `uuid4().hex` 生成，客户端只用于查找，不能指定路径；
- 落盘路径固定为 `<artifact_root>/<user_id>/<task_id>/<artifact_id><suffix>`；
- Markdown 中对原始 HTML 做转义，并拒绝 `javascript:` / `data:` / `file:` 等非 HTTP(S) 链接；
- `resolve_for_user()` 只接受严格的 32 位十六进制 ID，跨用户/跨任务/路径穿越一律 404。

Issue #3 / 计划 §2.6：`stats["reference_verification"]` 存在且正文没有 `## 引用核验` 时，
追加核验统计章节；参考文献行带核验标记。Markdown 与 DOCX 走同一条组装路径。
无核验信息的历史 payload 渲染与既有版本逐字一致。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from docx import Document as DocxDocument

from app.core.exceptions import NotFoundException, ValidationException
from app.core.logging import get_logger
from app.orchestration.contracts import ReviewReport
from app.tools.csl.models import clean_csl_title, strip_html_tags
from app.tools.literature.models import Artifact

logger = get_logger(__name__)

_ARTIFACT_ID = re.compile(r"^[0-9a-f]{32}$")
_UNSAFE_SCHEME = re.compile(r"(?i)\b(?:javascript|data|file|vbscript)\s*:")
_HTML_ESCAPES = (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"))
_INLINE_E_GROUP_RE = re.compile(r"(?:\[E\d+\](?:\s*[,、;；]?\s*(?=\[E\d+\]))?)+")
_SINGLE_E_RE = re.compile(r"\[E(\d+)\]")
_ABSTRACT_CITE_RE = re.compile(
    r"\s*(?:\[\[cite:[^\]]+\]\]|\[E\d+\]|\[\d+(?:(?:\s*,\s*|\s*[-–]\s*)\d+)*\])"
)
_MODEL_INFER_RE = re.compile(r"(?:模型推断[：:]\s*|（模型推断）|\(模型推断\))")
_EXTRACT_NOISE_HEADING_RE = re.compile(r"^#{1,3}\s+.*(?:证据提取|证据局限性说明)\s*$")
_EXISTING_HEADING_PREFIX_RE = re.compile(
    r"^(?:\d+(?:\.\d+)*\.?\s+|[一二三四五六七八九十]+[、.．]\s*|（[一二三四五六七八九十]+）\s*)"
)
_RESIDUAL_HTML_TAG_RE = re.compile(r"</?(?:p|sup|sub|span|div|strong|em|b|i|br)\b[^>]*>", re.I)
_HTML_SUP_BLOCK_RE = re.compile(r"<sup\b[^>]*>.*?</sup>", re.I | re.DOTALL)

#: 判定态 → 中文标签（与复盘 Agent / 校验报告的展示口径保持一致）
_VERDICT_LABELS: tuple[tuple[str, str], ...] = (
    ("verified", "已验证"),
    ("metadata_mismatch", "元数据不符"),
    ("not_found", "未找到"),
    ("unverified", "未核验"),
)

#: 中文源跳过态（Issue #67）：只在确有中文源条目时展示，避免旧任务凭空多出「0 条」。
_CN_VERDICT_VALUE = "cn_source_unverified"
_CN_VERDICT_LABEL = "中文源·未核验"
_VERDICT_LABEL_MAP: dict[str, str] = {**dict(_VERDICT_LABELS), _CN_VERDICT_VALUE: _CN_VERDICT_LABEL}

#: 核验统计章节标题；复盘产物已带该章节时导出侧不得重复追加
_REFERENCE_SECTION = "## 引用核验"


def _retraction_label(value: Any) -> str:
    """撤稿三态标签（与复盘 Agent 口径一致，故各保留一份实现避免跨层依赖）。

    - ``True`` → 已撤稿；
    - ``False`` → 来源明确未标记撤稿，**不等于「安全」**；
    - ``None`` / 缺失 → 撤稿状态未知（来源不可用或无该字段）。
    """
    if value is True:
        return "已撤稿"
    if value is False:
        return "未标记撤稿"
    return "撤稿状态未知"


def _verification_mark(status: Any, is_retracted: Any = None) -> str:
    """参考文献行的核验标记；无核验信息（历史 payload）时返回空串，保持既有渲染不变。"""
    if not isinstance(status, str) or not status:
        return ""
    label = _VERDICT_LABEL_MAP.get(status, status)
    return f"（核验：{label}；{_retraction_label(is_retracted)}）"


def _summary_count(summary: Mapping[str, Any], key: str) -> int:
    """统计值容错：非整数字段按 0 处理，避免脏 stats 让导出失败。"""
    value = summary.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


def _verification_line(summary: Mapping[str, Any]) -> str:
    """`## 引用核验` 章节正文（与复盘 Agent 的展示口径一致）。"""
    counts = " / ".join(
        f"{label} {_summary_count(summary, verdict)} 条" for verdict, label in _VERDICT_LABELS
    )
    # 中文源跳过态（Issue #67）：有中文源时才追加，避免旧任务显示「中文源·未核验 0 条」。
    cn_count = _summary_count(summary, _CN_VERDICT_VALUE)
    if cn_count > 0:
        counts += f" / {_CN_VERDICT_LABEL} {cn_count} 条"
    switch = "开启" if summary.get("intercepted") else "关闭"
    return (
        f"引用核验：{counts} / 已拦截 {_summary_count(summary, 'dropped')} 条；拦截开关：{switch}"
    )


class LiteratureExporter:
    """把 `ReviewReport` 落地为受控目录下的 Markdown / DOCX 产物。"""

    def __init__(self, root: str | Path, *, style_resolver: Any | None = None) -> None:
        self.root = Path(root).resolve()
        #: 样式解析器（Issue #4 M2）：传入共享 `StyleRegistry` 才能渲染用户上传的样式；
        #: 缺省时渲染器会自行构造一个只含预置样式的注册表。
        self._style_resolver = style_resolver

    # ===== 导出 =====

    def export_markdown(
        self,
        user_id: UUID,
        task_id: UUID,
        report: ReviewReport,
        *,
        citation_style: str | None = None,
    ) -> Artifact:
        """写出生成的 Markdown（HTML 已转义、链接已校验）。

        给出 `citation_style` 时，参考文献由 CSL 渲染器**确定性渲染**（Issue #4）；
        `[[cite:ref_id]]` 占位替换为样式对应的正文标记；`[E{n}]` 证据标记不受影响。
        """
        raw = self._compose_document(report, citation_style=citation_style)
        payload = self._escape_html(raw).encode("utf-8")
        return self._write(user_id, task_id, kind="markdown", suffix=".md", payload=payload)

    def export_docx(
        self,
        user_id: UUID,
        task_id: UUID,
        report: ReviewReport,
        *,
        citation_style: str | None = None,
    ) -> Artifact:
        """按同一标题层级写出 DOCX（python-docx）；出口自动清洗内部标记与重复章节。"""
        raw = self._compose_document(report, citation_style=citation_style)
        cleaned = _clean_docx_export_document(raw, report=report)
        payload = self._build_docx_bytes(cleaned)
        return self._write(user_id, task_id, kind="docx", suffix=".docx", payload=payload)

    # ===== 查找 =====

    def resolve_for_user(self, user_id: UUID, task_id: UUID, artifact_id: str) -> Path:
        """按 artifact_id 解析当前用户当前任务的产物路径；其余一律 404。"""
        if not isinstance(artifact_id, str) or not _ARTIFACT_ID.fullmatch(artifact_id):
            raise NotFoundException("产物不存在")
        directory = (self.root / str(user_id) / str(task_id)).resolve()
        for suffix in (".md", ".docx"):
            candidate = directory / f"{artifact_id}{suffix}"
            if candidate.is_file() and candidate.resolve().parent == directory:
                return candidate
        raise NotFoundException("产物不存在")

    # ===== 内部实现 =====

    def _task_dir(self, user_id: UUID, task_id: UUID) -> Path:
        return self.root / str(user_id) / str(task_id)

    def _write(
        self,
        user_id: UUID,
        task_id: UUID,
        *,
        kind: str,
        suffix: str,
        payload: bytes,
    ) -> Artifact:
        artifact_id = uuid4().hex
        filename = f"{artifact_id}{suffix}"
        directory = self._task_dir(user_id, task_id)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / filename
        target.write_bytes(payload)
        return Artifact(
            artifact_id=artifact_id,
            kind=kind,  # type: ignore[arg-type]
            filename=filename,
            download_name=f"literature_review_{task_id}{suffix}",
            size=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            created_at=datetime.now(UTC),
        )

    def _compose_document(self, report: ReviewReport, *, citation_style: str | None = None) -> str:
        """组装完整文档：标题 → 摘要 → 正文 → 局限性 → 参考文献 → 引用核验。

        给出 `citation_style` 时（Issue #4）：正文里的 `[[cite:ref_id]]` 先替换为样式标记，
        再用 CSL 渲染结果**替换**既有的 `## 参考文献` 段落（幂等，不受复盘侧文本影响）；
        未给出时完全保持既有行为。
        """
        body = (report.final_markdown or "").strip()
        stats = report.stats or {}
        title = str(stats.get("title") or self._first_heading(body) or "文献综述")
        # Fix 2：没有机器摘要时不要用正文首段冒充「## 摘要」，否则与正文第一段逐字重复
        abstract = str(stats.get("abstract") or "").strip()
        raw_limitations = stats.get("limitations") or report.issues or []
        limitations = [
            item
            for item in (
                raw_limitations if isinstance(raw_limitations, list) else [raw_limitations]
            )
            if not _is_empty_limitation_item(item)
        ]

        rendered_lines: list[str] | None = None
        if citation_style and report.bibliography:
            rendered_lines = self._render_bibliography(report, citation_style)
            body = self._substitute_inline_citations(body, report, citation_style)

        parts = [f"# {title}"]
        if abstract and "## 摘要" not in body:
            parts.append(f"## 摘要\n{abstract}")
        if body:
            parts.append(body)
        if limitations and "## 局限性" not in body:
            parts.append("## 局限性\n" + "\n".join(f"- {item}" for item in limitations))
        if rendered_lines is not None:
            # CSL 渲染结果优先：替换既有「参考文献」段落（保留其后的「引用核验」段落）
            document = "\n\n".join(parts)
            document = self._replace_bibliography_section(
                document, "## 参考文献\n" + "\n".join(rendered_lines)
            )
        else:
            if report.bibliography and "## 参考文献" not in body:
                lines = [
                    self._bibliography_line(index, entry)
                    for index, entry in enumerate(report.bibliography, start=1)
                ]
                parts.append("## 参考文献\n" + "\n".join(lines))
            document = "\n\n".join(parts)

        # Fix 3/6：相邻重复段落去重、空「局限性（无）」整节剔除
        document = _cleanup_export_body(document)
        # Fix 1：正文有 [n] 引用却没有任何参考文献条目时，用 report.bibliography 兜底补齐
        if _has_numeric_citations(document) and not _has_bibliography_entries(document):
            if report.bibliography:
                lines = [
                    self._bibliography_line(index, entry)
                    for index, entry in enumerate(report.bibliography, start=1)
                ]
                document = self._replace_bibliography_section(
                    document, "## 参考文献\n" + "\n".join(lines)
                )
            else:
                logger.error("export_bibliography_missing", cited=True)

        verification = stats.get("reference_verification")
        if isinstance(verification, dict) and _REFERENCE_SECTION not in document:
            document = f"{document}\n\n{_REFERENCE_SECTION}\n{_verification_line(verification)}"

        self._validate_links(document)
        return document

    # ===== Issue #4：CSL 渲染（懒导入，避免 tools 包之间的循环依赖）=====

    def _render_bibliography(self, report: ReviewReport, citation_style: str) -> list[str]:
        """用 CSL 渲染器产出参考文献行；未知样式由渲染器抛 ValidationException。"""
        from app.tools.csl.models import csl_item_from_bibliography_entry
        from app.tools.csl.renderer import CitationRenderer

        items = []
        entry_by_id: dict[str, dict] = {}
        for entry in report.bibliography:
            item = csl_item_from_bibliography_entry(entry)
            if item is not None:
                items.append(item)
                entry_by_id[item.id] = entry
        renderer = CitationRenderer(resolver=self._style_resolver)
        rendered = renderer.render(items, style_id=citation_style)
        lines = list(rendered.entries)
        # CSL 渲染会替换整段参考文献：逐条把核验/撤稿标记补回同一行，
        # 否则默认样式导出会丢失刚加入的撤稿状态。
        for index, ref_id in enumerate(rendered.item_order):
            if index >= len(lines):
                break
            matched_entry = entry_by_id.get(ref_id)
            if matched_entry is None:
                continue
            lines[index] += _verification_mark(
                matched_entry.get("verification_status"), matched_entry.get("is_retracted")
            )
        return lines

    def _substitute_inline_citations(
        self, body: str, report: ReviewReport, citation_style: str
    ) -> str:
        """把正文 `[[cite:ref_id]]` 替换为样式对应的正文标记（无占位时原样返回）。"""
        from app.tools.csl.models import csl_item_from_bibliography_entry
        from app.tools.csl.renderer import INLINE_PATTERN, CitationRenderer

        if not INLINE_PATTERN.search(body):
            return body
        items = []
        for entry in report.bibliography:
            item = csl_item_from_bibliography_entry(entry)
            if item is not None:
                items.append(item)
        renderer = CitationRenderer(resolver=self._style_resolver)
        rendered = renderer.render(items, style_id=citation_style)
        return renderer.substitute_inline(body, rendered)

    @staticmethod
    def _replace_bibliography_section(document: str, section: str) -> str:
        """替换 `## 参考文献` 段落（到下一个 `## ` 标题为止）；不存在则追加。"""
        match = re.search(r"^## 参考文献[ \t]*$", document, re.MULTILINE)
        if match is None:
            return f"{document}\n\n{section}"
        start = match.start()
        rest = document[match.end() :]
        following = re.search(r"^## ", rest, re.MULTILINE)
        end = match.end() + following.start() if following else len(document)
        prefix = document[:start].rstrip()
        suffix = document[end:].strip()
        combined = f"{prefix}\n\n{section}" if prefix else section
        return f"{combined}\n\n{suffix}" if suffix else combined

    @staticmethod
    def _bibliography_line(index: int, entry: dict) -> str:
        title = str(entry.get("title") or entry.get("source_id") or "未命名文献")
        url = str(entry.get("source_url") or "")
        source_id = str(entry.get("source_id") or "")
        suffix = f" — {url}" if url else ""
        prefix = f"[{index}] {title}"
        if source_id and source_id not in title:
            prefix = f"{prefix} ({source_id})"
        mark = _verification_mark(entry.get("verification_status"), entry.get("is_retracted"))
        return f"- {prefix}{suffix}{mark}"

    @staticmethod
    def _first_heading(body: str) -> str:
        for line in body.splitlines():
            if line.startswith("# "):
                return line[2:].strip()
        return ""

    @staticmethod
    def _first_paragraph(body: str) -> str:
        for line in body.splitlines():
            text = line.strip()
            if text and not text.startswith("#") and not text.startswith("-"):
                return text[:300]
        return ""

    @staticmethod
    def _validate_links(document: str) -> None:
        """只拒绝非 HTTP(S) 链接协议；原始 HTML 由 `_escape_html()` 转义成惰性文本。"""
        if _UNSAFE_SCHEME.search(document):
            raise ValidationException("Markdown 含非 HTTP(S) 链接，已拒绝导出")

    @staticmethod
    def _escape_html(document: str) -> str:
        escaped = document
        for source, target in _HTML_ESCAPES:
            escaped = escaped.replace(source, target)
        return escaped

    @staticmethod
    def _build_docx_bytes(document: str) -> bytes:
        docx = DocxDocument()
        for raw_line in document.splitlines():
            line = raw_line.rstrip()
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("### "):
                docx.add_heading(stripped[4:], level=3)
            elif stripped.startswith("## "):
                docx.add_heading(stripped[3:], level=2)
            elif stripped.startswith("# "):
                docx.add_heading(stripped[2:], level=1)
            elif stripped.startswith("- "):
                docx.add_paragraph(stripped[2:], style="List Bullet")
            else:
                docx.add_paragraph(stripped)
        buffer = BytesIO()
        docx.save(buffer)
        return buffer.getvalue()


# ===== P0-2 / P0-3 / P0-4 / P0-5 / P1-2 / P1-4 导出归一化管道 =====


def _compress_numeric_group(numbers: Sequence[int]) -> str:
    """将数字列表排序去重并压缩为规范引文：连续 >=3 个压缩为 a-b，否则逗号分隔。"""
    unique = sorted({n for n in numbers if n > 0})
    if not unique:
        return ""
    ranges: list[str] = []
    start = unique[0]
    end = unique[0]
    for val in unique[1:]:
        if val == end + 1:
            end = val
        else:
            ranges.append(_format_range(start, end))
            start = end = val
    ranges.append(_format_range(start, end))
    return f"[{', '.join(ranges)}]"


def _format_range(start: int, end: int) -> str:
    if end - start >= 2:
        return f"{start}-{end}"
    if end > start:
        return f"{start}, {end}"
    return str(start)


def normalize_bibliography_entries(
    entries: Sequence[Mapping[str, Any]],
    *,
    doc_titles_by_id: Mapping[str, str] | None = None,
) -> tuple[list[dict[str, Any]], dict[int, int]]:
    """按 `doc_id` 聚合知识库分块为单条文献，剥离 HTML 标签与题名末尾双句号（P0-5 / P1-4）。

    返回 `(清洗归并后的条目列表, 旧序号 -> 新序号 映射)`。
    """
    titles_map = doc_titles_by_id or {}
    merged: list[dict[str, Any]] = []
    key_to_pos: dict[str, int] = {}
    index_remap: dict[int, int] = {}

    for pos, raw_entry in enumerate(entries, start=1):
        entry = dict(raw_entry)
        old_index_raw = entry.get("index", pos)
        old_index = int(old_index_raw) if isinstance(old_index_raw, int) else pos

        doc_id = str(entry.get("doc_id") or "").strip()
        url = str(entry.get("source_url") or entry.get("url") or "").strip()
        doi = str(entry.get("doi") or entry.get("DOI") or "").strip()

        if doc_id and not url and not doi:
            dedup_key = f"doc:{doc_id}"
        elif url:
            dedup_key = f"url:{url}"
        else:
            dedup_key = f"row:{pos}"

        clean_title = ""
        if doc_id and doc_id in titles_map:
            clean_title = clean_csl_title(titles_map[doc_id])
        if not clean_title:
            clean_title = clean_csl_title(entry.get("title") or entry.get("source_id"))

        if isinstance(entry.get("authors"), list):
            entry["authors"] = [strip_html_tags(a) for a in entry["authors"] if strip_html_tags(a)]
        elif isinstance(entry.get("authors"), str):
            entry["authors"] = strip_html_tags(entry["authors"])

        if clean_title:
            entry["title"] = clean_title

        if dedup_key in key_to_pos:
            target_pos = key_to_pos[dedup_key]
            index_remap[old_index] = target_pos + 1
            # 补充缺失元数据
            target = merged[target_pos]
            if doc_id and doc_id in titles_map and clean_title:
                target["title"] = clean_title
            if not target.get("authors") and entry.get("authors"):
                target["authors"] = entry["authors"]
            if target.get("year") is None and entry.get("year") is not None:
                target["year"] = entry["year"]
            continue

        new_idx = len(merged) + 1
        entry["index"] = new_idx
        key_to_pos[dedup_key] = len(merged)
        index_remap[old_index] = new_idx
        merged.append(entry)

    return merged, index_remap


def render_clean_bibliography(
    entries: Sequence[Mapping[str, Any]],
    *,
    citation_style: str | None = None,
    style_resolver: Any | None = None,
) -> tuple[list[str], dict[int, str], dict[str, str], bool]:
    """用 CSL 渲染干净参考文献列表，并产出旧新编号到正文标记的映射。

    返回 `(格式化参考文献行列表, new_index->正文marker, item_id->正文marker, 是否顺序编码制)`。
    """
    from app.tools.csl.models import DEFAULT_STYLE_ID, csl_item_from_bibliography_entry
    from app.tools.csl.renderer import CitationRenderer

    if not entries:
        return [], {}, {}, True

    style_id = (citation_style or "").strip() or DEFAULT_STYLE_ID
    renderer = CitationRenderer(resolver=style_resolver)
    resolved_id = renderer.resolver.resolve(style_id)
    meta = renderer.resolver.meta(resolved_id)
    is_numeric = meta.numbering == "citation-number"

    items = []
    idx_by_item_id: dict[str, list[int]] = {}
    fallback_entries: list[tuple[int, Mapping[str, Any]]] = []

    for pos, entry in enumerate(entries, start=1):
        idx = int(entry.get("index", pos))
        item = csl_item_from_bibliography_entry(entry)
        if item is not None:
            if item.id not in idx_by_item_id:
                items.append(item)
                idx_by_item_id[item.id] = []
            idx_by_item_id[item.id].append(idx)
        else:
            fallback_entries.append((idx, entry))

    lines: list[str] = []
    marker_by_index: dict[int, str] = {}
    marker_by_item_id: dict[str, str] = {}

    if items:
        rendered = renderer.render(items, style_id=resolved_id)
        lines.extend(rendered.entries)
        marker_by_item_id.update(rendered.marker_by_item_id)
        for order_pos, item_id in enumerate(rendered.item_order, start=1):
            marker = rendered.marker_by_item_id.get(item_id, f"[{order_pos}]")
            for idx in idx_by_item_id.get(item_id, ()):
                marker_by_index[idx] = f"[{order_pos}]" if is_numeric else marker

    for fb_idx, entry in fallback_entries:
        next_no = len(lines) + 1
        title = clean_csl_title(entry.get("title") or entry.get("source_id") or f"文献 {fb_idx}")
        lines.append(f"[{next_no}] {title}." if is_numeric else f"{title}.")
        marker_by_index[fb_idx] = f"[{next_no}]" if is_numeric else f"({title})"

    return lines, marker_by_index, marker_by_item_id, is_numeric


def _strip_leading_doc_title_and_managed_sections(text: str) -> str:
    """剥离文档首行 `# 题目`、`## 参考文献`、`## 引用核验` 及内部推理噪声。"""
    lines = text.replace("\r\n", "\n").splitlines()
    # 剥离首个顶级 `# 题目`（模板封面已单独渲染论文题目）
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and lines[0].lstrip().startswith("# ") and not lines[0].lstrip().startswith("## "):
        lines.pop(0)

    cleaned_blocks: list[str] = []
    raw_body = "\n".join(lines)
    # 识别是否已经包含 write 产出的综合章节，若包含则剔除历史残存的证据提取草稿章节（P0-4）
    has_extract_heading = bool(re.search(r"^#{1,3}\s+.*证据提取\s*$", raw_body, re.MULTILINE))
    has_non_extract_body = any(
        line.lstrip().startswith(("## ", "### "))
        and "摘要" not in line
        and "局限" not in line
        and "参考文献" not in line
        and "引用核验" not in line
        and "证据提取" not in line
        and "证据局限性" not in line
        for line in lines
    )

    skipping = False
    filtered_lines: list[str] = []
    for line in lines:
        cleaned_line = _MODEL_INFER_RE.sub("", line)
        heading_match = re.match(r"^(#{1,3})\s+(.+?)\s*$", cleaned_line.strip())
        if heading_match:
            level = len(heading_match.group(1))
            heading_text = heading_match.group(2).strip()
            if heading_text in {"参考文献", "引用核验"} or heading_text.startswith(
                ("参考文献：", "参考文献:", "引用核验：", "引用核验:")
            ):
                skipping = True
                continue
            if (
                has_extract_heading
                and has_non_extract_body
                and (heading_text.endswith("证据提取") or "证据局限性说明" in heading_text)
            ):
                skipping = True
                continue
            if level <= 2:
                skipping = False
            elif skipping:
                continue
        elif skipping:
            continue
        filtered_lines.append(cleaned_line)

    seen_fingerprints: set[str] = set()
    for block in "\n".join(filtered_lines).split("\n\n"):
        stripped = block.strip()
        if not stripped:
            continue

        # 剥离段落内部多余的 HTML 标签（如 <p>/<sup>）
        stripped = _HTML_SUP_BLOCK_RE.sub("", stripped)
        stripped = _RESIDUAL_HTML_TAG_RE.sub("", stripped).strip()
        if not stripped:
            continue

        # 忽略引用标记做跨节点重复段落去重（P0-4）
        fp_src = re.sub(r"\[E\d+\]|\[\d+(?:[,\-–]\d+)*\]", "", stripped)
        fingerprint = re.sub(r"\s+", " ", fp_src).strip()
        if fingerprint and not stripped.startswith("#"):
            if fingerprint in seen_fingerprints:
                continue
            seen_fingerprints.add(fingerprint)
        cleaned_blocks.append(stripped)

    return "\n\n".join(cleaned_blocks)


def _strip_citations_from_abstract(body: str) -> str:
    """清理 `## 摘要` 段落正文中的所有引文角标（P0-3）。"""
    lines = body.splitlines()
    in_abstract = False
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if re.match(r"^#{1,6}\s+", stripped):
            in_abstract = bool(re.match(r"^#{1,6}\s+摘要\b", stripped))
            out.append(line)
            continue
        if in_abstract:
            cleaned = _ABSTRACT_CITE_RE.sub("", line)
            cleaned = re.sub(r"\s+([，。；、,.])", r"\1", cleaned)
            out.append(cleaned)
        else:
            out.append(line)
    return "\n".join(out)


def normalize_export_citations(
    body: str,
    *,
    index_remap: Mapping[int, int] | None = None,
    marker_map: Mapping[int, str] | None = None,
    item_id_map: Mapping[str, str] | None = None,
    is_numeric: bool = True,
) -> str:
    """替换 `[[cite:...]]` 与 `[E#]` 为规范学术引文，合并相邻编号并清理摘要角标（P0-3）。"""
    remap = index_remap or {}
    markers = marker_map or {}
    text = _strip_citations_from_abstract(body)

    if item_id_map:
        text = re.sub(
            r"\[\[cite:([^\s\]]+)\]\]",
            lambda m: item_id_map.get(m.group(1), m.group(0)),
            text,
        )

    def _replace_e_group(match: re.Match[str]) -> str:
        raw_nums = [int(n) for n in _SINGLE_E_RE.findall(match.group(0))]
        if not raw_nums:
            return match.group(0)
        if is_numeric:
            mapped_nums: list[int] = []
            for raw_n in raw_nums:
                merged_n = remap.get(raw_n, raw_n)
                target_marker = markers.get(merged_n)
                if target_marker:
                    m_num = re.search(r"\d+", target_marker)
                    mapped_nums.append(int(m_num.group(0)) if m_num else merged_n)
                else:
                    mapped_nums.append(merged_n)
            return _compress_numeric_group(mapped_nums)
        # 著者-出版年制合并多个作者标记
        labels: list[str] = []
        seen_labels: set[str] = set()
        for raw_n in raw_nums:
            merged_n = remap.get(raw_n, raw_n)
            label = markers.get(merged_n, f"[{merged_n}]").strip()
            if label.startswith("(") and label.endswith(")"):
                label = label[1:-1]
            if label not in seen_labels:
                seen_labels.add(label)
                labels.append(label)
        return f"({'; '.join(labels)})"

    return _INLINE_E_GROUP_RE.sub(_replace_e_group, text)


def number_markdown_headings(body: str) -> str:
    """为正文二级及以下标题自动补全 `1.` / `1.1` / `1.1.1` 标准序号（保留摘要，P1-2）。"""
    h1 = 0
    h2 = 0
    h3 = 0
    out: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        m = re.match(r"^(#{2,4})\s+(.+?)\s*$", stripped)
        if not m:
            out.append(line)
            continue
        hashes, title = m.group(1), m.group(2).strip()
        if title in {"摘要", "参考文献", "引用核验"} or _EXISTING_HEADING_PREFIX_RE.match(title):
            out.append(line)
            continue
        level = len(hashes)
        if level == 2:
            h1 += 1
            h2 = 0
            h3 = 0
            out.append(f"## {h1}. {title}")
        elif level == 3:
            if h1 == 0:
                h1 = 1
            h2 += 1
            h3 = 0
            out.append(f"### {h1}.{h2} {title}")
        else:
            if h1 == 0:
                h1 = 1
            if h2 == 0:
                h2 = 1
            h3 += 1
            out.append(f"#### {h1}.{h2}.{h3} {title}")
    return "\n".join(out)


_EMPTY_LIMITATION_VALUES = frozenset({"", "无", "（无）", "(无)", "暂无", "none", "null", "n/a"})
_NUMERIC_CITATION_RE = re.compile(r"\[\d{1,3}\]")


def _is_empty_limitation_item(item: Any) -> bool:
    """判断「局限性」条目是否为 (无) 之类的空占位。"""
    text = strip_html_tags(str(item or "")).strip()
    text = re.sub(r"^[-*•\d.、)\s]+", "", text).strip()
    cleaned = re.sub(r"[。；;，,.\s]+$", "", text).strip()
    return cleaned.casefold() in _EMPTY_LIMITATION_VALUES


def _strip_empty_limitations(body: str) -> str:
    """Fix 6：整节局限性均为空占位时删掉该节，避免成稿出现「局限性 •（无）」。"""
    lines = body.splitlines()
    out: list[str] = []
    i = 0
    total = len(lines)
    while i < total:
        if re.match(r"^#{1,6}\s*(?:\d+(?:\.\d+)*[.、．]?\s*)?局限性\s*$", lines[i].strip()):
            j = i + 1
            section: list[str] = []
            while j < total and not re.match(r"^#{1,6}\s", lines[j].strip()):
                section.append(lines[j])
                j += 1
            kept = [
                line for line in section if line.strip() and not _is_empty_limitation_item(line)
            ]
            if kept:
                out.append(lines[i])
                out.extend(kept)
            i = j
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def _dedupe_adjacent_paragraphs(body: str) -> str:
    """Fix 2/3：删除相邻的完全重复段落（摘要与正文首段逐字重复的兜底）。"""
    paragraphs = [
        paragraph.strip() for paragraph in re.split(r"\n\s*\n", body) if paragraph.strip()
    ]
    kept: list[str] = []
    for paragraph in paragraphs:
        if kept and re.sub(r"\s+", "", paragraph) == re.sub(r"\s+", "", kept[-1]):
            continue
        kept.append(paragraph)
    return "\n\n".join(kept)


def _cleanup_export_body(body: str) -> str:
    return _dedupe_adjacent_paragraphs(_strip_empty_limitations(body))


def _has_numeric_citations(document: str) -> bool:
    return bool(_NUMERIC_CITATION_RE.search(document))


def _has_bibliography_entries(document: str) -> bool:
    match = re.search(r"^#{1,6}\s*参考文献\s*$", document, re.MULTILINE)
    if match is None:
        return False
    rest = document[match.end() :]
    following = re.search(r"^#{1,6}\s", rest, re.MULTILINE)
    section = rest[: following.start()] if following else rest
    return bool(_NUMERIC_CITATION_RE.search(section))


def prepare_template_export(
    body: str,
    bibliography_entries: Sequence[Mapping[str, Any]] | None,
    *,
    fallback_bibliography: Sequence[str] = (),
    citation_style: str | None = None,
    doc_titles_by_id: Mapping[str, str] | None = None,
    style_resolver: Any | None = None,
    add_heading_numbers: bool = True,
) -> tuple[str, list[str], list[dict[str, Any]]]:
    """统一准备模板导出所需正文、规范 CSL 参考文献与去重后的条目元数据。"""
    cleaned_entries: list[dict[str, Any]] = []
    index_remap: dict[int, int] = {}
    marker_map: dict[int, str] = {}
    item_id_map: dict[str, str] = {}
    is_numeric = True
    rendered_bib: list[str] = []

    if bibliography_entries:
        cleaned_entries, index_remap = normalize_bibliography_entries(
            bibliography_entries, doc_titles_by_id=doc_titles_by_id
        )
        rendered_bib, marker_map, item_id_map, is_numeric = render_clean_bibliography(
            cleaned_entries, citation_style=citation_style, style_resolver=style_resolver
        )
    else:
        rendered_bib = [
            re.sub(r"[.。]+$", ".", strip_html_tags(line))
            for line in fallback_bibliography
            if strip_html_tags(line)
        ]

    stripped_body = _strip_leading_doc_title_and_managed_sections(body)
    normalized_body = normalize_export_citations(
        stripped_body,
        index_remap=index_remap,
        marker_map=marker_map,
        item_id_map=item_id_map,
        is_numeric=is_numeric,
    )
    normalized_body = _cleanup_export_body(normalized_body)
    if add_heading_numbers:
        normalized_body = number_markdown_headings(normalized_body)

    return normalized_body, rendered_bib, cleaned_entries


def _clean_docx_export_document(document: str, *, report: ReviewReport) -> str:
    """用于 `LiteratureExporter.export_docx` 的最终出口清洗：零 `[E#]` / `模型推断：` / 重复章 / HTML。"""
    cleaned_entries, index_remap = normalize_bibliography_entries(report.bibliography)
    marker_map = {
        int(e.get("index", i)): f"[{int(e.get('index', i))}]"
        for i, e in enumerate(cleaned_entries, start=1)
    }
    text = _MODEL_INFER_RE.sub("", document)
    text = _HTML_SUP_BLOCK_RE.sub("", text)
    text = _RESIDUAL_HTML_TAG_RE.sub("", text)
    # 摘要去角标 & [E#] -> [n]
    text = normalize_export_citations(
        text,
        index_remap=index_remap,
        marker_map=marker_map,
        is_numeric=True,
    )
    # 若上游遗留多份 ## 参考文献，仅保留最后一个权威参考文献段
    matches = list(re.finditer(r"^## 参考文献[ \t]*$", text, re.MULTILINE))
    if len(matches) > 1:
        for earlier in reversed(matches[:-1]):
            start = earlier.start()
            rest = text[earlier.end() :]
            following = re.search(r"^## ", rest, re.MULTILINE)
            end = earlier.end() + following.start() if following else len(text)
            text = text[:start].rstrip() + (
                "\n\n" + text[end:].lstrip() if text[end:].strip() else ""
            )
    return text
