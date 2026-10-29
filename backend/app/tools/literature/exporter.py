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
from collections.abc import Mapping
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from docx import Document as DocxDocument

from app.core.exceptions import NotFoundException, ValidationException
from app.orchestration.contracts import ReviewReport
from app.tools.literature.models import Artifact

_ARTIFACT_ID = re.compile(r"^[0-9a-f]{32}$")
_UNSAFE_SCHEME = re.compile(r"(?i)\b(?:javascript|data|file|vbscript)\s*:")
_HTML_ESCAPES = (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"))

#: 判定态 → 中文标签（与复盘 Agent / 校验报告的展示口径保持一致）
_VERDICT_LABELS: tuple[tuple[str, str], ...] = (
    ("verified", "已验证"),
    ("metadata_mismatch", "元数据不符"),
    ("not_found", "未找到"),
    ("unverified", "未核验"),
)

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
    label = dict(_VERDICT_LABELS).get(status, status)
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
        """按同一标题层级写出 DOCX（python-docx）；`citation_style` 语义同 Markdown。"""
        raw = self._compose_document(report, citation_style=citation_style)
        payload = self._build_docx_bytes(raw)
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
        abstract = str(stats.get("abstract") or self._first_paragraph(body))
        limitations = stats.get("limitations") or report.issues or []

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
            items = limitations if isinstance(limitations, list) else [limitations]
            parts.append("## 局限性\n" + "\n".join(f"- {item}" for item in items))
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
        for entry in report.bibliography:
            item = csl_item_from_bibliography_entry(entry)
            if item is not None:
                items.append(item)
        renderer = CitationRenderer(resolver=self._style_resolver)
        rendered = renderer.render(items, style_id=citation_style)
        return list(rendered.entries)

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
