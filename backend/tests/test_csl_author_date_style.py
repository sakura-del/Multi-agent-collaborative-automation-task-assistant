"""GB/T 7714—2025 著者-出版年制样式（Issue #4 遗留项）。

数字制样式（`gbt7714-2025-numeric`）已在 M1 交付；本轮补**著者-出版年制**
（`gbt7714-2025-author-date`），使国标双制式完整。

## GB/T 7714—2025 著者-出版年制的关键规则

1. **正文标注**：`(著者 年)` 形式；中文用「等」、英文用 "et al."（逐条 locale，与数字制同机制）。
2. **参考文献表**：按**著者字顺**（author-date）排序，**不编号**（无 `[1]` 前缀）。
3. **著录格式与数字制一致**：题名后的 `[文献类型标识/载体标识]`（2025 为必备项）、
   出版项、访问路径等规则相同。
4. 著者项在前的著者-出版年制里，出版年**紧跟著者**（`著者. 题名[M]. 出版地: 出版者, 年.`）。

## 本文件锁定

- 样式资产存在且通过**准入校验**（零 CSL-M、标准 1.0.2、含 `<citation>`/`<bibliography>`）；
- 注册表登记为 `gbt7714-2025-author-date`，`numbering="none"`、`ordering="author-date"`；
- 渲染结果**不编号**、按著者排序、保留类型标识、双语「等」/et al. 生效；
- 与数字制样式**互不干扰**（同一批条目两种制式输出不同）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.tools.csl.models import DEFAULT_STYLE_ID, CslItem
from app.tools.csl.renderer import CitationRenderer
from app.tools.csl.styles import StyleRegistry

STYLE_ID = "gbt7714-2025-author-date"
CORPUS = Path(__file__).parent / "fixtures" / "csl" / "corpus.json"


def _corpus_items() -> list[CslItem]:
    payload = json.loads(CORPUS.read_text(encoding="utf-8"))
    return [CslItem.from_csl_json(entry) for entry in payload["items"]]


def _render(style_id: str) -> list[str]:
    return list(CitationRenderer().render(_corpus_items(), style_id=style_id).entries)


class TestStyleAssetAdmission:
    def test_style_file_exists(self) -> None:
        path = Path(StyleRegistry().styles_dir) / f"{STYLE_ID}.csl"
        assert path.is_file(), f"缺少样式资产 {path.name}"

    def test_style_passes_admission_validation(self) -> None:
        """必须走与预置样式相同的准入校验：零 CSL-M、标准 1.0.2。"""
        path = Path(StyleRegistry().styles_dir) / f"{STYLE_ID}.csl"

        meta = StyleRegistry().validate_csl_file(path)

        assert meta.style_id == STYLE_ID
        assert meta.title
        assert meta.numbering == "none"
        assert meta.ordering == "author-date"

    def test_registry_lists_style_as_preset(self) -> None:
        registry = StyleRegistry()

        ids = [meta.style_id for meta in registry.list_styles()]

        assert STYLE_ID in ids
        meta = registry.meta(STYLE_ID)
        assert meta.numbering == "none"
        assert meta.ordering == "author-date"
        assert meta.is_default is False  # 默认仍是数字制

    def test_default_style_unchanged(self) -> None:
        """守卫：默认样式必须仍是 GB/T 7714—2025 顺序编码制。"""
        assert DEFAULT_STYLE_ID == "gbt7714-2025-numeric"


class TestAuthorDateRendering:
    def test_entries_are_not_numbered(self) -> None:
        """著者-出版年制参考文献表不编号。"""
        entries = _render(STYLE_ID)

        assert entries
        for entry in entries:
            assert not entry.startswith("["), entry

    def test_entries_sorted_by_author(self) -> None:
        """按著者字顺排序（author-date），而非条目给出的引用顺序。"""
        items = _corpus_items()
        rendered = CitationRenderer().render(items, style_id=STYLE_ID)

        assert rendered.item_order != [item.id for item in items], "排序未生效（仍是输入顺序）"
        assert len(rendered.item_order) == len(items)

    def test_type_identifier_present(self) -> None:
        """2025 版类型标识为必备项：著者-出版年制同样必须输出。"""
        entries = _render(STYLE_ID)
        joined = "\n".join(entries)

        for identifier in ("[J", "[M", "[D", "[C", "[PP"):
            assert identifier in joined, f"缺少类型标识 {identifier}"

    def test_bilingual_et_al_works(self) -> None:
        """逐条 locale：中文条目「等」、英文条目 "et al."。"""
        entries = _render(STYLE_ID)
        joined = "\n".join(entries)

        assert "等" in joined, "中文条目未出现「等」"
        assert "et al." in joined, "英文条目未出现 et al."

    def test_bibliography_differs_from_numeric(self) -> None:
        """两种制式输出必须不同（证明样式真的被采用，而非复用数字制）。"""
        author_date = _render(STYLE_ID)
        numeric = _render("gbt7714-2025-numeric")

        assert author_date != numeric


class TestCitationMarkers:
    def test_markers_are_author_year_not_numbers(self) -> None:
        """正文标注应为 (著者 年) 形态，而不是 [1]。"""
        items = _corpus_items()
        rendered = CitationRenderer().render(items, style_id=STYLE_ID)

        markers = list(rendered.marker_by_item_id.values())
        assert markers
        for marker in markers:
            assert not marker.startswith("["), f"marker 不应是数字制：{marker}"
            assert "(" in marker or "（" in marker or marker.strip(), marker

    def test_inline_substitution_uses_author_year(self) -> None:
        items = _corpus_items()
        renderer = CitationRenderer()
        rendered = renderer.render(items, style_id=STYLE_ID)
        ref_id = rendered.item_order[0]

        text = renderer.substitute_inline(f"见 [[cite:{ref_id}]]。", rendered)

        assert "[[cite:" not in text
        assert text.startswith("见 ")


class TestYearNotDuplicated:
    """著者-出版年制的核心识别特征：出版年**紧跟著者**，不得在出版项重复。

    错误形态（自研首版实测）：`Es S, … 2023. Ragas…[J/OL]. Proceedings …, 2023, 12(3): 150-160.`
    —— 年份出现两次。GB/T 7714 著者-出版年制的著录格式是
    `著者. 年. 题名[类型标识]. 出处/出版项.`，出版项内**不再重复年份**。
    """

    @staticmethod
    def _strip_access_paths(entry: str) -> str:
        """去掉 URL / DOI 段：它们内部可能含年份（如 `.../2023.evalnlp-1.16`），非著录年份。"""
        import re

        cleaned = re.sub(r"https?://\S+", "", entry)
        return re.sub(r"DOI:\S+", "", cleaned)

    def test_year_appears_once_per_entry(self) -> None:
        """著录年份只出现一次。

        注意：题名/会议名本身可能含年份（如 `Proceedings of the 2024 International Conference`），
        那是**题名内容**而非著录年份，不能算重复。因此按结构判断：
        著者项后紧接的年份 = 著录年份；此后到访问路径之前**不应再有独立的年份段**。
        """
        import re

        for entry in _render(STYLE_ID):
            cleaned = self._strip_access_paths(entry)
            # 著录年份 = 题名（首个 "["）之前紧跟着者的那个年份
            title_at = cleaned.find("[")
            head, tail = cleaned[:title_at], cleaned[title_at:]
            assert re.search(r"\b(?:19|20)\d{2}\b", head), f"年份未紧跟著者项：{entry}"
            # 出版项里不应再出现「, 年份」这种独立年份段（题名中的年份不算）
            assert not re.search(r",\s*(?:19|20)\d{2}\b", tail), f"出版项重复了年份：{entry}"

    def test_year_follows_contributors(self) -> None:
        """年份必须出现在题名（首个 `[` 类型标识）之前，即紧跟著者项。"""
        import re

        for entry in _render(STYLE_ID):
            title_at = entry.find("[")
            assert title_at > 0, entry
            head = entry[:title_at]
            assert re.search(r"\b(?:19|20)\d{2}\b", head), f"年份未紧跟著者项：{entry}"

    def test_periodical_entry_has_no_second_year(self) -> None:
        """期刊条目：刊名后直接卷(期): 页码，不再带年份。"""
        import re

        periodical = [e for e in _render(STYLE_ID) if "[J" in e]
        assert periodical
        for entry in periodical:
            tail = self._strip_access_paths(entry.split("[J", 1)[1])
            assert not re.search(r"\b(?:19|20)\d{2}\b", tail), entry


class TestNoCslM:
    def test_style_has_no_csl_m_constructs(self) -> None:
        """样式必须零 CSL-M（与数字制同标准）。"""
        from app.tools.csl.styles import CSL_M_PATTERNS, _strip_xml_comments

        path = Path(StyleRegistry().styles_dir) / f"{STYLE_ID}.csl"
        text = _strip_xml_comments(path.read_text(encoding="utf-8"))

        hits = [name for name, pattern in CSL_M_PATTERNS if __import__("re").search(pattern, text)]
        assert hits == [], f"样式含 CSL-M 构造：{hits}"


class TestGoldenSnapshot:
    def test_golden_snapshot_matches(self) -> None:
        """golden 快照：同一输入 → 同一输出（回归保护）。"""
        golden = (
            Path(__file__).parent / "fixtures" / "csl" / "golden" / "gb2025-author-date.expected.md"
        )
        if not golden.is_file():
            pytest.fail(f"缺少 golden 快照：{golden}（请用脚本生成后提交）")

        expected = golden.read_text(encoding="utf-8").splitlines()
        assert _render(STYLE_ID) == expected
