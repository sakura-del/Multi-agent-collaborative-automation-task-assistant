"""样式资产校验测试（Issue #4 M1-1 / 计划 §2 M1-1）。

覆盖：
- 样式资产齐全：自研 GB/T 7714—2025 顺序编码制 + 4 个 vendored 官方样式 + 署名文件；
- 每个样式都能被 `citeproc-py` 加载（`validate=False`，zh-CN / en-US 两种 locale）；
- **自研样式零 CSL-M 命中**（计划 §1.2 的 5 条冻结正则，命中即失败）；
- 自研样式声明标准 CSL 1.0.2 所需的结构与许可信息；
- vendored 样式**原样保留 `<info>` 块**（含 `<author>` / `<rights>`），且 `ATTRIBUTION.md`
  记录的 URL 与 sha256 与实际文件一致（资产可追溯）；
- 冻结语料 `tests/fixtures/csl/corpus.json` 的 6 条，按「逐条 locale 切换」逐条渲染
  不抛异常、输出非空，并体现 2025 版两条关键变化：
  ① 英文作者**仅首字母大写**（不是 2015 的全大写）；② 新增类型标识（PP 预印本等）；
  ③ 中文条目「等」/ 英文条目 "et al." 由 locale 切换产生（样式侧术语走 locale）。

无真实网络：所有样式与语料均来自仓库内文件。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest
from citeproc import (
    Citation,
    CitationItem,
    CitationStylesBibliography,
    CitationStylesStyle,
    formatter,
)
from citeproc.source.json import CiteProcJSON

BACKEND_DIR = Path(__file__).resolve().parent.parent
STYLES_DIR = BACKEND_DIR / "app" / "tools" / "csl" / "styles"
CORPUS_PATH = BACKEND_DIR / "tests" / "fixtures" / "csl" / "corpus.json"
ATTRIBUTION_PATH = STYLES_DIR / "ATTRIBUTION.md"

#: 自研样式（文件名 = `{style_id}.csl`，见 lead 裁决 R-M1-1）
SELF_AUTHORED_STYLE = "gbt7714-2025-numeric.csl"

#: vendored 官方样式（原样保留下载内容，含 `<info>` 块）
VENDORED_STYLES = (
    "gbt7714-2015-numeric.csl",
    "apa.csl",
    "ieee.csl",
    "nature.csl",
)

ALL_STYLES = (SELF_AUTHORED_STYLE, *VENDORED_STYLES)

#: vendored 样式的上游 URL（`ATTRIBUTION.md` 必须逐条记录）
VENDORED_SOURCE_URLS = {
    "gbt7714-2015-numeric.csl": (
        "https://github.com/citation-style-language/styles/blob/master/"
        "china-national-standard-gb-t-7714-2015-numeric.csl"
    ),
    "apa.csl": "https://github.com/citation-style-language/styles/blob/master/apa.csl",
    "ieee.csl": "https://github.com/citation-style-language/styles/blob/master/ieee.csl",
    "nature.csl": "https://github.com/citation-style-language/styles/blob/master/nature.csl",
}

#: 计划 §1.2 / ADR §2.1 冻结的 CSL-M 构造扫描规则
#: （与 `app.tools.csl.styles.CSL_M_PATTERNS` 内容一致；此处独立声明，保证本测试不依赖实现模块）
CSL_M_PATTERNS: tuple[tuple[str, str], ...] = (
    ("layout-locale", r"<layout[^>]*\blocale\s*="),
    ("institution", r"<institution\b"),
    ("has-day", r"has-day\s*="),
    ("citation-range-delimiter", r"citation-range-delimiter"),
    ("term-percent-s", r"<term[^>]*>[^<]*%s[^<]*</term>"),
)

CSL_NS = "http://purl.org/net/xbiblio/csl"


def _load_corpus() -> list[dict[str, Any]]:
    """读取冻结语料（不得修改，只读）。"""
    data = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    items = data["items"]
    assert isinstance(items, list)
    return items


def _render(item: dict[str, Any], *, style_file: str, locale: str) -> str:
    """按「逐条 locale 切换」渲染单条文献（ADR 0001 §2.2 的做法）。"""
    style = CitationStylesStyle(str(STYLES_DIR / style_file), locale=locale, validate=False)
    source = CiteProcJSON([item])
    bibliography = CitationStylesBibliography(style, source, formatter.plain)
    bibliography.register(Citation([CitationItem(item["id"])]))
    entries = bibliography.bibliography()
    return "".join(str(entry) for entry in entries)


def _render_corpus_item(index: int) -> str:
    """渲染语料第 index 条，locale 取该条目的 language（逐条切换）。"""
    item = _load_corpus()[index]
    return _render(item, style_file=SELF_AUTHORED_STYLE, locale=item["language"])


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _attribution_text() -> str:
    return ATTRIBUTION_PATH.read_text(encoding="utf-8")


class TestStyleAssetsPresent:
    def test_styles_directory_exists(self) -> None:
        assert STYLES_DIR.is_dir(), f"缺少样式目录：{STYLES_DIR}"

    @pytest.mark.parametrize("style_file", ALL_STYLES)
    def test_style_file_exists_and_non_empty(self, style_file: str) -> None:
        path = STYLES_DIR / style_file
        assert path.is_file(), f"缺少样式文件：{path}"
        assert path.stat().st_size > 0

    def test_attribution_file_exists(self) -> None:
        assert ATTRIBUTION_PATH.is_file(), f"缺少署名文件：{ATTRIBUTION_PATH}"

    def test_corpus_fixture_exists(self) -> None:
        assert CORPUS_PATH.is_file()
        assert len(_load_corpus()) == 6


class TestStylesAreLoadable:
    @pytest.mark.parametrize("style_file", ALL_STYLES)
    @pytest.mark.parametrize("locale", ["zh-CN", "en-US"])
    def test_citeproc_can_load_style(self, style_file: str, locale: str) -> None:
        style = CitationStylesStyle(str(STYLES_DIR / style_file), locale=locale, validate=False)
        assert style.has_bibliography()


class TestSelfAuthoredStyleStandardCompliance:
    def test_zero_csl_m_hits(self) -> None:
        """自研样式必须是标准 CSL 1.0.2：5 条 CSL-M 正则全部 0 命中。"""
        text = (STYLES_DIR / SELF_AUTHORED_STYLE).read_text(encoding="utf-8")
        hits = [
            f"{name}（正则 {pattern}）"
            for name, pattern in CSL_M_PATTERNS
            if re.search(pattern, text)
        ]
        assert hits == [], "自研样式命中 CSL-M 构造：" + "; ".join(hits)

    def test_declares_csl_1_0_x_and_class(self) -> None:
        text = (STYLES_DIR / SELF_AUTHORED_STYLE).read_text(encoding="utf-8")
        assert 'xmlns="http://purl.org/net/xbiblio/csl"' in text
        assert re.search(r'\bversion="1\.0(?:\.\d+)?"', text)
        assert re.search(r'\bclass="in-text"', text)

    def test_default_locale_is_zh_cn(self) -> None:
        """lead 裁决 R-M1-1：GB/T 系列根元素声明 default-locale="zh-CN"。"""
        text = (STYLES_DIR / SELF_AUTHORED_STYLE).read_text(encoding="utf-8")
        assert 'default-locale="zh-CN"' in text

    def test_english_surnames_are_not_uppercased(self) -> None:
        """2025 变化①：英文著者仅首字母大写 → 样式不得再对 family 施加 uppercase。"""
        text = (STYLES_DIR / SELF_AUTHORED_STYLE).read_text(encoding="utf-8")
        assert 'text-case="uppercase"' not in text

    def test_info_block_has_license_and_reference_notice(self) -> None:
        """头部须写明参考来源与许可（CC BY-SA 3.0），见 task-11 第 1 条。"""
        text = (STYLES_DIR / SELF_AUTHORED_STYLE).read_text(encoding="utf-8")
        assert "<info>" in text
        assert "CC BY-SA 3.0" in text or "creativecommons.org/licenses/by-sa/3.0" in text
        assert "citation-style-language/styles" in text
        assert "zotero-chinese/styles" in text

    def test_has_bibliography_and_numeric_citation(self) -> None:
        text = (STYLES_DIR / SELF_AUTHORED_STYLE).read_text(encoding="utf-8")
        assert "<bibliography" in text
        assert "<citation" in text
        assert 'variable="citation-number"' in text

    def test_required_2025_type_identifiers_are_expressible(self) -> None:
        """2025 版新增/保留的文献类型标识（附录 A.1）必须在样式里可产出。"""
        text = (STYLES_DIR / SELF_AUTHORED_STYLE).read_text(encoding="utf-8")
        for identifier in ("PP", "DS", "EB", "G", "J", "M", "C", "D", "R", "S", "P", "N"):
            assert f'value="{identifier}"' in text, f"缺少文献类型标识 {identifier}"

    def test_carrier_identifier_supports_ol_and_medium(self) -> None:
        """2025 新增载体标识 MM（缩微资料）与既有 OL/MT/DK/CD 一样走 medium 字段。"""
        text = (STYLES_DIR / SELF_AUTHORED_STYLE).read_text(encoding="utf-8")
        assert 'variable="medium"' in text
        assert 'value="OL"' in text

    def test_passes_csl_schema_validation(self) -> None:
        """用 citeproc-py 自带的 official CSL RELAX NG schema 校验（validate=True）。"""
        style = CitationStylesStyle(
            str(STYLES_DIR / SELF_AUTHORED_STYLE), locale="zh-CN", validate=True
        )
        assert style.has_bibliography()


class TestVendoredStylesPreserved:
    @pytest.mark.parametrize("style_file", VENDORED_STYLES)
    def test_info_block_and_author_are_preserved(self, style_file: str) -> None:
        text = (STYLES_DIR / style_file).read_text(encoding="utf-8")
        assert "<info>" in text
        assert "<title>" in text
        assert "<author>" in text
        assert "by-sa/3.0" in text

    @pytest.mark.parametrize("style_file", VENDORED_STYLES)
    def test_no_local_modification_beyond_info(self, style_file: str) -> None:
        """vendored 文件必须原样保留：含 `<?xml` 声明与 CSL 命名空间。"""
        text = (STYLES_DIR / style_file).read_text(encoding="utf-8")
        assert text.startswith("<?xml")
        assert 'xmlns="http://purl.org/net/xbiblio/csl"' in text


class TestAttribution:
    def test_mentions_citationstyles_org(self) -> None:
        assert "citationstyles.org" in _attribution_text()

    def test_declares_cc_by_sa_3_0(self) -> None:
        text = _attribution_text()
        assert "CC BY-SA 3.0" in text
        assert "creativecommons.org/licenses/by-sa/3.0" in text

    def test_self_authored_style_is_declared_independent(self) -> None:
        text = _attribution_text()
        assert SELF_AUTHORED_STYLE in text
        assert "独立实现" in text

    @pytest.mark.parametrize("style_file", VENDORED_STYLES)
    def test_records_source_url(self, style_file: str) -> None:
        text = _attribution_text()
        assert style_file in text
        assert VENDORED_SOURCE_URLS[style_file] in text

    @pytest.mark.parametrize("style_file", VENDORED_STYLES)
    def test_records_actual_sha256(self, style_file: str) -> None:
        text = _attribution_text()
        digest = _sha256(STYLES_DIR / style_file)
        assert digest in text, f"{style_file} 的 sha256 {digest} 未记录在 ATTRIBUTION.md 中"


class TestCorpusRenderingSelfAuthored:
    @pytest.mark.parametrize("index", range(6))
    def test_item_renders_non_empty(self, index: int) -> None:
        output = _render_corpus_item(index)
        assert output.strip(), f"语料第 {index} 条渲染为空"

    def test_english_authors_are_capitalize_first_only(self) -> None:
        """2025 版变化①：英文作者仅首字母大写（2015 版是全大写）。"""
        output = _render_corpus_item(0)  # Ragas, 4 位英文作者
        assert "Es S" in output or "Es, S" in output, output
        assert "ES S" not in output, output
        assert "SMITH J" not in output, output

    def test_english_et_al_term_comes_from_locale(self) -> None:
        """2025 版变化③（英文侧）：4 位作者截断为 "et al."。"""
        output = _render_corpus_item(0)
        assert "et al." in output, output

    def test_chinese_et_al_term_comes_from_locale(self) -> None:
        """2025 版变化③（中文侧）：4 位作者截断为「等」。"""
        output = _render_corpus_item(1)
        assert "等" in output, output
        assert "et al." not in output, output

    @pytest.mark.parametrize(
        ("index", "identifier"),
        [
            (0, "[J"),  # 期刊（英文）
            (1, "[J"),  # 期刊（中文）
            (2, "[M"),  # 图书
            (3, "[C"),  # 会议录
            (4, "[D"),  # 学位论文
            (5, "[PP"),  # 预印本（2025 新增）
        ],
    )
    def test_type_identifier_is_rendered(self, index: int, identifier: str) -> None:
        output = _render_corpus_item(index)
        assert identifier in output, output

    @pytest.mark.parametrize("index", range(6))
    def test_every_corpus_item_has_a_type_identifier(self, index: int) -> None:
        """lead 裁决 R-M1-3②：文献类型标识恒输出，与是否有 URL/DOI 无关。"""
        output = _render_corpus_item(index)
        assert re.search(r"\[[A-Z]{1,2}(?:/[A-Z]{2})?\]", output), output

    def test_carrier_identifier_is_ol_for_online_items(self) -> None:
        """有 URL/DOI 的条目输出 [文献类型标识/OL]（2025 版标识为必备项）。"""
        output = _render_corpus_item(0)
        assert "[J/OL]" in output, output

    def test_carrier_identifier_is_mm_when_medium_says_so(self) -> None:
        """2025 新增载体标识 MM（缩微资料）：条目 medium 字段直接决定 [J/MM]。"""
        item = dict(_load_corpus()[0])
        item["medium"] = "MM"
        output = _render(item, style_file=SELF_AUTHORED_STYLE, locale=item["language"])
        assert "[J/MM]" in output, output

    def test_chinese_entry_keeps_chinese_text(self) -> None:
        output = _render_corpus_item(1)
        assert "面向学位论文的中文参考文献著录规则研究" in output
        assert "中国科技期刊研究" in output

    def test_same_item_differs_between_locales(self) -> None:
        """逐条 locale 切换的前提：同一份样式在不同 locale 下术语不同。"""
        item = _load_corpus()[0]
        zh = _render(item, style_file=SELF_AUTHORED_STYLE, locale="zh-CN")
        en = _render(item, style_file=SELF_AUTHORED_STYLE, locale="en-US")
        assert zh != en, "逐条 locale 切换未生效：zh-CN 与 en-US 输出完全相同"
        assert "等" in zh, zh
        assert "et al." in en, en


class TestCorpusRenderingVendoredStyles:
    @pytest.mark.parametrize("style_file", VENDORED_STYLES)
    @pytest.mark.parametrize("index", range(6))
    def test_vendored_style_renders_corpus_item(self, style_file: str, index: int) -> None:
        item = _load_corpus()[index]
        output = _render(item, style_file=style_file, locale=item["language"])
        assert output.strip(), f"{style_file} 渲染语料第 {index} 条为空"
