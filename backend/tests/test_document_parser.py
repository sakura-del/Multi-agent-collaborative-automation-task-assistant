"""DocumentParser 测试：多格式文档解析为 ParsedDocument。

覆盖纯文本解析、Markdown 章节切分、文件解析、doc_id 确定性等核心行为。
"""

from __future__ import annotations

from pathlib import Path

from app.knowledge.models.document import DocumentType, ParsedDocument
from app.knowledge.services.document_parser import (
    DocumentParser,
    _generate_doc_id,
    _parse_markdown,
    _parse_txt,
)


class TestDocumentType:
    """DocumentType 枚举值校验。"""

    def test_enum_values(self) -> None:
        """各文档类型枚举应有正确的字符串值。"""
        assert DocumentType.PDF == "pdf"
        assert DocumentType.DOCX == "docx"
        assert DocumentType.MARKDOWN == "markdown"
        assert DocumentType.TXT == "txt"
        assert DocumentType.UNKNOWN == "unknown"

    def test_enum_is_str(self) -> None:
        """StrEnum 成员应可直接作为字符串使用。"""
        assert isinstance(DocumentType.TXT, str)
        assert str(DocumentType.MARKDOWN) == "markdown"


class TestParseText:
    """parse_text()：纯文本解析便捷方法。"""

    def test_returns_parsed_document_with_txt_type(self) -> None:
        """parse_text 应返回 ParsedDocument，doc_type 为 TXT，text 原样保留。"""
        parser = DocumentParser()
        doc = parser.parse_text("hello world")

        assert isinstance(doc, ParsedDocument)
        assert doc.doc_type is DocumentType.TXT
        assert doc.text == "hello world"

    def test_sections_split_by_blank_line(self) -> None:
        """sections 应按空行切分为多段，每段 level=0、title 为空。"""
        parser = DocumentParser()
        text = "第一段内容\n\n第二段内容\n\n第三段内容"
        doc = parser.parse_text(text)

        assert len(doc.sections) == 3
        contents = [s["content"] for s in doc.sections]
        assert contents == ["第一段内容", "第二段内容", "第三段内容"]
        for section in doc.sections:
            assert section["level"] == 0
            assert section["title"] == ""

    def test_single_paragraph_no_split(self) -> None:
        """无空行的单段文本应只产生一个 section。"""
        parser = DocumentParser()
        doc = parser.parse_text("只有一段没有空行")

        assert len(doc.sections) == 1
        assert doc.sections[0]["content"] == "只有一段没有空行"

    def test_empty_text_returns_empty_sections(self) -> None:
        """空文本解析后 sections 应为空列表。"""
        parser = DocumentParser()
        doc = parser.parse_text("")

        assert doc.text == ""
        assert doc.sections == []

    def test_whitespace_only_returns_empty_sections(self) -> None:
        """仅含空白字符的文本应被视作无有效段落。"""
        parser = DocumentParser()
        doc = parser.parse_text("   \n\n  \n\n  ")

        assert doc.sections == []

    def test_metadata_marks_raw_text_source(self) -> None:
        """parse_text 产出的 metadata 应标记 source_type 为 raw_text。"""
        parser = DocumentParser()
        doc = parser.parse_text("abc")

        assert doc.metadata["source_type"] == "raw_text"
        assert doc.source == "<raw_text>"


class TestDocIdDeterministic:
    """doc_id 确定性：相同输入产生相同 ID。"""

    def test_same_input_same_doc_id(self) -> None:
        """相同文本应生成相同的 doc_id。"""
        parser = DocumentParser()
        doc1 = parser.parse_text("确定性的文本内容")
        doc2 = parser.parse_text("确定性的文本内容")

        assert doc1.doc_id == doc2.doc_id

    def test_different_input_different_doc_id(self) -> None:
        """不同文本应生成不同的 doc_id。"""
        parser = DocumentParser()
        doc1 = parser.parse_text("文本A")
        doc2 = parser.parse_text("文本B")

        assert doc1.doc_id != doc2.doc_id

    def test_doc_id_matches_generate_function(self) -> None:
        """doc_id 应与 _generate_doc_id(text) 一致（md5 前 16 位）。"""
        text = "校验ID生成逻辑"
        parser = DocumentParser()
        doc = parser.parse_text(text)

        assert doc.doc_id == _generate_doc_id(text)

    def test_explicit_doc_id_overrides_generated(self) -> None:
        """显式传入 doc_id 时应直接使用，不再自动生成。"""
        parser = DocumentParser()
        doc = parser.parse_text("内容", doc_id="custom-id-123")

        assert doc.doc_id == "custom-id-123"


