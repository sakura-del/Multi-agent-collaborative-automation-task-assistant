"""CleanFullTextParser 单元测试（Issue #73 Phase A）。

PDF fixture 一律用 PyMuPDF 现场生成，不提交二进制测试资产。
"""

import fitz
import pytest

from app.knowledge.services.fulltext_parser import CleanFullTextParser, ParseStatus


def _make_two_column_pdf() -> bytes:
    """左侧 Introduction、右侧 Method，末尾 References 位于页脚区。"""
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((40, 100), "1 Introduction", fontsize=12)
    page.insert_text((40, 140), "Left column text about background.", fontsize=10)
    page.insert_text((330, 100), "2 Method", fontsize=12)
    page.insert_text((330, 140), "Right column text about method.", fontsize=10)
    page.insert_text((40, 760), "References", fontsize=12)
    page.insert_text((40, 780), "[1] should be truncated.", fontsize=10)
    return doc.tobytes()


def _make_references_mid_page_pdf() -> bytes:
    """References 位于正文后半段但仍在页脚区之上，用于验证截断逻辑。"""
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((40, 100), "1 Introduction", fontsize=12)
    page.insert_text((40, 140), "Body text of the introduction paragraph.", fontsize=10)
    page.insert_text((40, 560), "References", fontsize=12)
    page.insert_text((40, 590), "[1] Hidden reference entry.", fontsize=10)
    return doc.tobytes()


def test_two_column_order_and_references_truncated() -> None:
    parsed = CleanFullTextParser().parse_pdf(_make_two_column_pdf())
    assert parsed.status is ParseStatus.OK
    text = "\n".join(section.text for section in parsed.sections)
    assert text.index("1 Introduction") < text.index("2 Method")
    assert "should be truncated" not in text
    assert parsed.sections[0].page_start == 1


def test_scanned_pdf_is_rejected() -> None:
    doc = fitz.open()
    _ = doc.new_page()  # 空白页：无文本层
    parsed = CleanFullTextParser().parse_pdf(doc.tobytes())
    assert parsed.status is ParseStatus.SCANNED_NO_TEXT_LAYER
    assert parsed.sections == []


def test_references_truncated_in_latter_half() -> None:
    parsed = CleanFullTextParser().parse_pdf(_make_references_mid_page_pdf())
    assert parsed.status is ParseStatus.OK
    text = "\n".join(section.text for section in parsed.sections)
    assert "Body text of the introduction paragraph." in text
    assert "Hidden reference entry" not in text
    assert "References" not in text


def test_invalid_pdf_returns_failed() -> None:
    parsed = CleanFullTextParser().parse_pdf(b"not a pdf")
    assert parsed.status is ParseStatus.FAILED
    assert parsed.sections == []


@pytest.mark.parametrize(
    ("title", "expected_role"),
    [
        ("1 Introduction", "background"),
        ("2 Related Work", "background"),
        ("3 Method", "method"),
        ("4 Experiments", "result"),
        ("5 Limitations", "limitation"),
        ("Appendix", "other"),
    ],
)
def test_section_role_mapping(title: str, expected_role: str) -> None:
    assert CleanFullTextParser().role_for_title(title) == expected_role


def test_parse_html_sections_by_heading() -> None:
    html = (
        b"<html><body>"
        b"<h1>1 Introduction</h1><p>Background body.</p>"
        b"<h2>2 Method</h2><p>Method body.</p>"
        b"</body></html>"
    )
    parsed = CleanFullTextParser().parse_html(html)
    assert parsed.status is ParseStatus.OK
    assert [section.title for section in parsed.sections] == ["1 Introduction", "2 Method"]
    assert [section.role for section in parsed.sections] == ["background", "method"]


def test_empty_html_returns_empty() -> None:
    parsed = CleanFullTextParser().parse_html(b"")
    assert parsed.status is ParseStatus.EMPTY
    assert parsed.sections == []


def test_parse_jats_sections() -> None:
    jats = (
        b"<article><body>"
        b"<sec><title>Introduction</title><p>Background paragraph.</p></sec>"
        b"<sec><title>Methods</title><p>Method paragraph.</p></sec>"
        b"</body></article>"
    )
    parsed = CleanFullTextParser().parse_jats(jats)
    assert parsed.status is ParseStatus.OK
    assert [section.title for section in parsed.sections] == ["Introduction", "Methods"]
    assert [section.role for section in parsed.sections] == ["background", "method"]


def test_malformed_jats_returns_failed() -> None:
    parsed = CleanFullTextParser().parse_jats(b"<article><body>")
    assert parsed.status is ParseStatus.FAILED
    assert parsed.sections == []
