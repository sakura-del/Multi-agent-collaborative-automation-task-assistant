"""Issue #4 剩余三项：`medium` 字段、拼音排序、全大写姓名归一。

三项都来自 M1/M2 记录在案的遗留（AGENTS.md §6.1 Issue #4 行）：

1. **`CslItem.medium`**（2025 载体标识 `MM` 等）：样式侧早已支持 `medium` 变量
   （`carrier-identifier` 宏），但 `CslItem` 没有该字段，数据无法流到样式 → `[J/MM]` 永远出不来。
2. **author-date 中文排序**：渲染器按 Unicode 码位排序（`_author_date_key`），
   中文姓名实际应按**拼音**排序，否则「赵」排在「陈」前之类明显不合字顺。
3. **全大写姓名归一**：`citeproc-py` 的 `name-part text-case="capitalize-first"` 抛
   `AttributeError`，故样式不对 family 做大小写变换；若上游给 `"SMITH J"` 就会原样输出全大写。
   需在**数据层**归一为「首字母大写 + 其余小写」。
"""

from __future__ import annotations

import json
from pathlib import Path

from app.tools.csl.models import (
    CslItem,
    CslName,
    normalize_name_case,
    parse_csl_name,
    sort_key_text,
)
from app.tools.csl.renderer import CitationRenderer

CORPUS = Path(__file__).parent / "fixtures" / "csl" / "corpus.json"


# ===== 1. medium 字段（2025 载体标识 MM）=====


class TestMediumField:
    def test_item_has_medium_field(self) -> None:
        item = CslItem(id="src:1", type="article-journal", title="题名", medium="MM")

        assert item.medium == "MM"

    def test_medium_round_trips_through_csl_json(self) -> None:
        item = CslItem(id="src:1", type="article-journal", title="题名", medium="MM")

        payload = item.to_csl_json()

        assert payload["medium"] == "MM"
        assert CslItem.from_csl_json(payload).medium == "MM"

    def test_medium_absent_by_default(self) -> None:
        item = CslItem(id="src:1", type="article-journal", title="题名")

        assert item.medium is None
        assert "medium" not in item.to_csl_json()

    def test_medium_produces_carrier_identifier(self) -> None:
        """`medium=MM` → 参考文献出现 `[J/MM]`（2025 附录 A.2 载体标识）。"""
        item = CslItem(
            id="src:1",
            type="article-journal",
            title="缩微资料示例",
            medium="MM",
            issued_year=2024,
            language="zh-CN",
        )

        entries = CitationRenderer().render([item], style_id="gbt7714-2025-numeric").entries

        assert entries
        assert "[J/MM]" in entries[0], entries[0]

    def test_medium_wins_over_url_derived_ol(self) -> None:
        """显式 medium 优先于「有 URL 即 OL」的推断。"""
        item = CslItem(
            id="src:1",
            type="article-journal",
            title="缩微资料示例",
            medium="MM",
            url="https://example.org/x",
            issued_year=2024,
            language="zh-CN",
        )

        entries = CitationRenderer().render([item], style_id="gbt7714-2025-numeric").entries

        assert "[J/MM]" in entries[0]
        assert "/OL" not in entries[0]

    def test_medium_flows_from_bibliography_entry(self) -> None:
        from app.tools.csl.models import csl_item_from_bibliography_entry

        item = csl_item_from_bibliography_entry(
            {"title": "题名", "type": "article-journal", "medium": "MM"}
        )

        assert item is not None
        assert item.medium == "MM"


# ===== 2. 拼音排序 =====


class TestPinyinSorting:
    def test_sort_key_text_uses_pinyin_for_cjk(self) -> None:
        """中文姓名排序键必须按拼音，而不是 Unicode 码位。"""
        # 码位序：赵(U+8D75) > 陈(U+9648) > 白(U+767D)；拼音序：bai < chen < zhao
        names = ["赵敏", "陈国强", "白露"]

        ordered = sorted(names, key=sort_key_text)

        assert ordered == ["白露", "陈国强", "赵敏"]

    def test_sort_key_text_keeps_latin_unchanged(self) -> None:
        assert sort_key_text("Smith") == "smith"
        assert sort_key_text("  Es  ") == "es"

    def test_author_date_bibliography_sorted_by_pinyin(self) -> None:
        """端到端：author-date 参考文献按拼音字顺排列。"""
        items = [
            CslItem(
                id=f"src:{index}",
                type="book",
                title=f"题名{index}",
                author=[CslName(literal=name)],
                issued_year=2020,
                language="zh-CN",
            )
            for index, name in enumerate(["赵敏", "陈国强", "白露"], start=1)
        ]

        rendered = CitationRenderer().render(items, style_id="gbt7714-2025-author-date")

        authors = [line.split(".", 1)[0].split()[0] for line in rendered.entries]
        assert authors == ["白露", "陈国强", "赵敏"], rendered.entries

    def test_pinyin_sort_is_deterministic_for_same_pronunciation(self) -> None:
        """同音不同字：拼音键相同，回落到原文码位保证确定性排序。"""
        items = [
            CslItem(
                id=f"src:{index}",
                type="book",
                title=f"题名{index}",
                author=[CslName(literal=name)],
                issued_year=2020,
                language="zh-CN",
            )
            for index, name in enumerate(["张伟", "章伟"], start=1)
        ]

        first = CitationRenderer().render(items, style_id="gbt7714-2025-author-date")
        second = CitationRenderer().render(items, style_id="gbt7714-2025-author-date")

        assert first.entries == second.entries