class TestParseNonExistentPath:
    """parse() 对不存在文件路径的处理：当作纯文本。"""

    def test_nonexistent_path_treated_as_text(self) -> None:
        """不存在的路径应作为纯文本处理，doc_type=TXT。"""
        parser = DocumentParser()
        # 该路径不存在，整体被视为文本内容
        raw = "/nonexistent/path/not_a_file.txt"
        doc = parser.parse(raw)

        assert doc.doc_type is DocumentType.TXT
        assert doc.text == raw
        assert doc.metadata["source_type"] == "raw_text"

    def test_plain_sentence_treated_as_text(self) -> None:
        """普通句子（非文件路径）应作为纯文本处理。"""
        parser = DocumentParser()
        sentence = "这是一段普通文本，不是文件路径"
        doc = parser.parse(sentence)

        assert doc.doc_type is DocumentType.TXT
        assert doc.text == sentence


class TestParseTxt:
    """_parse_txt()：纯文本按空行切段。"""

    def test_multiple_paragraphs(self) -> None:
        """多段文本应按空行切分为多个 section，full_text 原样返回。"""
        text = "段落一\n\n段落二\n\n段落三"
        full_text, sections = _parse_txt(text)

        assert full_text == text
        assert len(sections) == 3
        assert [s["content"] for s in sections] == ["段落一", "段落二", "段落三"]

    def test_strips_paragraph_whitespace(self) -> None:
        """段落首尾空白应被 strip 清理。"""
        text = "  段落一  \n\n  段落二  "
        _, sections = _parse_txt(text)

        assert sections[0]["content"] == "段落一"
        assert sections[1]["content"] == "段落二"

    def test_consecutive_blank_lines_ignored(self) -> None:
        """连续空行产生的空段落应被忽略。"""
        text = "段落一\n\n\n\n段落二"
        _, sections = _parse_txt(text)

        assert len(sections) == 2

    def test_empty_text_returns_no_sections(self) -> None:
        """空文本应返回空 sections 列表。"""
        _, sections = _parse_txt("")

        assert sections == []


class TestParseMarkdown:
    """_parse_markdown()：按 # 标题切分章节并保留层级。"""

    def test_split_by_heading_levels(self) -> None:
        """不同层级标题应切分为独立章节，level 与 # 数量一致。"""
        text = "# 标题一\n内容一\n## 子标题\n内容二"
        _, sections = _parse_markdown(text)

        assert len(sections) == 2
        assert sections[0]["title"] == "标题一"
        assert sections[0]["level"] == 1
        assert sections[0]["content"] == "内容一"
        assert sections[1]["title"] == "子标题"
        assert sections[1]["level"] == 2
        assert sections[1]["content"] == "内容二"

    def test_content_before_first_heading(self) -> None:
        """首个标题之前的内容应归入 level=0 的无标题章节。"""
        text = "前言内容\n# 标题一\n正文"
        _, sections = _parse_markdown(text)

        # 第一段为无标题前言
        assert sections[0]["level"] == 0
        assert sections[0]["title"] == ""
        assert sections[0]["content"] == "前言内容"
        # 第二段为标题一
        assert sections[1]["title"] == "标题一"
        assert sections[1]["level"] == 1

    def test_heading_level_three(self) -> None:
        """三级标题应被正确识别为 level=3。"""
        text = "### 深层标题\n深层内容"
        _, sections = _parse_markdown(text)

        assert sections[0]["level"] == 3
        assert sections[0]["title"] == "深层标题"

    def test_full_text_preserved(self) -> None:
        """解析后返回的 full_text 应与输入完全一致。"""
        text = "# 标题\n内容"
        full_text, _ = _parse_markdown(text)

        assert full_text == text


