"""文献导出适配器：ReviewReport → Markdown / DOCX 产物（Plan 3 Task 5）。

安全约定：
- artifact_id 由服务端 `uuid4().hex` 生成，客户端只用于查找，不能指定路径；
- 落盘路径固定为 `<artifact_root>/<user_id>/<task_id>/<artifact_id><suffix>`；
- Markdown 中对原始 HTML 做转义，并拒绝 `javascript:` / `data:` / `file:` 等非 HTTP(S) 链接；
- `resolve_for_user()` 只接受严格的 32 位十六进制 ID，跨用户/跨任务/路径穿越一律 404。
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

from docx import Document as DocxDocument

from app.core.exceptions import NotFoundException, ValidationException
from app.orchestration.contracts import ReviewReport
from app.tools.literature.models import Artifact

_ARTIFACT_ID = re.compile(r"^[0-9a-f]{32}$")
_UNSAFE_SCHEME = re.compile(r"(?i)\b(?:javascript|data|file|vbscript)\s*:")
_HTML_ESCAPES = (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"))


class LiteratureExporter:
    """把 `ReviewReport` 落地为受控目录下的 Markdown / DOCX 产物。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    # ===== 导出 =====

    def export_markdown(self, user_id: UUID, task_id: UUID, report: ReviewReport) -> Artifact:
        """写出生成的 Markdown（HTML 已转义、链接已校验）。"""
        raw = self._compose_document(report)
        payload = self._escape_html(raw).encode("utf-8")
        return self._write(user_id, task_id, kind="markdown", suffix=".md", payload=payload)

    def export_docx(self, user_id: UUID, task_id: UUID, report: ReviewReport) -> Artifact:
        """按同一标题层级写出 DOCX（python-docx）。"""
        raw = self._compose_document(report)
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

    def _compose_document(self, report: ReviewReport) -> str:
        """组装完整文档：标题 → 摘要 → 正文 → 局限性 → 参考文献。"""
        body = (report.final_markdown or "").strip()
        stats = report.stats or {}
        title = str(stats.get("title") or self._first_heading(body) or "文献综述")
        abstract = str(stats.get("abstract") or self._first_paragraph(body))
        limitations = stats.get("limitations") or report.issues or []

        parts = [f"# {title}"]
        if abstract and "## 摘要" not in body:
            parts.append(f"## 摘要\n{abstract}")
        if body:
            parts.append(body)
        if limitations and "## 局限性" not in body:
            items = limitations if isinstance(limitations, list) else [limitations]
            parts.append("## 局限性\n" + "\n".join(f"- {item}" for item in items))
        if report.bibliography and "## 参考文献" not in body:
            lines = [
                self._bibliography_line(index, entry)
                for index, entry in enumerate(report.bibliography, start=1)
            ]
            parts.append("## 参考文献\n" + "\n".join(lines))

        document = "\n\n".join(parts)
        self._validate_links(document)
        return document

    @staticmethod
    def _bibliography_line(index: int, entry: dict) -> str:
        title = str(entry.get("title") or entry.get("source_id") or "未命名文献")
        url = str(entry.get("source_url") or "")
        source_id = str(entry.get("source_id") or "")
        suffix = f" — {url}" if url else ""
        prefix = f"[{index}] {title}"
        if source_id and source_id not in title:
            prefix = f"{prefix} ({source_id})"
        return f"- {prefix}{suffix}"

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
