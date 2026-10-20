"""CSL 导入测试（Phase 1 P1-E，计划 §1.5）：BibTeX / RIS → ``list[CslItem]``。

覆盖：
- **round-trip（硬验收）**：冻结语料 6 条经 `to_bibtex` / `to_ris` 导出再导入，关键字段
  （title/authors/year/doi/type）与原条目一致，并尽量覆盖 container/page/volume/publisher/url/language；
- **BibTeX**：7 种必需条目类型 + 常见别名、花括号/引号/裸值三种字段形式、`@comment`/`@preamble` 忽略、
  导出转义（`\\& \\% \\$ \\# \\_ \\{ \\} \\textasciitilde{} \\textasciicircum{} \\textbackslash{}`）反转义、
  citation key 兜底 id、`and` 作者拆分、嵌套花括号、大小写不敏感字段名、中文 UTF-8 原样保留；
- **RIS**：`TY`/`ER` 分记录、字段映射（含 `SP`+`EP` → page）、类型反向映射、未知标签忽略、`LA` → language；
- **健壮性**：空输入 → `[]`；非法输入 → 明确的 `ValidationException`（不静默返回半成品）；
  二次导出稳定（`to(parse(to(items))) == to(items)`）。

纯文本解析，无真实网络。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from app.core.exceptions import ValidationException
from app.tools.csl.exporters import to_bibtex, to_ris
from app.tools.csl.importers import parse_bibtex, parse_ris
from app.tools.csl.models import CslItem, CslName, build_ref_id

CORPUS_PATH = Path(__file__).parent / "fixtures" / "csl" / "corpus.json"
CORPUS: list[dict[str, Any]] = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))["items"]
CORPUS_ITEMS: list[CslItem] = [CslItem.from_csl_json(entry) for entry in CORPUS]


def _item(**overrides: Any) -> CslItem:
    payload: dict[str, Any] = {
        "id": "test:1",
        "type": "article-journal",
        "title": "测试标题",
        "author": [CslName(family="Doe", given="Jane")],
        "issued_year": 2024,
        "language": "zh-CN",
    }
    payload.update(overrides)
    return CslItem(**payload)


def _name_tuples(item: CslItem) -> list[tuple[str, str, str]]:
    return [(name.family, name.given, name.literal) for name in item.author]


def assert_import_matches(original: CslItem, imported: CslItem) -> None:
    """round-trip 的关键字段断言（title/authors/year/doi/type + 其余可保留字段）。"""
    assert imported.title == original.title
    assert imported.type == original.type
    assert _name_tuples(imported) == _name_tuples(original)
    assert imported.issued_year == original.issued_year
    assert imported.doi == original.doi
    assert imported.container_title == original.container_title
    assert imported.volume == original.volume
    assert imported.issue == original.issue
    assert imported.page == original.page
    assert imported.publisher == original.publisher
    assert imported.publisher_place == original.publisher_place
    assert imported.url == original.url
    # language 不在导出字段里，但字符集启发式对冻结语料应与原值一致
    assert imported.language == original.language


# ============================================================
# 1. round-trip（导出 → 导入，硬验收）
# ============================================================


class TestBibtexRoundTrip:
    @pytest.mark.parametrize("original", CORPUS_ITEMS, ids=[item.id for item in CORPUS_ITEMS])
    def test_single_item_round_trip(self, original: CslItem) -> None:
        text = to_bibtex([original])
        imported = parse_bibtex(text)
        assert len(imported) == 1
        assert_import_matches(original, imported[0])

    def test_all_six_items_round_trip(self) -> None:
        imported = parse_bibtex(to_bibtex(CORPUS_ITEMS))
        assert len(imported) == len(CORPUS_ITEMS)
        for original, parsed in zip(CORPUS_ITEMS, imported, strict=True):
            assert_import_matches(original, parsed)

    def test_double_export_is_stable(self) -> None:
        first = to_bibtex(CORPUS_ITEMS)
        second = to_bibtex(parse_bibtex(first))
        assert second == first

    def test_round_trip_text_is_idempotent(self) -> None:
        first = to_bibtex(CORPUS_ITEMS)
        imported = parse_bibtex(first)
        second_import = parse_bibtex(to_bibtex(imported))
        assert [item.model_dump() for item in second_import] == [
            item.model_dump() for item in imported
        ]


class TestRisRoundTrip:
    @pytest.mark.parametrize("original", CORPUS_ITEMS, ids=[item.id for item in CORPUS_ITEMS])
    def test_single_item_round_trip(self, original: CslItem) -> None:
        text = to_ris([original])
        imported = parse_ris(text)
        assert len(imported) == 1
        assert_import_matches(original, imported[0])

    def test_all_six_items_round_trip(self) -> None:
        imported = parse_ris(to_ris(CORPUS_ITEMS))
        assert len(imported) == len(CORPUS_ITEMS)
        for original, parsed in zip(CORPUS_ITEMS, imported, strict=True):
            assert_import_matches(original, parsed)

    def test_double_export_is_stable(self) -> None:
        first = to_ris(CORPUS_ITEMS)
        second = to_ris(parse_ris(first))
        assert second == first

    def test_round_trip_text_is_idempotent(self) -> None:
        imported = parse_ris(to_ris(CORPUS_ITEMS))
        second_import = parse_ris(to_ris(imported))
        assert [item.model_dump() for item in second_import] == [
            item.model_dump() for item in imported
        ]


# ============================================================
# 2. BibTeX 解析细节
# ============================================================


class TestBibtexEntryTypes:
    @pytest.mark.parametrize(
        ("bibtex_type", "csl_type"),
        [
            ("article", "article-journal"),
            ("book", "book"),
            ("inbook", "chapter"),
            ("inproceedings", "paper-conference"),
            ("phdthesis", "thesis"),
            ("techreport", "report"),
            ("misc", "manuscript"),
        ],
    )
    def test_required_types_are_mapped(self, bibtex_type: str, csl_type: str) -> None:
        items = parse_bibtex(f"@{bibtex_type}{{K1, title = {{T}}}}")
        assert [item.type for item in items] == [csl_type]

    @pytest.mark.parametrize(
        ("bibtex_type", "csl_type"),
        [
            ("incollection", "chapter"),
            ("conference", "paper-conference"),
            ("mastersthesis", "thesis"),
            ("unpublished", "manuscript"),
            ("online", "webpage"),
            ("electronic", "webpage"),
            ("www", "webpage"),
            ("dataset", "dataset"),
            ("patent", "patent"),
            ("standard", "standard"),
            ("report", "report"),
            ("proceedings", "book"),
        ],
    )
    def test_common_aliases_are_mapped(self, bibtex_type: str, csl_type: str) -> None:
        items = parse_bibtex(f"@{bibtex_type}{{K1, title = {{T}}}}")
        assert [item.type for item in items] == [csl_type]

    def test_unknown_type_is_rejected(self) -> None:
        with pytest.raises(ValidationException):
            parse_bibtex("@collection{K1, title = {T}}")

    def test_entry_type_is_case_insensitive(self) -> None:
        items = parse_bibtex("@ARTICLE{K1, title = {T}}")
        assert [item.type for item in items] == ["article-journal"]


class TestBibtexSyntax:
    def test_braced_quoted_and_bare_values(self) -> None:
        text = (
            "@article{K1,\n"
            '  title = "Quoted Title",\n'
            "  year = 2024,\n"
            "  journal = {A Braced Journal},\n"
            "  volume = {12},\n"
            "}\n"
        )
        item = parse_bibtex(text)[0]
        assert item.title == "Quoted Title"
        assert item.issued_year == 2024
        assert item.container_title == "A Braced Journal"
        assert item.volume == "12"

    def test_field_names_are_case_insensitive(self) -> None:
        item = parse_bibtex("@article{K1, TITLE = {T}, YEAR = {2024}}")[0]
        assert item.title == "T"
        assert item.issued_year == 2024

    def test_comment_and_preamble_are_ignored(self) -> None:
        text = (
            "@comment{会被忽略：@article{ghost, title = {Ghost}}}\n"
            '@preamble{"\\newcommand{\\noop}[1]{}"}\n'
            "@article{real, title = {Real Title}, year = {2024}}\n"
        )
        items = parse_bibtex(text)
        assert [item.title for item in items] == ["Real Title"]

    def test_line_comments_are_ignored(self) -> None:
        text = "% 这是注释，里面有 @article{fake, title={Fake}}\n@book{real, title={Real}}\n"
        assert [item.title for item in parse_bibtex(text)] == ["Real"]

    def test_entries_are_returned_in_input_order(self) -> None:
        text = "@book{B1, title = {第一本}}\n" "@article{A1, title = {Second}}\n"
        assert [item.title for item in parse_bibtex(text)] == ["第一本", "Second"]

    def test_nested_protective_braces_are_kept(self) -> None:
        item = parse_bibtex("@article{K1, title = {{NASA} Study on {AI}}}")[0]
        assert "NASA" in item.title
        assert "Study" in item.title

    def test_duplicate_field_last_wins(self) -> None:
        item = parse_bibtex("@article{K1, title = {First}, title = {Second}}")[0]
        assert item.title == "Second"

    def test_trailing_comma_is_allowed(self) -> None:
        item = parse_bibtex("@article{K1, title = {T},}")[0]
        assert item.title == "T"


class TestBibtexEscaping:
    @pytest.mark.parametrize("raw", ["&", "%", "$", "#", "_", "{", "}", "~", "^", "\\"])
    def test_special_characters_round_trip(self, raw: str) -> None:
        item = _item(title=f"x{raw}y")
        parsed = parse_bibtex(to_bibtex([item]))
        assert parsed[0].title == f"x{raw}y"

    def test_latex_text_commands_are_unescaped(self) -> None:
        text = (
            "@article{K1, title = {"
            r"A \& B 100\% \$5 \#1 x\_y \{z\} "
            r"\textasciitilde{} \textasciicircum{} \textbackslash{}"
            "}}"
        )
        item = parse_bibtex(text)[0]
        assert item.title == "A & B 100% $5 #1 x_y {z} ~ ^ \\"

    def test_escapes_are_restored_in_author_field(self) -> None:
        item = parse_bibtex(r"@article{K1, title={T}, author={M\&m, Corp}}")[0]
        assert _name_tuples(item) == [("M&m", "Corp", "")]


class TestBibtexFieldMapping:
    def test_author_is_split_on_and(self) -> None:
        item = parse_bibtex("@article{K1, title={T}, author={Es, Shahul and James, Jithin}}")[0]
        assert _name_tuples(item) == [("Es", "Shahul", ""), ("James", "Jithin", "")]

    def test_chinese_literal_authors_are_preserved(self) -> None:
        item = parse_bibtex("@book{K1, title={多智能体系统导论}, author={陈国强 and 刘洋}}")[0]
        assert [name.literal for name in item.author] == ["陈国强", "刘洋"]

    def test_journal_and_booktitle_become_container(self) -> None:
        journal = parse_bibtex("@article{K1, title={T}, journal={J}}")[0]
        booktitle = parse_bibtex("@inproceedings{K2, title={T}, booktitle={Proc}}")[0]
        assert journal.container_title == "J"
        assert booktitle.container_title == "Proc"

    def test_number_becomes_issue_and_pages_are_normalized(self) -> None:
        item = parse_bibtex("@article{K1, title={T}, volume={12}, number={3}, pages={150--160}}")[0]
        assert item.volume == "12"
        assert item.issue == "3"
        assert item.page == "150-160"

    def test_publisher_address_doi_url(self) -> None:
        item = parse_bibtex(
            "@book{K1, title={T}, publisher={科学出版社}, address={北京}, "
            "doi={https://doi.org/10.1234/ABC}, url={https://example.org/x}}"
        )[0]
        assert item.publisher == "科学出版社"
        assert item.publisher_place == "北京"
        assert item.doi == "10.1234/abc"
        assert item.url == "https://example.org/x"

    def test_year_and_date_fields(self) -> None:
        assert parse_bibtex("@article{K1, title={T}, year={2024}}")[0].issued_year == 2024
        assert parse_bibtex("@article{K2, title={T}, date={2023-09-26}}")[0].issued_year == 2023
        assert parse_bibtex("@article{K3, title={T}}")[0].issued_year is None

    def test_unknown_fields_are_ignored(self) -> None:
        item = parse_bibtex(
            "@article{K1, title={T}, abstract={很长}, keywords={a,b}, month=jan, note={x}}"
        )[0]
        assert item.title == "T"


class TestBibtexIdsAndLanguage:
    def test_doi_has_priority_for_id(self) -> None:
        item = parse_bibtex("@article{MyKey, title={T}, doi={10.1/ABC}}")[0]
        assert item.doi == "10.1/abc"
        assert item.id == "doi:10.1/abc"

    def test_source_id_field_has_priority_over_key(self) -> None:
        item = parse_bibtex("@misc{MyKey, title={T}, source_id={s-1}}")[0]
        assert item.id == "src:s-1"

    def test_citation_key_is_id_fallback(self) -> None:
        item = parse_bibtex("@article{MyKey, title={T}}")[0]
        assert item.id == "MyKey"

    def test_key_may_contain_punctuation(self) -> None:
        item = parse_bibtex("@article{Es:2024/x-1, title={T}}")[0]
        assert item.id == "Es:2024/x-1"

    def test_language_is_detected_from_content(self) -> None:
        zh = parse_bibtex("@book{K1, title={中文书}, author={陈国强}}")[0]
        en = parse_bibtex("@book{K2, title={English Book}, author={Doe, Jane}}")[0]
        assert zh.language == "zh-CN"
        assert en.language == "en-US"


class TestBibtexInvalidInput:
    def test_empty_input_returns_empty_list(self) -> None:
        assert parse_bibtex("") == []
        assert parse_bibtex("   \n\t\n") == []
        assert parse_bibtex("% 只有注释\n") == []

    def test_unclosed_entry_is_rejected(self) -> None:
        with pytest.raises(ValidationException):
            parse_bibtex("@article{K1, title = {Unclosed")

    def test_missing_title_is_rejected(self) -> None:
        with pytest.raises(ValidationException):
            parse_bibtex("@article{K1, year = {2024}}")

    def test_missing_key_is_rejected(self) -> None:
        with pytest.raises(ValidationException):
            parse_bibtex("@article{, title = {T}}")

    def test_garbage_text_is_rejected(self) -> None:
        with pytest.raises(ValidationException):
            parse_bibtex("这不是 BibTeX，只是普通文本")

    def test_trailing_garbage_is_rejected(self) -> None:
        with pytest.raises(ValidationException):
            parse_bibtex("@article{K1, title = {T}} 尾部垃圾")

    def test_string_macro_is_rejected(self) -> None:
        with pytest.raises(ValidationException):
            parse_bibtex("@string{foo = {bar}}")

    def test_parenthesis_form_is_rejected(self) -> None:
        with pytest.raises(ValidationException):
            parse_bibtex("@article(K1, title = {T})")

    def test_missing_equals_is_rejected(self) -> None:
        with pytest.raises(ValidationException):
            parse_bibtex("@article{K1, title {T}}")

    def test_non_string_input_is_rejected(self) -> None:
        with pytest.raises(ValidationException):
            parse_bibtex(None)  # type: ignore[arg-type]


class TestBibtexSyntaxErrors:
    """逐条覆盖扫描器的错误分支：任何异常都必须转成明确的 ValidationException。"""

    def test_invalid_entry_head_is_rejected(self) -> None:
        with pytest.raises(ValidationException):
            parse_bibtex("@1234{K1, title = {T}}")

    def test_unclosed_comment_is_rejected(self) -> None:
        with pytest.raises(ValidationException):
            parse_bibtex("@comment{没有闭合")

    def test_key_with_brace_is_rejected(self) -> None:
        with pytest.raises(ValidationException):
            parse_bibtex("@article{K{1, title = {T}}")

    def test_eof_after_key_is_rejected(self) -> None:
        with pytest.raises(ValidationException):
            parse_bibtex("@article{K1")

    def test_entry_without_fields_is_rejected(self) -> None:
        with pytest.raises(ValidationException):
            parse_bibtex("@article{K1}")

    def test_eof_after_comma_is_rejected(self) -> None:
        with pytest.raises(ValidationException):
            parse_bibtex("@article{K1, title = {T},")

    def test_invalid_field_name_is_rejected(self) -> None:
        with pytest.raises(ValidationException):
            parse_bibtex("@article{K1, 123 = {T}}")

    def test_missing_value_at_eof_is_rejected(self) -> None:
        with pytest.raises(ValidationException):
            parse_bibtex("@article{K1, title =")

    def test_missing_closing_brace_after_value_is_rejected(self) -> None:
        with pytest.raises(ValidationException):
            parse_bibtex("@article{K1, title = {T}")

    def test_junk_after_value_is_rejected(self) -> None:
        with pytest.raises(ValidationException):
            parse_bibtex("@article{K1, title = {T} xyz}")

    def test_empty_bare_value_is_rejected(self) -> None:
        with pytest.raises(ValidationException):
            parse_bibtex("@article{K1, title = ,}")

    def test_unclosed_quoted_value_is_rejected(self) -> None:
        with pytest.raises(ValidationException):
            parse_bibtex('@article{K1, title = "未闭合}')

    def test_escaped_quote_inside_quoted_value(self) -> None:
        item = parse_bibtex('@article{K1, title = "A \\"quoted\\" B"}')[0]
        assert "quoted" in item.title

    def test_whitespace_around_key_and_fields_is_tolerated(self) -> None:
        item = parse_bibtex("@article{  K1 ,  title = {T}  }")[0]
        assert item.title == "T"

    def test_trailing_and_in_author_is_skipped(self) -> None:
        item = parse_bibtex("@article{K1, title={T}, author={Doe, Jane and }}")[0]
        assert _name_tuples(item) == [("Doe", "Jane", "")]

    def test_fully_braced_corporate_author_becomes_literal(self) -> None:
        item = parse_bibtex("@book{K1, title={T}, author={{World Health Organization}}}")[0]
        assert item.author == [CslName(literal="World Health Organization")]

    def test_nested_braced_author_does_not_crash(self) -> None:
        item = parse_bibtex("@book{K1, title={T}, author={{World {Health} Organization}}}")[0]
        assert len(item.author) == 1
        assert "World" in item.author[0].literal

    def test_partially_braced_author_is_parsed_as_name(self) -> None:
        """外层花括号没有包住整体（`{A} {B}`）时按普通姓名解析，不当作机构 literal。"""
        item = parse_bibtex("@book{K1, title={T}, author={{A} {B}}}")[0]
        assert len(item.author) == 1
        assert item.author[0].literal == ""


# ============================================================
# 3. RIS 解析细节
# ============================================================


class TestRisEntryTypes:
    @pytest.mark.parametrize(
        ("ris_type", "csl_type"),
        [
            ("JOUR", "article-journal"),
            ("MGZN", "article-magazine"),
            ("NEWS", "article-newspaper"),
            ("BOOK", "book"),
            ("CHAP", "chapter"),
            ("CONF", "paper-conference"),
            ("THES", "thesis"),
            ("RPRT", "report"),
            ("UNPB", "manuscript"),
            ("DATA", "dataset"),
            ("ELEC", "webpage"),
            ("STAND", "standard"),
            ("PATENT", "patent"),
            ("GEN", "manuscript"),
        ],
    )
    def test_types_are_mapped(self, ris_type: str, csl_type: str) -> None:
        items = parse_ris(f"TY  - {ris_type}\nTI  - T\nER  -\n")
        assert [item.type for item in items] == [csl_type]

    def test_unknown_type_is_rejected(self) -> None:
        with pytest.raises(ValidationException):
            parse_ris("TY  - WEIRD\nTI  - T\nER  -\n")


class TestRisFieldMapping:
    def test_journal_fields_are_mapped(self) -> None:
        text = (
            "TY  - JOUR\n"
            "AU  - Es, Shahul\n"
            "AU  - James, Jithin\n"
            "TI  - Ragas\n"
            "JO  - Journal of Testing\n"
            "PY  - 2023\n"
            "VL  - 12\n"
            "IS  - 3\n"
            "SP  - 150\n"
            "EP  - 160\n"
            "DO  - 10.1/ABC\n"
            "UR  - https://example.org/x\n"
            "ER  -\n"
        )
        item = parse_ris(text)[0]
        assert item.type == "article-journal"
        assert _name_tuples(item) == [("Es", "Shahul", ""), ("James", "Jithin", "")]
        assert item.title == "Ragas"
        assert item.container_title == "Journal of Testing"
        assert item.issued_year == 2023
        assert item.volume == "12"
        assert item.issue == "3"
        assert item.page == "150-160"
        assert item.doi == "10.1/abc"
        assert item.url == "https://example.org/x"

    def test_chinese_literals_are_preserved(self) -> None:
        text = (
            "TY  - JOUR\n"
            "AU  - 牛耕田\n"
            "AU  - 李泽\n"
            "TI  - 面向学位论文的中文参考文献著录规则研究\n"
            "JO  - 中国科技期刊研究\n"
            "PY  - 2024\n"
            "ER  -\n"
        )
        item = parse_ris(text)[0]
        assert [name.literal for name in item.author] == ["牛耕田", "李泽"]
        assert item.title == "面向学位论文的中文参考文献著录规则研究"
        assert item.container_title == "中国科技期刊研究"

    def test_t2_is_used_for_conference_container(self) -> None:
        text = (
            "TY  - CONF\n"
            "TI  - A Survey\n"
            "T2  - Proceedings of the 2024 Conference\n"
            "PB  - ACM\n"
            "CY  - New York\n"
            "ER  -\n"
        )
        item = parse_ris(text)[0]
        assert item.container_title == "Proceedings of the 2024 Conference"
        assert item.publisher == "ACM"
        assert item.publisher_place == "New York"

    def test_single_sp_becomes_page(self) -> None:
        item = parse_ris("TY  - JOUR\nTI  - T\nSP  - 42\nER  -\n")[0]
        assert item.page == "42"

    def test_py_with_full_date(self) -> None:
        item = parse_ris("TY  - JOUR\nTI  - T\nPY  - 2023/09/26/\nER  -\n")[0]
        assert item.issued_year == 2023

    def test_la_tag_sets_language(self) -> None:
        zh = parse_ris("TY  - JOUR\nTI  - 中文\nLA  - zh\nER  -\n")[0]
        en = parse_ris("TY  - JOUR\nTI  - English\nLA  - en\nER  -\n")[0]
        assert zh.language == "zh-CN"
        assert en.language == "en-US"

    def test_unknown_tags_are_ignored(self) -> None:
        text = (
            "TY  - JOUR\n"
            "TI  - T\n"
            "DB  - CrossRef\n"
            "KW  - retrieval\n"
            "AB  - abstract\n"
            "SN  - 1234-5678\n"
            "M3  - Journal Article\n"
            "ER  -\n"
        )
        item = parse_ris(text)[0]
        assert item.title == "T"

    def test_multiple_records_are_split_on_er(self) -> None:
        text = "TY  - BOOK\nTI  - First\nER  -\n" "\n" "TY  - JOUR\nTI  - Second\nER  -\n"
        items = parse_ris(text)
        assert [item.title for item in items] == ["First", "Second"]

    def test_empty_tags_without_space_are_accepted(self) -> None:
        """exporter 输出 `ER  -`（无值、无尾随空格），解析不得报错。"""
        assert parse_ris("TY  - JOUR\nTI  - T\nER  -\n")[0].title == "T"


class TestRisIdsAndLanguage:
    def test_doi_has_priority_for_id(self) -> None:
        item = parse_ris("TY  - JOUR\nTI  - T\nDO  - 10.1/ABC\nER  -\n")[0]
        assert item.doi == "10.1/abc"
        assert item.id == "doi:10.1/abc"

    def test_title_hash_is_id_fallback(self) -> None:
        item = parse_ris("TY  - JOUR\nTI  - Some Title\nER  -\n")[0]
        assert item.id == build_ref_id(title="Some Title")

    def test_language_heuristic_without_la(self) -> None:
        zh = parse_ris("TY  - JOUR\nTI  - 中文题名\nER  -\n")[0]
        en = parse_ris("TY  - JOUR\nTI  - English title\nER  -\n")[0]
        assert zh.language == "zh-CN"
        assert en.language == "en-US"


class TestRisInvalidInput:
    def test_empty_input_returns_empty_list(self) -> None:
        assert parse_ris("") == []
        assert parse_ris("   \n\n") == []

    def test_garbage_text_is_rejected(self) -> None:
        with pytest.raises(ValidationException):
            parse_ris("这不是 RIS 记录")

    def test_missing_er_is_rejected(self) -> None:
        with pytest.raises(ValidationException):
            parse_ris("TY  - JOUR\nTI  - T\n")

    def test_missing_ty_is_rejected(self) -> None:
        with pytest.raises(ValidationException):
            parse_ris("TI  - T\nER  -\n")

    def test_invalid_tag_line_is_rejected(self) -> None:
        with pytest.raises(ValidationException):
            parse_ris("TY  - JOUR\n这一行不是标签\nER  -\n")

    def test_missing_title_is_rejected(self) -> None:
        with pytest.raises(ValidationException):
            parse_ris("TY  - JOUR\nPY  - 2024\nER  -\n")

    def test_trailing_content_after_er_is_rejected(self) -> None:
        with pytest.raises(ValidationException):
            parse_ris("TY  - JOUR\nTI  - T\nER  -\n尾巴\n")

    def test_non_string_input_is_rejected(self) -> None:
        with pytest.raises(ValidationException):
            parse_ris(None)  # type: ignore[arg-type]

    def test_new_ty_before_er_is_rejected(self) -> None:
        with pytest.raises(ValidationException):
            parse_ris("TY  - JOUR\nTI  - T\nTY  - BOOK\nER  -\n")

    def test_empty_ty_value_is_rejected(self) -> None:
        with pytest.raises(ValidationException):
            parse_ris("TY  -\nTI  - T\nER  -\n")

    def test_py_year_is_parsed_from_full_date(self) -> None:
        item = parse_ris("TY  - JOUR\nTI  - T\nPY  - 2024/03/01/\nER  -\n")[0]
        assert item.issued_year == 2024


class TestTypeMapConsistency:
    """导入映射必须与 exporters 的导出映射互逆（防止两侧漂移）。"""

    def test_every_exported_bibtex_type_parses_back_to_same_bibtex_type(self) -> None:
        from app.tools.csl.exporters import BIBTEX_TYPE_MAP

        for csl_type, bibtex_type in BIBTEX_TYPE_MAP.items():
            item = _item(type=csl_type)
            imported = parse_bibtex(to_bibtex([item]))
            assert len(imported) == 1
            assert BIBTEX_TYPE_MAP[imported[0].type] == bibtex_type, csl_type

    def test_every_exported_ris_type_parses_back_to_same_ris_type(self) -> None:
        from app.tools.csl.exporters import RIS_TYPE_MAP

        for csl_type, ris_type in RIS_TYPE_MAP.items():
            item = _item(type=csl_type)
            imported = parse_ris(to_ris([item]))
            assert len(imported) == 1
            assert RIS_TYPE_MAP[imported[0].type] == ris_type, csl_type


# ============================================================
# 4. 真实文件形态（多行、CRLF、混合）
# ============================================================


class TestRealWorldShape:
    def test_crlf_line_endings_are_accepted(self) -> None:
        text = "@article{K1, title = {T}, year = {2024}}\r\n"
        assert parse_bibtex(text)[0].title == "T"
        ris = "TY  - JOUR\r\nTI  - T\r\nER  -\r\n"
        assert parse_ris(ris)[0].title == "T"

    def test_bibtex_multiline_fields(self) -> None:
        text = (
            "@article{K1,\n"
            "  author = {Doe, Jane\n"
            "            and Roe, Richard},\n"
            "  title = {A very\n"
            "    long title},\n"
            "  year = {2024}\n"
            "}\n"
        )
        item = parse_bibtex(text)[0]
        assert _name_tuples(item) == [("Doe", "Jane", ""), ("Roe", "Richard", "")]
        assert "very" in item.title

    def test_ris_crlf_and_blank_lines_between_records(self) -> None:
        text = "TY  - JOUR\r\nTI  - First\r\nER  -\r\n\r\nTY  - BOOK\r\nTI  - Second\r\nER  -\r\n"
        assert [item.title for item in parse_ris(text)] == ["First", "Second"]

    def test_exported_corpus_text_can_be_reimported_as_file_like_payload(self) -> None:
        """导出文本（末尾换行、条目间空行）作为完整「文件」导入不报错。"""
        assert len(parse_bibtex(to_bibtex(CORPUS_ITEMS))) == 6
        assert len(parse_ris(to_ris(CORPUS_ITEMS))) == 6
        assert re.search(r"\n\n@", to_bibtex(CORPUS_ITEMS)) is not None