class TestParseFiles:
    """parse() 解析真实临时文件（tmp_path）。"""

    def test_parse_txt_file(self, tmp_path: Path) -> None:
        """解析 .txt 文件应返回 TXT 类型与正确文本/段落/元数据。"""
        content = "第一段\n\n第二段"
        file_path = tmp_path / "sample.txt"
        # newline="" 避免 Windows 下 \n 被转写为 \r\n 影响 file_size 断言
        file_path.write_text(content, encoding="utf-8", newline="")

        parser = DocumentParser()
        doc = parser.parse(str(file_path))

        assert doc.doc_type is DocumentType.TXT
        assert doc.text == content
        assert len(doc.sections) == 2
        assert doc.metadata["file_name"] == "sample.txt"
        assert doc.metadata["file_size"] == len(content.encode("utf-8"))

    def test_parse_markdown_file(self, tmp_path: Path) -> None:
        """解析 .md 文件应返回 MARKDOWN 类型并按标题切分章节。"""
        content = "# 章节一\n内容一\n## 子节\n内容二"
        file_path = tmp_path / "note.md"
        file_path.write_text(content, encoding="utf-8", newline="")

        parser = DocumentParser()
        doc = parser.parse(str(file_path))

        assert doc.doc_type is DocumentType.MARKDOWN
        assert doc.text == content
        assert len(doc.sections) == 2
        assert doc.sections[0]["title"] == "章节一"
        assert doc.sections[0]["level"] == 1
        assert doc.sections[1]["title"] == "子节"
        assert doc.sections[1]["level"] == 2
        assert doc.metadata["file_name"] == "note.md"

    def test_parse_file_doc_id_deterministic(self, tmp_path: Path) -> None:
        """同一文件多次解析应得到相同 doc_id（默认基于路径生成）。"""
        file_path = tmp_path / "stable.txt"
        file_path.write_text("稳定内容", encoding="utf-8", newline="")

        parser = DocumentParser()
        doc1 = parser.parse(str(file_path))
        doc2 = parser.parse(str(file_path))

        assert doc1.doc_id == doc2.doc_id


class TestParsePdf:
    """_parse_pdf()：PyMuPDF 解析 PDF 文档（使用 mock 避免依赖真实文件）。"""

    def test_parse_pdf_extracts_pages(self) -> None:
        """PDF 解析应按页提取文本并生成 sections。"""
        import sys
        from unittest.mock import MagicMock

        from app.knowledge.services.document_parser import _parse_pdf

        # 构造 mock fitz 文档：2 页内容
        mock_doc = MagicMock()
        mock_page1 = MagicMock()
        mock_page1.get_text.return_value = "第一页内容"
        mock_page2 = MagicMock()
        mock_page2.get_text.return_value = "第二页内容"
        mock_doc.__len__ = MagicMock(return_value=2)
        mock_doc.__getitem__ = MagicMock(side_effect=[mock_page1, mock_page2])

        # 构造 mock fitz 模块
        mock_fitz = MagicMock()
        mock_fitz.open.return_value = mock_doc

        # 注入 mock 模块到 sys.modules，使 `import fitz` 能找到它
        original = sys.modules.get("fitz")
        sys.modules["fitz"] = mock_fitz
        try:
            text, sections = _parse_pdf("fake.pdf")
        finally:
            if original is not None:
                sys.modules["fitz"] = original
            else:
                sys.modules.pop("fitz", None)

        assert "第一页内容" in text
        assert "第二页内容" in text
        assert len(sections) == 2
        assert sections[0]["title"] == "第 1 页"
        assert sections[0]["page"] == 1
        assert sections[1]["page"] == 2

    def test_parse_pdf_empty_pages(self) -> None:
        """空页应被跳过，不产生 sections。"""
        import sys
        from unittest.mock import MagicMock

        from app.knowledge.services.document_parser import _parse_pdf

        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_page.get_text.return_value = "   "  # 仅空白
        mock_doc.__len__ = MagicMock(return_value=1)
        mock_doc.__getitem__ = MagicMock(return_value=mock_page)

        mock_fitz = MagicMock()
        mock_fitz.open.return_value = mock_doc

        original = sys.modules.get("fitz")
        sys.modules["fitz"] = mock_fitz
        try:
            text, sections = _parse_pdf("empty.pdf")
        finally:
            if original is not None:
                sys.modules["fitz"] = original
            else:
                sys.modules.pop("fitz", None)

        assert text == ""
        assert sections == []