# ===== 3. 全大写姓名归一 =====


class TestNormalizeNameCase:
    def test_all_caps_family_is_normalized(self) -> None:
        assert normalize_name_case("SMITH") == "Smith"

    def test_all_caps_family_and_given(self) -> None:
        assert normalize_name_case("SMITH J") == "Smith J"

    def test_mixed_case_untouched(self) -> None:
        """已经正常的姓名不得被改写。"""
        assert normalize_name_case("Smith") == "Smith"
        assert normalize_name_case("Espinosa-Anke") == "Espinosa-Anke"
        assert normalize_name_case("McDonald") == "McDonald"

    def test_cjk_untouched(self) -> None:
        assert normalize_name_case("牛耕田") == "牛耕田"

    def test_hyphenated_all_caps(self) -> None:
        assert normalize_name_case("ESPINOSA-ANKE") == "Espinosa-Anke"

    def test_empty_and_whitespace(self) -> None:
        assert normalize_name_case("") == ""
        assert normalize_name_case("   ") == ""

    def test_single_letter_initial_preserved(self) -> None:
        """单字母首字母不应被压成小写。"""
        assert normalize_name_case("J") == "J"

    def test_parse_csl_name_normalizes_all_caps(self) -> None:
        """数据入口即归一：`"SMITH, J"` → family=Smith / given=J（逗号形式无歧义）。"""
        name = parse_csl_name("SMITH, J")

        assert name.family == "Smith"
        assert name.given == "J"

    def test_parse_csl_name_normalizes_plain_all_caps(self) -> None:
        """无逗号的 `"SMITH"` 按既有约定整体作为 family，且归一为首字母大写。"""
        assert parse_csl_name("SMITH").family == "Smith"

    def test_from_csl_json_normalizes_all_caps(self) -> None:
        """CSL-JSON 入口同样归一（覆盖上传/导入路径）。"""
        item = CslItem.from_csl_json(
            {
                "id": "src:1",
                "type": "article-journal",
                "title": "题名",
                "author": [{"family": "SMITH", "given": "JOHN"}],
            }
        )

        assert item.author[0].family == "Smith"
        assert item.author[0].given == "John"

    def test_rendered_entry_not_all_caps(self) -> None:
        """端到端：全大写输入渲染后不得仍是全大写。"""
        item = CslItem.from_csl_json(
            {
                "id": "src:1",
                "type": "article-journal",
                "title": "Ragas: Automated Evaluation",
                "author": [{"family": "ES", "given": "SHAHUL"}],
                "issued": {"date-parts": [[2023]]},
                "language": "en-US",
            }
        )

        entries = CitationRenderer().render([item], style_id="gbt7714-2025-numeric").entries

        assert "ES S" not in entries[0]
        assert "Es S" in entries[0], entries[0]


class TestCorpusStillRenders:
    """守卫：三项改动不得破坏既有 golden 快照。"""

    def test_numeric_golden_unchanged(self) -> None:
        payload = json.loads(CORPUS.read_text(encoding="utf-8"))
        items = [CslItem.from_csl_json(entry) for entry in payload["items"]]
        golden = (
            Path(__file__).parent / "fixtures" / "csl" / "golden" / "gb2025-numeric.expected.md"
        )

        entries = CitationRenderer().render(items, style_id="gbt7714-2025-numeric").entries

        assert entries == golden.read_text(encoding="utf-8").splitlines()

    def test_author_date_golden_unchanged(self) -> None:
        payload = json.loads(CORPUS.read_text(encoding="utf-8"))
        items = [CslItem.from_csl_json(entry) for entry in payload["items"]]
        golden = (
            Path(__file__).parent / "fixtures" / "csl" / "golden" / "gb2025-author-date.expected.md"
        )

        entries = CitationRenderer().render(items, style_id="gbt7714-2025-author-date").entries

        assert entries == golden.read_text(encoding="utf-8").splitlines()
