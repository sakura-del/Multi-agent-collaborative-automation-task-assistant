"""NoteExpress `.net` 导入与题录摘要解析测试（Issue #67 Step 1）。

覆盖：
- NoteExpress `{Tag}: value` 记录解析（多记录、BOM、续行、中文标签）；
- 三种格式的摘要字段：BibTeX `abstract`、RIS `AB`/`N2`、NoteExpress `{Abstract}`；
- 摘要截断 4000 字；
- 语法错误 → ValidationException（绝不静默返回半成品）。

这些用例与 HTTP 端点测试（`test_references_import_api.py`）分工：
这里锁**纯函数语义**，那里锁 HTTP 映射。
"""

from __future__ import annotations

import pytest

from app.core.exceptions import ValidationException
from app.tools.csl.importers import (
    MAX_ABSTRACT_LENGTH,
    parse_bibtex,
    parse_noteexpress,
    parse_ris,
)

NOTEEXPRESS_SAMPLE = """{Reference Type}: Journal Article
{Title}: 检索增强生成在软件工程中的应用综述
{Authors}: 张犬俊;李四
{Author Address}: 清华大学软件学院
{Journal}: 软件学报
{Year}: 2026
{Volume}: 37
{Issue}: 3
{Pages}: 1-18
{Abstract}: 检索增强生成（RAG）已成为大语言模型落地软件工程的重要范式。
本文系统梳理了代码生成、缺陷修复与测试三个方向的研究进展。
{Keywords}: 检索增强生成;软件工程;大模型
{DOI}: 10.13328/j.cnki.jos.007567
{URL}: https://d.wanfangdata.com.cn/periodical/rjxb202603017
"""


class TestParseNoteexpress:
    def test_parses_cjk_journal_article(self) -> None:
        items = parse_noteexpress(NOTEEXPRESS_SAMPLE)

        assert len(items) == 1
        item = items[0]
        assert item.id == "doi:10.13328/j.cnki.jos.007567"
        assert item.type == "article-journal"
        assert item.title == "检索增强生成在软件工程中的应用综述"
        # 中文姓名按 CSL 规范进 `literal`（不拆姓/名）
        assert [name.literal or f"{name.family}{name.given}" for name in item.author] == [
            "张犬俊",
            "李四",
        ]
        assert item.container_title == "软件学报"
        assert item.issued_year == 2026
        assert item.volume == "37"
        assert item.issue == "3"
        assert item.page == "1-18"
        assert item.doi == "10.13328/j.cnki.jos.007567"
        assert item.language.startswith("zh")

    def test_multiline_abstract_is_joined(self) -> None:
        item = parse_noteexpress(NOTEEXPRESS_SAMPLE)[0]

        assert item.abstract is not None
        assert "检索增强生成（RAG）" in item.abstract
        # 续行必须拼回摘要，而不是被丢弃或当成新字段
        assert "代码生成、缺陷修复与测试" in item.abstract
        assert "\n" in item.abstract

    def test_parses_multiple_records_separated_by_blank_line(self) -> None:
        text = (
            "{Reference Type}: Thesis\n"
            "{Title}: 多智能体协作综述\n"
            "{Authors}: 王五\n"
            "{Year}: 2025\n"
            "{URL}: https://d.wanfangdata.com.cn/thesis/1\n"
            "\n"
            "{Reference Type}: Conference Proceedings\n"
            "{Title}: 检索增强生成评测\n"
            "{Authors}: Smith, John;李四\n"
            "{Year}: 2024\n"
            "{DOI}: 10.1234/c.2024.1\n"
        )

        items = parse_noteexpress(text)

        assert [item.type for item in items] == ["thesis", "paper-conference"]
        assert [item.title for item in items] == ["多智能体协作综述", "检索增强生成评测"]
        # 中文分号与中文逗号都是作者分隔符；ASCII 逗号仍是 Family, Given
        assert items[1].author[0].family == "Smith"
        assert items[1].author[0].given == "John"

    def test_strips_utf8_bom(self) -> None:
        text = "\ufeff" + NOTEEXPRESS_SAMPLE
        assert parse_noteexpress(text)[0].title == "检索增强生成在软件工程中的应用综述"

    def test_unknown_reference_type_falls_back_to_manuscript(self) -> None:
        text = "{Reference Type}: Some Exotic Type\n{Title}: T\n{Year}: 2024\n"
        assert parse_noteexpress(text)[0].type == "manuscript"

    def test_chinese_reference_type_alias(self) -> None:
        text = "{文献类型}: 学位论文\n{题名}: 中文标题\n{作者}: 张三\n{年份}: 2024\n"
        item = parse_noteexpress(text)[0]
        assert item.type == "thesis"
        assert item.title == "中文标题"
        assert item.issued_year == 2024

    def test_empty_returns_empty_list(self) -> None:
        assert parse_noteexpress("") == []
        assert parse_noteexpress("   \n\n") == []

    def test_missing_title_raises(self) -> None:
        with pytest.raises(ValidationException):
            parse_noteexpress("{Reference Type}: Journal Article\n{Year}: 2024\n")

    def test_field_before_reference_type_raises(self) -> None:
        with pytest.raises(ValidationException):
            parse_noteexpress("{Title}: 没有类型行\n")

    def test_non_field_line_without_previous_field_raises(self) -> None:
        with pytest.raises(ValidationException):
            parse_noteexpress("这不是 NoteExpress\n{Reference Type}: Journal Article\n")

    def test_abstract_is_truncated_to_limit(self) -> None:
        text = (
            "{Reference Type}: Journal Article\n"
            "{Title}: T\n"
            f"{{Abstract}}: {'长' * (MAX_ABSTRACT_LENGTH + 500)}\n"
            "{Year}: 2024\n"
            "{DOI}: 10.1234/a\n"
        )
        item = parse_noteexpress(text)[0]
        assert item.abstract is not None
        assert len(item.abstract) == MAX_ABSTRACT_LENGTH


class TestAbstractFromBibtexAndRis:
    def test_bibtex_abstract_is_imported(self) -> None:
        item = parse_bibtex("@article{K1, title={T}, abstract={An abstract.}}")[0]
        assert item.abstract == "An abstract."

    def test_ris_ab_is_imported(self) -> None:
        item = parse_ris("TY  - JOUR\nTI  - T\nAB  - RIS abstract\nER  -\n")[0]
        assert item.abstract == "RIS abstract"

    def test_ris_n2_fallback(self) -> None:
        item = parse_ris("TY  - JOUR\nTI  - T\nN2  - Secondary abstract\nER  -\n")[0]
        assert item.abstract == "Secondary abstract"

    def test_abstract_absent_stays_none(self) -> None:
        assert parse_bibtex("@article{K1, title={T}}")[0].abstract is None
        assert parse_ris("TY  - JOUR\nTI  - T\nER  -\n")[0].abstract is None