class TestParseDocx:
    """_parse_docx()：python-docx 解析 Word 文档（使用 mock）。"""

    def test_parse_docx_extracts_paragraphs(self) -> None:
        """DOCX 解析应提取段落并按标题切分章节。"""
        import sys
        from unittest.mock import MagicMock

        from app.knowledge.services.document_parser import _parse_docx

        # 构造 mock Document：1 个标题 + 2 个正文段落
        mock_doc = MagicMock()
        mock_heading = MagicMock()
        mock_heading.text = "章节标题"
        mock_heading.style.name = "Heading 1"

        mock_para1 = MagicMock()
        mock_para1.text = "正文内容一"
        mock_para1.style.name = "Normal"

        mock_para2 = MagicMock()
        mock_para2.text = "正文内容二"
        mock_para2.style.name = "Normal"

        mock_doc.paragraphs = [mock_heading, mock_para1, mock_para2]

        # 构造 mock docx 模块
        mock_docx = MagicMock()
        mock_docx.Document.return_value = mock_doc

        original = sys.modules.get("docx")
        sys.modules["docx"] = mock_docx
        try:
            text, sections = _parse_docx("fake.docx")
        finally:
            if original is not None:
                sys.modules["docx"] = original
            else:
                sys.modules.pop("docx", None)

        assert "正文内容一" in text
        assert "正文内容二" in text
        assert len(sections) >= 1
        assert sections[0]["title"] == "章节标题"
        assert sections[0]["level"] == 1

    def test_parse_docx_empty_paragraphs_skipped(self) -> None:
        """空段落应被跳过。"""
        import sys
        from unittest.mock import MagicMock

        from app.knowledge.services.document_parser import _parse_docx

        mock_doc = MagicMock()
        mock_empty = MagicMock()
        mock_empty.text = ""
        mock_empty.style.name = "Normal"
        mock_doc.paragraphs = [mock_empty]

        mock_docx = MagicMock()
        mock_docx.Document.return_value = mock_doc

        original = sys.modules.get("docx")
        sys.modules["docx"] = mock_docx
        try:
            text, sections = _parse_docx("empty.docx")
        finally:
            if original is not None:
                sys.modules["docx"] = original
            else:
                sys.modules.pop("docx", None)

        assert text == ""
        assert sections == []


class TestParseFileErrorHandling:
    """_parse_file() 错误处理：解析失败返回空结果。"""

    def test_parse_unknown_type_falls_back_to_txt(self, tmp_path: Path) -> None:
        """未知文件类型应尝试按纯文本读取。"""
        file_path = tmp_path / "data.xyz"
        file_path.write_text("未知格式但可读取的文本", encoding="utf-8")

        parser = DocumentParser()
        doc = parser.parse(str(file_path))

        assert doc.doc_type is DocumentType.UNKNOWN
        assert doc.text == "未知格式但可读取的文本"

    def test_parse_file_exception_returns_empty(self, tmp_path: Path) -> None:
        """解析失败时应返回空文本与空 sections（容错不抛出）。"""
        # 创建一个 .pdf 文件但内容为乱码二进制，触发解析异常
        file_path = tmp_path / "fakefile.pdf"
        file_path.write_bytes(b"\x00\x01\x02not a real pdf")

        parser = DocumentParser()
        doc = parser.parse(str(file_path))

        # fitz 解析失败时 _parse_file 捕获异常返回 ("", [])
        # doc_type 仍为 PDF（基于扩展名检测），但 text 为空
        assert doc.doc_type is DocumentType.PDF
        assert doc.text == ""
        assert doc.sections == []
