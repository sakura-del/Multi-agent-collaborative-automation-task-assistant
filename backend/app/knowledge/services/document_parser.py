"""文档解析器：多格式文档解析为纯文本 + 结构化元数据。

支持 PDF / DOCX / Markdown / TXT 四种格式。
每种格式有独立解析器，统一通过 DocumentParser 调度。

设计要点：
- 策略模式：按文件扩展名分发到对应解析器，新增格式只需注册新解析器
- 结构保留：解析时提取标题层级与段落边界，供语义分块使用
- 容错：单段解析失败不中断整体，标记为 UNKNOWN 类型
- 延迟导入：PDF/DOCX 等重依赖在首次使用时才导入（内存优化）
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.core.logging import get_logger
from app.knowledge.models.document import DocumentType, ParsedDocument

logger = get_logger(__name__)


def _detect_type(source: str) -> DocumentType:
    """根据文件扩展名推断文档类型。"""
    ext = Path(source).suffix.lower()
    return {
        ".pdf": DocumentType.PDF,
        ".docx": DocumentType.DOCX,
        ".md": DocumentType.MARKDOWN,
        ".markdown": DocumentType.MARKDOWN,
        ".txt": DocumentType.TXT,
    }.get(ext, DocumentType.UNKNOWN)


def _parse_txt(text: str) -> tuple[str, list[dict[str, Any]]]:
    """解析纯文本：按空行切分段落。"""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    sections = [{"level": 0, "title": "", "content": p} for p in paragraphs]
    return text, sections


def _parse_markdown(text: str) -> tuple[str, list[dict[str, Any]]]:
    """解析 Markdown：按标题行切分章节，保留标题层级。"""
    lines = text.split("\n")
    sections: list[dict[str, Any]] = []
    current_title = ""
    current_level = 0
    current_lines: list[str] = []

    for line in lines:
        stripped = line.lstrip()
        # 识别 ATX 风格标题（# ~ ######）
        if stripped.startswith("#"):
            # 保存前一段
            if current_lines or current_title:
                sections.append(
                    {
                        "level": current_level,
                        "title": current_title,
                        "content": "\n".join(current_lines).strip(),
                    }
                )
            level = len(stripped) - len(stripped.lstrip("#"))
            current_level = level
            current_title = stripped.lstrip("# ").strip()
            current_lines = []
        else:
            current_lines.append(line)

    # 收尾
    if current_lines or current_title:
        sections.append(
            {
                "level": current_level,
                "title": current_title,
                "content": "\n".join(current_lines).strip(),
            }
        )

    return text, sections


def _parse_pdf(file_path: str) -> tuple[str, list[dict[str, Any]]]:
    """解析 PDF：使用 PyMuPDF 提取文本，按页切分。"""
    import fitz  # PyMuPDF

    doc = fitz.open(file_path)
    full_text_parts: list[str] = []
    sections: list[dict[str, Any]] = []

    for page_num in range(len(doc)):
        page = doc[page_num]
        page_text = page.get_text("text").strip()
        if page_text:
            full_text_parts.append(page_text)
            sections.append(
                {
                    "level": 1,
                    "title": f"第 {page_num + 1} 页",
                    "content": page_text,
                    "page": page_num + 1,
                }
            )

    doc.close()
    return "\n\n".join(full_text_parts), sections


def _parse_docx(file_path: str) -> tuple[str, list[dict[str, Any]]]:
    """解析 DOCX：使用 python-docx 提取段落与标题样式。"""
    from docx import Document

    doc = Document(file_path)
    full_text_parts: list[str] = []
    sections: list[dict[str, Any]] = []
    current_title = ""
    current_level = 0
    current_lines: list[str] = []

    def _flush() -> None:
        nonlocal current_title, current_level, current_lines
        if current_lines or current_title:
            full_text_parts.append("\n".join(current_lines))
            sections.append(
                {
                    "level": current_level,
                    "title": current_title,
                    "content": "\n".join(current_lines).strip(),
                }
            )
        current_title = ""
        current_level = 0
        current_lines = []

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        style = (para.style.name or "").lower()
        if style.startswith("heading"):
            _flush()
            # 提取 heading 级别
            try:
                current_level = int(style.replace("heading", "").strip())
            except ValueError:
                current_level = 1
            current_title = text
        else:
            current_lines.append(text)
            full_text_parts.append(text)

    _flush()
    return "\n".join(full_text_parts), sections


class DocumentParser:
    """多格式文档解析器：按类型分发到对应解析逻辑。

    用法：
        parser = DocumentParser()
        doc = parser.parse("report.pdf")
        print(doc.text, doc.sections)
    """

    def parse(self, source: str, doc_id: str | None = None) -> ParsedDocument:
        """解析文件路径或纯文本内容。

        若 source 为已存在的文件路径则按文件解析；
        否则视为纯文本内容，按 TXT 处理。
        """
        doc_id = doc_id or _generate_doc_id(source)

        if os.path.isfile(source):
            doc_type = _detect_type(source)
            text, sections = self._parse_file(source, doc_type)
            metadata = {"file_name": Path(source).name, "file_size": os.path.getsize(source)}
        else:
            # source 本身是文本内容
            doc_type = DocumentType.TXT
            text, sections = _parse_txt(source)
            metadata = {"source_type": "raw_text"}

        logger.info(
            "document_parsed",
            doc_id=doc_id,
            doc_type=doc_type.value,
            text_length=len(text),
            sections=len(sections),
        )
        return ParsedDocument(
            doc_id=doc_id,
            source=source,
            doc_type=doc_type,
            text=text,
            metadata=metadata,
            sections=sections,
        )

    def parse_text(self, text: str, doc_id: str | None = None) -> ParsedDocument:
        """直接解析文本内容（便捷方法）。"""
        doc_id = doc_id or _generate_doc_id(text)
        _, sections = _parse_txt(text)
        return ParsedDocument(
            doc_id=doc_id,
            source="<raw_text>",
            doc_type=DocumentType.TXT,
            text=text,
            metadata={"source_type": "raw_text"},
            sections=sections,
        )

    def _parse_file(
        self, file_path: str, doc_type: DocumentType
    ) -> tuple[str, list[dict[str, Any]]]:
        """按类型分发到具体解析器。"""
        try:
            if doc_type is DocumentType.PDF:
                return _parse_pdf(file_path)
            if doc_type is DocumentType.DOCX:
                return _parse_docx(file_path)
            if doc_type is DocumentType.MARKDOWN:
                text = Path(file_path).read_text(encoding="utf-8")
                return _parse_markdown(text)
            if doc_type is DocumentType.TXT:
                text = Path(file_path).read_text(encoding="utf-8")
                return _parse_txt(text)
            # 未知类型：尝试按纯文本读取
            text = Path(file_path).read_text(encoding="utf-8", errors="ignore")
            return _parse_txt(text)
        except Exception as exc:
            logger.error("document_parse_failed", file_path=file_path, error=str(exc))
            return "", []


def _generate_doc_id(source: str) -> str:
    """基于源标识生成文档 ID。"""
    import hashlib

    return hashlib.md5(source.encode("utf-8")).hexdigest()[:16]
