"""学术全文清洁解析（Issue #73 Phase A）。

四项核心处理：双栏坐标重排 + 页眉页脚裁切；扫描件密度检测；末尾 References 截断；
章节角色确定性映射。输出与 ``fulltext_cache.parsed_sections`` 对齐。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from html.parser import HTMLParser
from typing import Any, Literal
from xml.etree import ElementTree

SectionRole = Literal["background", "method", "result", "limitation", "other"]

#: ``page.get_text("blocks")`` 的文本块：(x0, y0, x1, y1, text)。
_Block = tuple[float, float, float, float, str]

_WHITESPACE_RE = re.compile(r"\s+")

#: 章节编号前缀（``1 `` / ``2.3 `` / ``III. ``），用于判定独立标题行。
_SECTION_NUMBER_RE = re.compile(r"^(?:\d{1,3}(?:\.\d{1,3})*\.?|[IVXLC]{1,6}[.)]?)\s+")

#: 末尾参考文献 / 致谢标题（必须是独立整行）。
_REFERENCES_RE = re.compile(
    r"^(references|bibliography|参考文献|acknowledg(e)?ments|致谢)$",
    re.IGNORECASE,
)


class ParseStatus(StrEnum):
    """解析状态；非 ``OK`` 时调用方必须降级为 metadata 证据。"""

    OK = "ok"
    SCANNED_NO_TEXT_LAYER = "scanned_no_text_layer"
    EMPTY = "empty"
    FAILED = "failed"


@dataclass(slots=True)
class ParsedSection:
    """与 ``fulltext_cache.parsed_sections`` 对齐的单个章节。"""

    title: str
    role: SectionRole
    page_start: int
    page_end: int
    text: str


@dataclass(slots=True)
class ParseResult:
    """一次解析的结果；扫描件 / 失败时 ``sections`` 为空。"""

    status: ParseStatus
    sections: list[ParsedSection] = field(default_factory=list)
    page_count: int = 0


_ROLE_KEYWORDS: list[tuple[tuple[str, ...], SectionRole]] = [
    (("introduction", "background", "related work", "引言", "背景"), "background"),
    (("method", "approach", "architecture", "方法", "模型", "设计"), "method"),
    (("experiment", "result", "evaluation", "实验", "结果"), "result"),
    (("limitation", "discussion", "future", "局限", "讨论", "展望"), "limitation"),
]


class _HtmlBlockCollector(HTMLParser):
    """把 HTML 正文按块收集为 ``(是否标题, 文本)``，忽略脚本 / 样式内容。"""

    _HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
    _TEXT_TAGS = frozenset({"p", "li", "table", "blockquote", "pre", "dd"})
    _SKIP_TAGS = frozenset({"script", "style", "noscript"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[bool, str]] = []
        self._active_tag: str | None = None
        self._is_heading = False
        self._buffer: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth or self._active_tag is not None:
            return
        if tag in self._HEADING_TAGS:
            self._active_tag = tag
            self._is_heading = True
            self._buffer = []
        elif tag in self._TEXT_TAGS:
            self._active_tag = tag
            self._is_heading = False
            self._buffer = []

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth or tag != self._active_tag:
            return
        text = _WHITESPACE_RE.sub(" ", "".join(self._buffer)).strip()
        if text:
            self.blocks.append((self._is_heading, text))
        self._active_tag = None
        self._is_heading = False
        self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._active_tag is not None and not self._skip_depth:
            self._buffer.append(data)


class CleanFullTextParser:
    """PDF / HTML / JATS 清洁解析器（PDF 用 PyMuPDF，HTML / JATS 用标准库）。"""

    HEADER_RATIO = 0.08
    FOOTER_RATIO = 0.92
    MIN_CHARS_PER_PAGE = 50
    SCANNED_PAGE_RATIO = 0.70

    #: 双栏判定：左半区（x1 < 52% 页宽）与右半区（x0 > 48% 页宽）的块数占比下限。
    LEFT_COLUMN_END_RATIO = 0.52
    RIGHT_COLUMN_START_RATIO = 0.48
    SIDE_BLOCK_RATIO = 0.25

    #: 标题行长度上限；无标题段落取首行前 120 字符作为标题。
    MAX_HEADING_CHARS = 80
    KEYWORD_HEADING_MAX_CHARS = 40
    MAX_TITLE_CHARS = 120

    def role_for_title(self, title: str) -> SectionRole:
        """按标题关键词做确定性角色映射（未命中 → ``other``）。"""
        normalized = title.strip().lower()
        for keywords, role in _ROLE_KEYWORDS:
            if any(keyword in normalized for keyword in keywords):
                return role
        return "other"

    def parse_pdf(self, content: bytes) -> ParseResult:
        """解析 PDF 字节流：裁页眉页脚 → 双栏重排 → 扫描检测 → 截 References → 分节。"""
        import fitz  # PyMuPDF：延迟导入，HTML / JATS 路径不承担加载成本

        try:
            document = fitz.open(stream=content, filetype="pdf")
        except Exception:
            return ParseResult(status=ParseStatus.FAILED)

        try:
            page_count = int(document.page_count)
            if page_count <= 0:
                return ParseResult(status=ParseStatus.EMPTY)

            lines: list[tuple[str, int]] = []
            low_text_pages = 0
            for page_index in range(page_count):
                page = document.load_page(page_index)
                blocks = self._filter_blocks(page)
                if self._char_count(blocks) < self.MIN_CHARS_PER_PAGE:
                    low_text_pages += 1
                page_lines = self._ordered_lines(blocks, float(page.rect.width))
                lines.extend((text, page_index + 1) for text in page_lines)

            if low_text_pages / page_count >= self.SCANNED_PAGE_RATIO:
                return ParseResult(
                    status=ParseStatus.SCANNED_NO_TEXT_LAYER,
                    page_count=page_count,
                )

            lines = self._truncate_references(lines)
            entries = [(text, self._is_heading(text), page) for text, page in lines]
            sections = self._assemble(entries)
            if not sections:
                return ParseResult(status=ParseStatus.EMPTY, page_count=page_count)
            return ParseResult(
                status=ParseStatus.OK,
                sections=sections,
                page_count=page_count,
            )
        finally:
            document.close()

    def parse_html(self, content: bytes) -> ParseResult:
        """解析 arXiv HTML / PMC HTML：``h1-h6`` 切节，正文段落按角色归入。"""
        try:
            collector = _HtmlBlockCollector()
            collector.feed(content.decode("utf-8", errors="replace"))
            collector.close()
        except Exception:
            return ParseResult(status=ParseStatus.FAILED)

        entries = [(text, is_heading, 1) for is_heading, text in collector.blocks]
        sections = self._assemble(entries)
        if not sections:
            return ParseResult(status=ParseStatus.EMPTY)
        return ParseResult(status=ParseStatus.OK, sections=sections, page_count=1)

    def parse_jats(self, content: bytes) -> ParseResult:
        """解析 JATS XML：按 ``sec -> title + p`` 分节，异常返回 ``FAILED``。"""
        try:
            root = ElementTree.fromstring(content)
        except Exception:
            return ParseResult(status=ParseStatus.FAILED)

        entries: list[tuple[str, bool, int]] = []
        for section_element in root.iter("sec"):
            title = self._element_text(section_element.find("title"))
            if title:
                entries.append((title, True, 1))
            for paragraph in section_element.findall("p"):
                text = self._element_text(paragraph)
                if text:
                    entries.append((text, False, 1))

        if not entries:
            for paragraph in root.iter("p"):
                text = self._element_text(paragraph)
                if text:
                    entries.append((text, False, 1))

        sections = self._assemble(entries)
        if not sections:
            return ParseResult(status=ParseStatus.EMPTY)
        return ParseResult(status=ParseStatus.OK, sections=sections, page_count=1)

    # ------------------------------------------------------------------ PDF 内部

    def _filter_blocks(self, page: Any) -> list[_Block]:
        """取出文本块并裁掉页眉（y1 < 8% 页高）/ 页脚（y0 > 92% 页高）。"""
        page_rect = page.rect
        page_height = float(page_rect.height)
        header_limit = page_height * self.HEADER_RATIO
        footer_limit = page_height * self.FOOTER_RATIO

        blocks: list[_Block] = []
        for raw_block in page.get_text("blocks"):
            x0 = float(raw_block[0])
            y0 = float(raw_block[1])
            x1 = float(raw_block[2])
            y1 = float(raw_block[3])
            text = str(raw_block[4]).strip()
            block_type = int(raw_block[6]) if len(raw_block) > 6 else 0
            if block_type != 0 or not text:
                continue
            if y1 < header_limit or y0 > footer_limit:
                continue
            blocks.append((x0, y0, x1, y1, text))
        return blocks

    @staticmethod
    def _char_count(blocks: list[_Block]) -> int:
        """有效字符数（去掉全部空白），用于扫描件密度检测。"""
        return sum(len(_WHITESPACE_RE.sub("", block[4])) for block in blocks)

    @staticmethod
    def _bbox_key(block: _Block) -> tuple[float, float]:
        return (block[1], block[0])

    def _split_columns(
        self, blocks: list[_Block], page_width: float
    ) -> tuple[list[_Block], list[_Block], list[_Block]]:
        left_end = page_width * self.LEFT_COLUMN_END_RATIO
        right_start = page_width * self.RIGHT_COLUMN_START_RATIO
        left: list[_Block] = []
        right: list[_Block] = []
        spanning: list[_Block] = []
        for block in blocks:
            if block[2] < left_end:
                left.append(block)
            elif block[0] > right_start:
                right.append(block)
            else:
                spanning.append(block)
        return left, right, spanning

    def _ordered_lines(self, blocks: list[_Block], page_width: float) -> list[str]:
        """双栏页先左栏后右栏（跨栏块按 y 排在最前），单栏页按 (y0, x0) 排序。"""
        left, right, spanning = self._split_columns(blocks, page_width)
        if self._has_two_columns(blocks, left, right):
            ordered = [
                *sorted(spanning, key=self._bbox_key),
                *sorted(left, key=self._bbox_key),
                *sorted(right, key=self._bbox_key),
            ]
        else:
            ordered = sorted(blocks, key=self._bbox_key)

        lines: list[str] = []
        for block in ordered:
            lines.extend(line.strip() for line in block[4].splitlines() if line.strip())
        return lines

    def _has_two_columns(
        self, blocks: list[_Block], left: list[_Block], right: list[_Block]
    ) -> bool:
        if not blocks:
            return False
        total = len(blocks)
        return (
            len(left) / total >= self.SIDE_BLOCK_RATIO
            and len(right) / total >= self.SIDE_BLOCK_RATIO
        )

    @staticmethod
    def _truncate_references(lines: list[tuple[str, int]]) -> list[tuple[str, int]]:
        """仅在文本后半段截断独立的 References / Acknowledgements 标题行。"""
        if not lines:
            return lines
        total_chars = sum(len(text) + 1 for text, _page in lines)
        half = total_chars / 2
        offset = 0
        for index, (text, _page) in enumerate(lines):
            if offset >= half and _REFERENCES_RE.match(text.strip()):
                return lines[:index]
            offset += len(text) + 1
        return lines

    def _is_heading(self, text: str) -> bool:
        """独立行且「数字编号开头」或「角色关键词开头」才视为章节标题。"""
        stripped = text.strip()
        if not stripped or len(stripped) > self.MAX_HEADING_CHARS:
            return False
        if stripped.endswith((".", "。", ",", "，", ";", "；")):
            return False

        numbered = _SECTION_NUMBER_RE.match(stripped)
        if numbered is not None:
            remainder = stripped[numbered.end() :].strip()
            return bool(remainder) and (remainder[0].isupper() or not remainder[0].isascii())

        if len(stripped) > self.KEYWORD_HEADING_MAX_CHARS:
            return False
        if any(mark in stripped for mark in "，。；"):
            return False
        normalized = stripped.rstrip(":：").strip().lower()
        return any(
            normalized.startswith(keyword)
            for keywords, _role in _ROLE_KEYWORDS
            for keyword in keywords
        )

    def _assemble(self, entries: list[tuple[str, bool, int]]) -> list[ParsedSection]:
        """按标题切节；标题前（或全程无标题）的文本归入 ``other`` 角色。"""
        sections: list[ParsedSection] = []
        buffer: list[tuple[str, int]] = []
        title: str | None = None
        role: SectionRole = "other"

        for text, is_heading, page in entries:
            if is_heading:
                self._append_section(sections, title, role, buffer)
                buffer = []
                title = text[: self.MAX_TITLE_CHARS]
                role = self.role_for_title(text)
            buffer.append((text, page))

        self._append_section(sections, title, role, buffer)
        return sections

    def _append_section(
        self,
        sections: list[ParsedSection],
        title: str | None,
        role: SectionRole,
        buffer: list[tuple[str, int]],
    ) -> None:
        if not buffer:
            return
        section_title = title if title is not None else buffer[0][0][: self.MAX_TITLE_CHARS]
        sections.append(
            ParsedSection(
                title=section_title,
                role=role,
                page_start=buffer[0][1],
                page_end=buffer[-1][1],
                text="\n".join(line for line, _page in buffer),
            )
        )

    @staticmethod
    def _element_text(element: ElementTree.Element | None) -> str:
        if element is None:
            return ""
        return _WHITESPACE_RE.sub(" ", "".join(element.itertext())).strip()
