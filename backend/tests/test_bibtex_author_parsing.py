"""BibTeX 作者名解析回归（Issue #20）。

⚠️ 铁律 #0：本机 Windows 不执行 pytest；本文件按静态检查交付。

验收（2026-11-18 生产实测 `POST /references/import` 返回
`['Lewis, Patrick', 'Perez, Ethan', ...]`，分隔符完整）：
- `Last, First and ...` 逐人拆分，逗号保留「姓, 名」结构；
- `{...}` 包裹视为机构名（literal）；
- Jr. 后缀、无空格逗号、罗马化「名 姓」等边界不丢字符。
"""

from __future__ import annotations

from app.api.v1.endpoints.references import _name_to_text
from app.tools.csl.importers import _parse_bibtex_authors


class TestStandardInput:
    def test_lewis_rag_paper(self) -> None:
        """Issue 原始复现串：4 位作者、分隔符完整。"""
        names = _parse_bibtex_authors(
            "Lewis, Patrick and Perez, Ethan and Piktus, Aleksandra and Petroni, Fabio"
        )
        texts = [_name_to_text(n) for n in names]
        assert texts == [
            "Lewis, Patrick",
            "Perez, Ethan",
            "Piktus, Aleksandra",
            "Petroni, Fabio",
        ]


class TestEdgeCases:
    def test_single_author(self) -> None:
        assert [_name_to_text(n) for n in _parse_bibtex_authors("Smith, John")] == [
            "Smith, John"
        ]

    def test_suffix_jr_kept_in_given(self) -> None:
        """citeproc 惯例：Jr. 属于 given 部分，不得吞掉。"""
        names = _parse_bibtex_authors("Smith, John, Jr.")
        assert names[0].family == "Smith"
        assert names[0].given == "John, Jr."

    def test_braced_institution_is_literal(self) -> None:
        names = _parse_bibtex_authors("{RAG Consortium} and Smith, John")
        assert names[0].literal == "RAG Consortium"
        assert _name_to_text(names[0]) == "RAG Consortium"
        assert names[1].family == "Smith"

    def test_no_space_after_comma(self) -> None:
        names = _parse_bibtex_authors("Lewis,Patrick")
        assert _name_to_text(names[0]) == "Lewis, Patrick"

    def test_given_family_western_order(self) -> None:
        """无逗号按西文「名 姓」惯例拆分。"""
        names = _parse_bibtex_authors("Wei Zhang")
        assert (names[0].family, names[0].given) == ("Zhang", "Wei")

    def test_multiple_authors_and_separator(self) -> None:
        names = _parse_bibtex_authors("Zhang Wei and Li Na")
        assert len(names) == 2

    def test_empty_and_missing(self) -> None:
        assert _parse_bibtex_authors("") == []
