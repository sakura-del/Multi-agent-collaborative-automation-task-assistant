r"""M1-2 渲染核心测试：CSL-JSON 契约 + `CitationRenderer`（计划 §1.1 / §1.3 / §1.5）。

本文件的核心断言（Task 12 验收点）：
1. **双语**：同一批条目里中文条目出现「等」、英文条目出现 `et al.`（逐条 locale 生效）；
   并给出**对照组**：整批只用一种 locale 时术语退化（证明必须按 `item.language` 分组）。
2. **编号**：`numbering="citation-number"` 的样式，输出行 = 应用层编号 + 剥离引擎自带
   `^\[\d+\]\s*` 后的内容；条目顺序按 `ordering`（citation-order / author-date）。
3. **内联**：`substitute_inline` 把 `[[cite:ref_id]]` 换成 marker，未知 id 保留原样且有可观测 warnings。
4. **golden 快照**：冻结语料 + 产品样式（`app/tools/csl/styles/*.csl`）逐字断言。

铁律：
- **不产生任何真实网络请求**（citeproc-py 解析样式时 `no_network=True`，locale 走内置文件）；
- 冻结语料 `tests/fixtures/csl/corpus.json` 只读；
- 排序/编号/内联全部由应用层负责（ADR 0001 §4.2），引擎仅做逐条格式化。
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
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
from pydantic import ValidationError

from app.core.exceptions import ValidationException
from app.tools.csl.models import (
    DEFAULT_STYLE_ID,
    TYPE_IDENTIFIERS,
    CslItem,
    CslName,
    StyleMeta,
    StyleResolver,
    build_ref_id,
    csl_item_from_bibliography_entry,
    csl_item_from_literature_record,
    detect_language,
    doi_from_url,
    normalize_doi,
    parse_csl_name,
)
from app.tools.csl.renderer import (
    ENGINE_NUMBER_PATTERN,
    INLINE_PATTERN,
    CitationRenderer,
    RenderedBibliography,
    fallback_bibliography_entry,
    locale_for_language,
    sort_items,
)

FIXTURES = Path(__file__).parent / "fixtures" / "csl"
CORPUS_PATH = FIXTURES / "corpus.json"
GOLDEN_DIR = FIXTURES / "golden"
MINI_STYLES = GOLDEN_DIR / "styles"
BACKEND_ROOT = Path(__file__).resolve().parents[1]
PRODUCT_STYLES = BACKEND_ROOT / "app" / "tools" / "csl" / "styles"

MINI_NUMERIC = MINI_STYLES / "mini-numeric.csl"
MINI_AUTHOR_DATE = MINI_STYLES / "mini-author-date.csl"
MINI_BROKEN = MINI_STYLES / "mini-broken.csl"

#: 自研 2025 样式文件名由 lead 裁决为唯一名 ``{style_id}.csl``（见裁决 R-M1-1：
#: 早先任务书里的 ``gb-t-7714-2025-numeric.csl`` 作废），与 test_csl_styles.py 的注册表断言一致。
GB2025_FILENAME = "gbt7714-2025-numeric.csl"


# ===== 语料 / 样式装载 =====


def load_corpus_json() -> list[dict[str, Any]]:
    """读取冻结语料（CSL-JSON 形式，只读）。"""
    payload = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    return list(payload["items"])


def load_corpus_items() -> list[CslItem]:
    """冻结语料 → `CslItem` 列表。"""
    return [CslItem.from_csl_json(entry) for entry in load_corpus_json()]


def require_product_style(filename: str) -> Path:
    path = PRODUCT_STYLES / filename
    if not path.is_file():
        pytest.fail(f"产品样式资产缺失：{path}（task-11 交付物，golden 快照无法校验）")
    return path


def require_gb2025_style() -> Path:
    return require_product_style(GB2025_FILENAME)


def read_golden(filename: str) -> list[str]:
    path = GOLDEN_DIR / filename
    if not path.is_file():
        pytest.fail(f"golden 快照缺失：{path}（由本任务生成并逐字断言）")
    return path.read_text(encoding="utf-8").splitlines()


class FakeResolver:
    """`StyleResolver` 协议的最小测试替身（记录调用，未知 id 抛 ValidationException）。"""

    def __init__(
        self,
        metas: Mapping[str, StyleMeta],
        aliases: Mapping[str, str] | None = None,
    ) -> None:
        self._metas = dict(metas)
        self._aliases = dict(aliases or {})
        self.resolve_calls: list[str] = []
        self.meta_calls: list[str] = []

    def resolve(self, style_id: str) -> str:
        self.resolve_calls.append(style_id)
        resolved = self._aliases.get(style_id, style_id)
        if resolved not in self._metas:
            raise ValidationException(f"未知 citation_style：{style_id!r}")
        return resolved

    def meta(self, style_id: str) -> StyleMeta:
        self.meta_calls.append(style_id)
        try:
            return self._metas[style_id]
        except KeyError as exc:  # pragma: no cover - 由测试用法保证
            raise ValueError(f"未知 style_id：{style_id!r}") from exc


def make_meta(
    style_id: str,
    path: Path,
    *,
    numbering: str = "citation-number",
    ordering: str = "citation-order",
    default_locale: str = "en-US",
) -> StyleMeta:
    return StyleMeta(
        style_id=style_id,
        title=f"{style_id} (test fixture)",
        numbering=numbering,  # type: ignore[arg-type]
        ordering=ordering,  # type: ignore[arg-type]
        path=path,
        default_locale=default_locale,
    )


@pytest.fixture
def mini_resolver() -> FakeResolver:
    """mini 样式解析器：数字制 / 著者-出版年制 / 损坏样式 + 一个别名。"""
    return FakeResolver(
        metas={
            "mini-numeric": make_meta("mini-numeric", MINI_NUMERIC),
            "mini-author-date": make_meta(
                "mini-author-date", MINI_AUTHOR_DATE, numbering="none", ordering="author-date"
            ),
            "broken": make_meta("broken", MINI_BROKEN),
        },
        aliases={"gbt7714-mini": "mini-numeric"},
    )


# ===== 直接使用 citeproc-py 的取证助手（对照组 / 剥离规则证据）=====


def render_single_raw(style_path: Path, locale: str, item: Mapping[str, Any]) -> str:
    """用引擎原样渲染单条 bibliography 行（不剥离、不编号）。"""
    style = CitationStylesStyle(str(style_path), locale=locale, validate=False)
    source = CiteProcJSON([dict(item)])
    bibliography = CitationStylesBibliography(style, source, formatter.plain)
    citation = Citation([CitationItem(item["id"])])
    bibliography.register(citation)
    return "\n".join(str(line) for line in bibliography.bibliography())


def render_batch_raw(
    style_path: Path, locale: str, items: Sequence[Mapping[str, Any]]
) -> list[str]:
    """整批单一 locale 渲染（对照组：证明不分组会退化）。"""
    style = CitationStylesStyle(str(style_path), locale=locale, validate=False)
    source = CiteProcJSON([dict(item) for item in items])
    bibliography = CitationStylesBibliography(style, source, formatter.plain)
    citation = Citation([CitationItem(item["id"]) for item in items])
    bibliography.register(citation)
    return [str(line) for line in bibliography.bibliography()]


# ============================================================
# 1. CSL-JSON 契约（models.py）
# ============================================================


class TestTypeIdentifiers:
    def test_frozen_mapping(self) -> None:
        """§1.1 冻结的 CSL type → GB/T 7714 文献类型标识。"""
        assert TYPE_IDENTIFIERS == {
            "article-journal": "J",
            "book": "M",
            "chapter": "M",
            "paper-conference": "C",
            "thesis": "D",
            "report": "R",
            "dataset": "DS",
            "webpage": "EB",
            "manuscript": "PP",
            "standard": "S",
            "patent": "P",
        }

    def test_default_style_id(self) -> None:
        assert DEFAULT_STYLE_ID == "gbt7714-2025-numeric"


class TestCslModels:
    def test_csl_name_forbids_extra(self) -> None:
        with pytest.raises(ValidationError):
            CslName(family="Es", given="Shahul", bogus="x")  # type: ignore[call-arg]

    def test_csl_item_forbids_extra(self) -> None:
        with pytest.raises(ValidationError):
            CslItem(id="x", type="book", title="t", bogus=1)  # type: ignore[call-arg]

    def test_defaults_are_bilingual_safe(self) -> None:
        item = CslItem(id="x", type="book", title="t")
        assert item.language == "zh-CN"
        assert item.author == []
        assert item.issued_year is None

    def test_to_csl_json_uses_standard_keys_and_omits_none(self) -> None:
        item = CslItem.from_csl_json(load_corpus_json()[0])
        payload = item.to_csl_json()

        assert payload["id"] == "src:2309.15217v2"
        assert payload["type"] == "article-journal"
        assert payload["container-title"] == (
            "Proceedings of the 1st Workshop on Evaluation and Comparison of NLP Systems"
        )
        assert payload["DOI"] == "10.18653/v1/2023.evalnlp-1.16"
        assert payload["issued"] == {"date-parts": [[2023]]}
        assert payload["author"][0] == {"family": "Es", "given": "Shahul"}
        assert "publisher" not in payload  # None 字段不落 JSON
        assert "volume" in payload and payload["volume"] == "12"

    def test_to_csl_json_keeps_literal_names_for_chinese_authors(self) -> None:
        item = CslItem.from_csl_json(load_corpus_json()[1])
        payload = item.to_csl_json()
        assert payload["author"] == [
            {"literal": "牛耕田"},
            {"literal": "李泽"},
            {"literal": "张三"},
            {"literal": "王五"},
        ]

    def test_corpus_roundtrip_is_stable(self) -> None:
        for raw in load_corpus_json():
            first = CslItem.from_csl_json(raw)
            second = CslItem.from_csl_json(first.to_csl_json())
            assert first == second, raw["id"]
            assert first.to_csl_json() == second.to_csl_json()

    def test_from_csl_json_ignores_unknown_top_level_keys(self) -> None:
        raw = dict(load_corpus_json()[0], abstract="摘要", keyword=["a", "b"], note="x")
        item = CslItem.from_csl_json(raw)
        assert item.title.startswith("Ragas")

    def test_from_csl_json_requires_id_type_title(self) -> None:
        with pytest.raises(ValidationException):
            CslItem.from_csl_json({"type": "book", "title": "无 id"})
        with pytest.raises(ValidationException):
            CslItem.from_csl_json({"id": "x", "title": "无 type"})
        with pytest.raises(ValidationException):
            CslItem.from_csl_json({"id": "x", "type": "book"})

    def test_from_csl_json_rejects_non_mapping(self) -> None:
        with pytest.raises(ValidationException):
            CslItem.from_csl_json(["not", "a", "mapping"])  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        ("issued", "expected"),
        [
            ({"date-parts": [[2024, 3, 1]]}, 2024),
            ({"date-parts": [[2024]]}, 2024),
            ({"date-parts": [[2024], [2025]]}, 2024),
            ({"raw": "2024"}, 2024),
            ({"year": 2024}, 2024),
            ({"date-parts": [["2024"]]}, 2024),
            ({"date-parts": [[]]}, None),
            ("2024", 2024),
            ({}, None),
            (None, None),
        ],
    )
    def test_from_csl_json_issued_variants(self, issued: Any, expected: int | None) -> None:
        item = CslItem.from_csl_json({"id": "x", "type": "book", "title": "t", "issued": issued})
        assert item.issued_year == expected

    def test_from_csl_json_skips_empty_author_entries(self) -> None:
        item = CslItem.from_csl_json(
            {
                "id": "x",
                "type": "book",
                "title": "t",
                "author": [{"affiliation": "某大学"}, {"family": "Es", "given": "Shahul"}, {}],
            }
        )
        assert item.author == [CslName(family="Es", given="Shahul")]

    def test_from_csl_json_missing_language_uses_heuristic(self) -> None:
        zh = CslItem.from_csl_json({"id": "x", "type": "book", "title": "中文书名"})
        en = CslItem.from_csl_json({"id": "y", "type": "book", "title": "English Title"})
        assert zh.language == "zh-CN"
        assert en.language == "en-US"


class TestNameAndLanguage:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Shahul Es", CslName(family="Es", given="Shahul")),
            ("Zhang, Wei", CslName(family="Zhang", given="Wei")),
            ("  Zhang ,  Wei  ", CslName(family="Zhang", given="Wei")),
            ("Aristotle", CslName(family="Aristotle")),
            ("牛耕田", CslName(literal="牛耕田")),
            ("陈国强", CslName(literal="陈国强")),
            ("", CslName()),
        ],
    )
    def test_parse_csl_name(self, text: str, expected: CslName) -> None:
        assert parse_csl_name(text) == expected

    def test_detect_language_priority(self) -> None:
        # 显式 language 最高优先（即使题名/作者是中文）
        assert detect_language(title="中文题名", authors=["牛耕田"], explicit="en-US") == "en-US"
        assert detect_language(title="English", explicit="zh") == "zh-CN"
        assert detect_language(title="English", explicit="en") == "en-US"
        assert detect_language(title="English", explicit="ja-JP") == "ja-JP"
        assert detect_language(title="English", explicit="   ") == "en-US"
        # 其次中文作者名，再次题名 CJK，最后 en-US
        assert detect_language(title="English title", authors=["牛耕田"]) == "zh-CN"
        assert detect_language(title="中文题名", authors=["Shahul Es"]) == "zh-CN"
        assert detect_language(title="English title", authors=["Shahul Es"]) == "en-US"
        assert detect_language(title="") == "en-US"

    def test_normalize_doi(self) -> None:
        assert normalize_doi("https://doi.org/10.1234/ABC") == "10.1234/abc"
        assert normalize_doi("http://dx.doi.org/10.1/x") == "10.1/x"
        assert normalize_doi("doi:10.1/X") == "10.1/x"
        assert normalize_doi(" 10.1/x ") == "10.1/x"
        assert normalize_doi("") is None
        assert normalize_doi(None) is None

    def test_build_ref_id_priority(self) -> None:
        assert (
            build_ref_id(doi="https://doi.org/10.1/ABC", source_id="s1", title="T")
            == "doi:10.1/abc"
        )
        assert build_ref_id(source_id="2309.15217v2", title="T") == "src:2309.15217v2"
        title_id = build_ref_id(title="  A   B  ")
        assert re.fullmatch(r"title:[0-9a-f]{12}", title_id) is not None

    def test_build_ref_id_title_hash_is_stable_and_normalized(self) -> None:
        assert build_ref_id(title="Title") == build_ref_id(title="  title  ")

    def test_build_ref_id_requires_something(self) -> None:
        with pytest.raises(ValidationException):
            build_ref_id(doi=None, source_id=None, title="  ")


class TestBuilders:
    def test_from_literature_record_object(self) -> None:
        from app.tools.literature.models import LiteratureRecord

        record = LiteratureRecord(
            source_id="2309.15217v2",
            title="Ragas: Automated Evaluation of Retrieval Augmented Generation",
            authors=["Shahul Es", "Jithin James"],
            published_at=datetime(2023, 9, 26, tzinfo=UTC),
            source_url="https://arxiv.org/abs/2309.15217v2",
            pdf_url="https://arxiv.org/pdf/2309.15217v2",
        )
        item = csl_item_from_literature_record(record)

        assert item.id == "src:2309.15217v2"
        assert item.type == "manuscript"  # 预印本 → PP
        assert item.title.startswith("Ragas")
        assert item.author == [
            CslName(family="Es", given="Shahul"),
            CslName(family="James", given="Jithin"),
        ]
        assert item.issued_year == 2023
        assert item.url == "https://arxiv.org/abs/2309.15217v2"
        assert item.language == "en-US"

    def test_from_literature_record_chinese_and_ref_id_override(self) -> None:
        class Record:
            source_id = "zh-1"
            title = "面向学位论文的中文参考文献著录规则研究"
            authors = ("牛耕田", "李泽")
            published_at = datetime(2024, 3, 1, tzinfo=UTC)
            source_url = "https://example.org/zh-1"

        item = csl_item_from_literature_record(Record(), ref_id="custom:1")
        assert item.id == "custom:1"
        assert item.language == "zh-CN"
        assert item.author == [CslName(literal="牛耕田"), CslName(literal="李泽")]

    def test_from_literature_record_mapping_input(self) -> None:
        item = csl_item_from_literature_record(
            {
                "source_id": "s-1",
                "title": "A Survey",
                "authors": ["Smith, John"],
                "published_at": "2024-06-15T00:00:00Z",
                "source_url": "https://example.org/s-1",
                "doi": "10.1/ABC",
            }
        )
        assert item.id == "doi:10.1/abc"
        assert item.author == [CslName(family="Smith", given="John")]
        assert item.issued_year == 2024

    def test_from_literature_record_derives_doi_from_doi_org_url(self) -> None:
        item = csl_item_from_literature_record(
            {"source_id": "s-2", "title": "A Survey", "source_url": "https://doi.org/10.5/XY"}
        )
        assert item.doi == "10.5/xy"
        assert item.id == "doi:10.5/xy"

    def test_from_bibliography_entry_requires_title(self) -> None:
        assert csl_item_from_bibliography_entry({"index": 1, "source_url": "https://e.org"}) is None
        assert csl_item_from_bibliography_entry({"title": "   "}) is None

    def test_from_bibliography_entry_minimal_uses_title_hash(self) -> None:
        entry = {
            "index": 1,
            "title": "Ragas: Automated Evaluation",
            "source_url": "https://e.org/x",
        }
        item = csl_item_from_bibliography_entry(entry)
        assert item is not None
        assert item.id == build_ref_id(title="Ragas: Automated Evaluation")
        assert item.type == "article-journal"
        assert item.url == "https://e.org/x"
        assert item.language == "en-US"

    def test_from_bibliography_entry_prefers_doi_then_source_id(self) -> None:
        with_doi = csl_item_from_bibliography_entry(
            {"title": "T", "doi": "https://doi.org/10.2/AB", "source_id": "s1"}
        )
        with_src = csl_item_from_bibliography_entry({"title": "T", "source_id": "s1"})
        assert with_doi is not None and with_doi.id == "doi:10.2/ab"
        assert with_src is not None and with_src.id == "src:s1"

    def test_from_bibliography_entry_flat_fields(self) -> None:
        entry = {
            "index": 2,
            "title": "面向学位论文的中文参考文献著录规则研究",
            "source_url": "https://doi.org/10.1234/zh.journal.2024.001",
            "source_id": "zh.journal.2024.001",
            "authors": ["牛耕田", "李泽", "张三", "王五"],
            "year": 2024,
            "language": "zh-CN",
            "container_title": "中国科技期刊研究",
            "verification_status": "verified",
        }
        item = csl_item_from_bibliography_entry(entry)
        assert item is not None
        # source_url 是 doi.org 链接 → §1.5「DOI 优先」应命中（与核验器 _doi_from_url 同规则）
        assert item.id == "doi:10.1234/zh.journal.2024.001"
        assert item.doi == "10.1234/zh.journal.2024.001"
        assert item.language == "zh-CN"
        assert item.issued_year == 2024
        assert item.container_title == "中国科技期刊研究"
        assert item.author[0] == CslName(literal="牛耕田")

    def test_from_bibliography_entry_derives_doi_from_doi_org_url(self) -> None:
        """reviewer 条目只有 source_url（doi.org）时也要命中 DOI 优先规则。

        这是 lead 集成测试 `test_inline_placeholder_supports_doi_ref_id` 依赖的行为：
        `[[cite:doi:10.1234/zh.journal.2024.001]]` 必须能替换成功。
        """
        item = csl_item_from_bibliography_entry(
            {
                "title": "面向学位论文的中文参考文献著录规则研究",
                "source_url": "https://doi.org/10.1234/zh.journal.2024.001",
                "source_id": "zh.journal.2024.001",
            }
        )
        assert item is not None
        assert item.doi == "10.1234/zh.journal.2024.001"
        assert item.id == "doi:10.1234/zh.journal.2024.001"

    def test_from_bibliography_entry_does_not_invent_doi_from_plain_url(self) -> None:
        item = csl_item_from_bibliography_entry(
            {
                "title": "Ragas: Automated Evaluation",
                "source_url": "https://arxiv.org/abs/2309.15217v2",
                "source_id": "2309.15217v2",
            }
        )
        assert item is not None
        assert item.doi is None
        assert item.id == "src:2309.15217v2"

    def test_from_bibliography_entry_csl_json_derives_doi_from_url(self) -> None:
        item = csl_item_from_bibliography_entry(
            {
                "title": "T",
                "csl_json": {
                    "type": "article-journal",
                    "title": "T",
                    "URL": "https://doi.org/10.7/XY",
                },
            }
        )
        assert item is not None
        assert item.doi == "10.7/xy"
        assert item.id == "doi:10.7/xy"

    def test_doi_from_url_only_accepts_doi_org_links(self) -> None:
        assert doi_from_url("https://doi.org/10.1/Ab") == "10.1/ab"
        assert doi_from_url("http://dx.doi.org/10.1/Ab") == "10.1/ab"
        assert doi_from_url("https://arxiv.org/abs/2309.15217v2") is None
        assert doi_from_url("10.1/ab") is None
        assert doi_from_url("") is None
        assert doi_from_url(None) is None

    def test_from_bibliography_entry_honours_csl_json_override(self) -> None:
        entry = {
            "index": 1,
            "title": "被 csl_json 覆盖",
            "csl_json": {
                "id": "doi:10.1234/zh.journal.2024.001",
                "type": "article-journal",
                "title": "面向学位论文的中文参考文献著录规则研究",
                "author": [{"literal": "牛耕田"}, {"literal": "李泽"}, {"literal": "张三"}],
                "container-title": "中国科技期刊研究",
                "issued": {"date-parts": [[2024]]},
                "language": "zh-CN",
            },
        }
        item = csl_item_from_bibliography_entry(entry)
        assert item is not None
        assert item.id == "doi:10.1234/zh.journal.2024.001"
        assert item.title == "面向学位论文的中文参考文献著录规则研究"
        assert item.language == "zh-CN"
        assert item.container_title == "中国科技期刊研究"

    def test_from_bibliography_entry_explicit_ref_id_wins(self) -> None:
        item = csl_item_from_bibliography_entry({"title": "T", "ref_id": "src:custom"})
        assert item is not None and item.id == "src:custom"


class TestStyleProtocol:
    def test_style_meta_shape_and_defaults(self) -> None:
        meta = StyleMeta(
            style_id="apa",
            title="APA",
            numbering="none",
            ordering="author-date",
            path=MINI_AUTHOR_DATE,
        )
        assert meta.default_locale == "en-US"
        assert meta.is_default is False
        assert isinstance(meta.path, Path)

    def test_style_meta_forbids_extra_and_bad_literals(self) -> None:
        with pytest.raises(ValidationError):
            StyleMeta(
                style_id="apa",
                title="APA",
                numbering="none",
                ordering="author-date",
                path=MINI_AUTHOR_DATE,
                bogus=1,  # type: ignore[call-arg]
            )
        with pytest.raises(ValidationError):
            StyleMeta(
                style_id="apa",
                title="APA",
                numbering="weird",  # type: ignore[arg-type]
                ordering="author-date",
                path=MINI_AUTHOR_DATE,
            )

    def test_fake_resolver_satisfies_protocol(self, mini_resolver: FakeResolver) -> None:
        assert isinstance(mini_resolver, StyleResolver)
        assert mini_resolver.resolve("gbt7714-mini") == "mini-numeric"
        assert mini_resolver.meta("mini-numeric").path == MINI_NUMERIC


# ============================================================
# 2. 渲染核心（renderer.py）
# ============================================================


class TestPatterns:
    def test_inline_pattern_is_frozen(self) -> None:
        """冻结正则（计划 §1.3，经裁决 R-M1-2 放宽字符类）。"""
        assert INLINE_PATTERN.pattern == r"\[\[cite:([^\s\]]+)\]\]"
        assert INLINE_PATTERN.findall("a [[cite:src:2309.15217v2]] b") == ["src:2309.15217v2"]
        assert INLINE_PATTERN.findall("[[cite:bad id]]") == []

    def test_inline_pattern_supports_doi_ref_ids(self) -> None:
        """DOI 型 ref_id 必然含 `/`（§1.5：``doi:{归一化 DOI}``）——裁决 R-M1-2 已覆盖。"""
        assert INLINE_PATTERN.findall("a [[cite:doi:10.1/x]] b") == ["doi:10.1/x"]
        assert INLINE_PATTERN.findall("[[cite:doi:10.1234/zh.journal.2024.001]]") == [
            "doi:10.1234/zh.journal.2024.001"
        ]
        # DOI 允许的其它标点（+ # ? & =）也不应让占位失配
        assert INLINE_PATTERN.findall("[[cite:doi:10.1/a+b#c?d&e=f]]") == ["doi:10.1/a+b#c?d&e=f"]

    def test_engine_number_pattern(self) -> None:
        """裁决 R-M1-3①：既剥 ``[n] ``，也剥 nature 的裸 ``n.``/``n)``；仅行首、仅一次。"""
        assert ENGINE_NUMBER_PATTERN.pattern == r"^(?:\[\d+\]\s*|\d+[.)]\s*)"
        assert ENGINE_NUMBER_PATTERN.sub("", "[12] 正文") == "正文"
        assert ENGINE_NUMBER_PATTERN.sub("", "[3]正文") == "正文"
        assert ENGINE_NUMBER_PATTERN.sub("", "1. 正文") == "正文"
        assert ENGINE_NUMBER_PATTERN.sub("", "1.正文") == "正文"
        assert ENGINE_NUMBER_PATTERN.sub("", "2)正文") == "正文"
        assert ENGINE_NUMBER_PATTERN.sub("", "无编号") == "无编号"
        # 只在行首剥离：正文中间的编号不受影响
        assert ENGINE_NUMBER_PATTERN.sub("", "正文 [1] 尾注") == "正文 [1] 尾注"
        # 只剥一层：`[1] 1.` 剥成 `1.`（由编号断言保证不会出现这种形态）
        assert ENGINE_NUMBER_PATTERN.sub("", "[1] 1.Es", count=1) == "1.Es"

    def test_locale_for_language(self) -> None:
        assert locale_for_language("zh-CN") == "zh-CN"
        assert locale_for_language("zh") == "zh-CN"
        assert locale_for_language("zh_TW") == "zh-CN"
        assert locale_for_language("en-US") == "en-US"
        assert locale_for_language("en-GB") == "en-US"
        assert locale_for_language("ja-JP") == "en-US"
        assert locale_for_language("") == "en-US"
        assert locale_for_language(None) == "en-US"
        assert locale_for_language("ja-JP", default="zh-CN") == "zh-CN"


class TestOrdering:
    def test_citation_order_keeps_input_order(self) -> None:
        items = load_corpus_items()
        assert [item.id for item in sort_items(items, "citation-order")] == [
            item.id for item in items
        ]

    def test_author_date_sorts_by_first_author_then_year(self) -> None:
        ordered = sort_items(load_corpus_items(), "author-date")
        assert [item.id for item in ordered] == [
            "src:2309.15217v2",  # Es
            "src:arxiv-fair-rag-2025",  # Garcia
            "src:conf-orchestration-2024",  # Smith
            "doi:10.1234/zh.journal.2024.001",  # 牛耕田（Unicode 码位序，无拼音库）
            "src:thesis-evidence-tracing-2025",  # 赵敏
            "src:book-multi-agent-2022",  # 陈国强
        ]

    def test_unknown_ordering_rejected(self) -> None:
        with pytest.raises(ValueError):
            sort_items(load_corpus_items(), "no-such-ordering")  # type: ignore[arg-type]


class TestRenderedBibliographyShape:
    def test_empty_input_returns_empty_result(self, mini_resolver: FakeResolver) -> None:
        result = CitationRenderer(resolver=mini_resolver).render([], style_id="mini-numeric")
        assert result == RenderedBibliography(
            style_id="mini-numeric", entries=[], marker_by_item_id={}, item_order=[]
        )
        assert result.warnings == []

    def test_style_id_is_normalized_through_resolver(self, mini_resolver: FakeResolver) -> None:
        result = CitationRenderer(resolver=mini_resolver).render(
            load_corpus_items(), style_id="gbt7714-mini"
        )
        assert result.style_id == "mini-numeric"
        assert mini_resolver.resolve_calls == ["gbt7714-mini"]

    def test_unknown_style_raises_validation_error(self, mini_resolver: FakeResolver) -> None:
        with pytest.raises(ValidationException):
            CitationRenderer(resolver=mini_resolver).render(load_corpus_items(), style_id="nope")

    def test_result_is_frozen_but_warnings_are_appendable(self) -> None:
        result = RenderedBibliography(style_id="x", entries=[], marker_by_item_id={}, item_order=[])
        with pytest.raises(FrozenInstanceError):
            result.style_id = "y"  # type: ignore[misc]
        result.warnings.append("w")
        assert result.warnings == ["w"]
        assert (
            RenderedBibliography(
                style_id="x", entries=[], marker_by_item_id={}, item_order=[]
            ).warnings
            == []
        )


class TestNumberingAndOrdering:
    def test_engine_emits_own_number_for_every_single_item(self) -> None:
        """证据：单条渲染时引擎总会输出 `[1]`（因此必须剥离后前置应用层编号）。"""
        first = load_corpus_json()[0]
        raw = render_single_raw(MINI_NUMERIC, "en-US", first)
        assert raw.startswith("[1] ")
        assert "Ragas" in raw

    def test_numeric_entries_are_numbered_by_application(self, mini_resolver: FakeResolver) -> None:
        items = load_corpus_items()
        result = CitationRenderer(resolver=mini_resolver).render(items, style_id="mini-numeric")

        assert result.item_order == [item.id for item in items]
        assert len(result.entries) == len(items)
        for index, line in enumerate(result.entries, start=1):
            assert line.startswith(f"[{index}] ")
            # 引擎自带的 [1] 已被剥离：不会出现双重编号
            assert not line.startswith(f"[{index}] [{index}] ")
            assert ENGINE_NUMBER_PATTERN.match(line) is not None

    def test_author_date_entries_are_not_numbered(self, mini_resolver: FakeResolver) -> None:
        result = CitationRenderer(resolver=mini_resolver).render(
            load_corpus_items(), style_id="mini-author-date"
        )
        assert all(not line.startswith("[") for line in result.entries)

    def test_order_matches_ordering_declaration(self, mini_resolver: FakeResolver) -> None:
        result = CitationRenderer(resolver=mini_resolver).render(
            load_corpus_items(), style_id="mini-author-date"
        )
        assert result.item_order == [
            item.id for item in sort_items(load_corpus_items(), "author-date")
        ]
        assert len(result.entries) == len(result.item_order)


class TestBilingual:
    """Task 12 核心验收点：逐条 locale 生效 + 单 locale 对照组退化。"""

    def test_per_item_locale_produces_deng_and_et_al(self, mini_resolver: FakeResolver) -> None:
        result = CitationRenderer(resolver=mini_resolver).render(
            load_corpus_items(), style_id="mini-numeric"
        )
        lines = dict(zip(result.item_order, result.entries, strict=True))
        zh_line = lines["doi:10.1234/zh.journal.2024.001"]
        en_line = lines["src:2309.15217v2"]

        print("\n[双语原始输出][zh 条目]", zh_line)
        print("[双语原始输出][en 条目]", en_line)

        assert "等" in zh_line
        assert "et al." not in zh_line
        assert "et al." in en_line
        assert "等" not in en_line

    def test_author_date_markers_are_bilingual(self, mini_resolver: FakeResolver) -> None:
        result = CitationRenderer(resolver=mini_resolver).render(
            load_corpus_items(), style_id="mini-author-date"
        )
        assert result.marker_by_item_id["doi:10.1234/zh.journal.2024.001"] == "(牛耕田 等, 2024)"
        assert result.marker_by_item_id["src:2309.15217v2"] == "(Es et al., 2023)"
        print(
            "\n[双语原始输出][zh marker]",
            result.marker_by_item_id["doi:10.1234/zh.journal.2024.001"],
        )
        print("[双语原始输出][en marker]", result.marker_by_item_id["src:2309.15217v2"])

    def test_numeric_markers_are_application_numbers(self, mini_resolver: FakeResolver) -> None:
        result = CitationRenderer(resolver=mini_resolver).render(
            load_corpus_items(), style_id="mini-numeric"
        )
        assert [result.marker_by_item_id[item_id] for item_id in result.item_order] == [
            "[1]",
            "[2]",
            "[3]",
            "[4]",
            "[5]",
            "[6]",
        ]

    def test_single_locale_batch_degrades(self) -> None:
        """对照组：整批只用一种 locale 会退化，故必须按 `item.language` 分组。"""
        raw_items = load_corpus_json()
        zh_batch = render_batch_raw(MINI_NUMERIC, "zh-CN", raw_items)
        en_batch = render_batch_raw(MINI_NUMERIC, "en-US", raw_items)

        print("\n[对照组][整批 zh-CN] 英文条目行:", zh_batch[0])
        print("[对照组][整批 en-US] 中文条目行:", en_batch[1])

        # 英文条目在整批 zh-CN 下被写成「等」；中文条目在整批 en-US 下被写成 "et al."
        assert "等" in zh_batch[0] and "et al." not in zh_batch[0]
        assert "et al." in en_batch[1] and "等" not in en_batch[1]

        # 分组渲染（本任务实现）在同一批语料上同时拿到两种术语
        result = CitationRenderer(
            resolver=FakeResolver({"mini-numeric": make_meta("mini-numeric", MINI_NUMERIC)})
        ).render(load_corpus_items(), style_id="mini-numeric")
        joined = "\n".join(result.entries)
        assert "等" in joined and "et al." in joined

    def test_unknown_language_falls_back_to_style_default_locale(
        self, mini_resolver: FakeResolver
    ) -> None:
        item = CslItem(
            id="x",
            type="article-journal",
            title="A Paper With Four Authors",
            author=[
                CslName(family="A", given="A"),
                CslName(family="B", given="B"),
                CslName(family="C", given="C"),
                CslName(family="D", given="D"),
            ],
            language="ja-JP",
        )
        result = CitationRenderer(resolver=mini_resolver).render([item], style_id="mini-numeric")
        assert "et al." in result.entries[0]
        assert "等" not in result.entries[0]


class TestInlineSubstitution:
    def test_known_ids_are_replaced_and_evidence_markers_kept(
        self, mini_resolver: FakeResolver
    ) -> None:
        renderer = CitationRenderer(resolver=mini_resolver)
        result = renderer.render(load_corpus_items(), style_id="mini-numeric")
        text = "结论 [E1] [[cite:src:2309.15217v2]] 与 [E2] [[cite:doi:10.1234/zh.journal.2024.001]] 此处"

        substituted = renderer.substitute_inline(text, result)

        assert substituted == "结论 [E1] [1] 与 [E2] [2] 此处"
        assert "[E1]" in substituted and "[E2]" in substituted
        assert result.warnings == []

    def test_unknown_id_is_preserved_and_warned(self, mini_resolver: FakeResolver) -> None:
        renderer = CitationRenderer(resolver=mini_resolver)
        result = renderer.render(load_corpus_items(), style_id="mini-numeric")

        first = renderer.substitute_inline("x [[cite:missing-ref]] y", result)
        assert first == "x [[cite:missing-ref]] y"
        assert any("missing-ref" in warning for warning in result.warnings)
        # 语法合法但 id 未知 → 只报「未知引用 id」一条，不叠加「语法不符」告警
        assert len(result.warnings) == 1

        warned = list(result.warnings)
        renderer.substitute_inline("x [[cite:missing-ref]] y", result)
        assert list(result.warnings) == warned  # 同一问题不重复告警

    def test_malformed_placeholder_is_warned(self, mini_resolver: FakeResolver) -> None:
        renderer = CitationRenderer(resolver=mini_resolver)
        result = renderer.render(load_corpus_items(), style_id="mini-numeric")

        out = renderer.substitute_inline("x [[cite:bad id]] y", result)
        assert out == "x [[cite:bad id]] y"
        assert any("[[cite:" in warning for warning in result.warnings)
        # 未闭合占位同样可观测
        renderer.substitute_inline("x [[cite:unclosed y", result)
        assert any("[[cite:" in warning for warning in result.warnings)

    def test_text_without_placeholders_is_identity(self, mini_resolver: FakeResolver) -> None:
        renderer = CitationRenderer(resolver=mini_resolver)
        result = renderer.render(load_corpus_items(), style_id="mini-numeric")
        text = "纯文本，含 [E3] 与 [1] 但不含占位。"
        assert renderer.substitute_inline(text, result) == text


class TestFallback:
    def test_broken_style_falls_back_with_warning(self, mini_resolver: FakeResolver) -> None:
        items = load_corpus_items()
        result = CitationRenderer(resolver=mini_resolver).render(items, style_id="broken")

        assert len(result.entries) == len(items)
        assert all(line.strip() for line in result.entries)
        assert any("回退内置极简格式化器" in warning for warning in result.warnings)
        assert result.marker_by_item_id["src:2309.15217v2"] == "[1]"

    def test_fallback_formatter_is_readable(self) -> None:
        item = load_corpus_items()[0]
        line = fallback_bibliography_entry(item)
        assert "Ragas" in line
        assert "[J]" in line  # 类型标识仍然给出
        assert "2023" in line

    def test_default_resolver_is_lazy_style_registry(self) -> None:
        pytest.importorskip("app.tools.csl.styles", reason="task-13 的 styles.py 尚未交付")
        renderer = CitationRenderer()
        assert renderer.resolver.resolve(DEFAULT_STYLE_ID) == DEFAULT_STYLE_ID

    def test_default_resolver_renders_default_style_end_to_end(self) -> None:
        """默认路径（不注入 resolver）走 task-13 的 StyleRegistry + 产品样式。"""
        pytest.importorskip("app.tools.csl.styles", reason="task-13 的 styles.py 尚未交付")
        items = load_corpus_items()
        result = CitationRenderer().render(items)

        assert result.style_id == DEFAULT_STYLE_ID
        assert result.item_order == [item.id for item in items]
        assert len(result.entries) == len(items)
        joined = "\n".join(result.entries)
        assert "等" in joined and "et al." in joined  # 逐条 locale 端到端生效


# ============================================================
# 3. golden 快照（冻结语料 + 产品样式，逐字断言）
# ============================================================


def golden_entries(
    style_id: str,
    filename: str,
    *,
    numbering: str,
    ordering: str,
    default_locale: str = "en-US",
) -> list[str]:
    meta = StyleMeta(
        style_id=style_id,
        title=f"{style_id} (product asset)",
        numbering=numbering,  # type: ignore[arg-type]
        ordering=ordering,  # type: ignore[arg-type]
        path=require_product_style(filename),
        default_locale=default_locale,
    )
    resolver = FakeResolver({style_id: meta})
    return (
        CitationRenderer(resolver=resolver).render(load_corpus_items(), style_id=style_id).entries
    )


class TestGoldenSnapshots:
    def test_gb2025_numeric_golden(self) -> None:
        entries = golden_entries(
            DEFAULT_STYLE_ID,
            GB2025_FILENAME,
            numbering="citation-number",
            ordering="citation-order",
            default_locale="zh-CN",  # 裁决 R-M1-1：GB/T 样式实际 default-locale 是 zh-CN
        )
        assert entries == read_golden("gb2025-numeric.expected.md")

    def test_apa_golden(self) -> None:
        entries = golden_entries("apa", "apa.csl", numbering="none", ordering="author-date")
        assert entries == read_golden("apa.expected.md")

    def test_ieee_golden(self) -> None:
        entries = golden_entries(
            "ieee", "ieee.csl", numbering="citation-number", ordering="citation-order"
        )
        assert entries == read_golden("ieee.expected.md")

    def test_gb2025_golden_is_bilingual(self) -> None:
        """真样式上的双语验收：中文条目「等」、英文条目 `et al.` 同时存在。"""
        lines = read_golden("gb2025-numeric.expected.md")
        zh_line = next(line for line in lines if "中国科技期刊研究" in line)
        en_line = next(line for line in lines if "Ragas" in line)
        print("\n[golden 双语][zh]", zh_line)
        print("[golden 双语][en]", en_line)
        assert "等" in zh_line and "et al." not in zh_line
        assert "et al." in en_line and "等" not in en_line

    def test_gb2025_golden_has_type_identifiers(self) -> None:
        """必备的文献类型标识必须出现；载体标识（``/OL``）按 URL/DOI 追加（R-M1-3②）。"""
        joined = "\n".join(read_golden("gb2025-numeric.expected.md"))
        for identifier in ("J", "M", "C", "D", "PP"):
            assert re.search(
                rf"\[{identifier}(?:/[A-Z]+)?\]", joined
            ), f"golden 缺少类型标识 [{identifier}]"

    def test_gb2025_golden_is_numbered(self) -> None:
        lines = read_golden("gb2025-numeric.expected.md")
        for index, line in enumerate(lines, start=1):
            assert line.startswith(f"[{index}] ")

    def test_nature_golden(self) -> None:
        entries = golden_entries(
            "nature", "nature.csl", numbering="citation-number", ordering="citation-order"
        )
        assert entries == read_golden("nature.expected.md")

    def test_nature_entries_are_not_double_numbered(self) -> None:
        """R-M1-3①：nature 的引擎编号是裸 ``1.``，剥离后不得出现 ``[1] 1.`` 双重编号。"""
        lines = read_golden("nature.expected.md")
        for index, line in enumerate(lines, start=1):
            assert line.startswith(f"[{index}] ")
            assert not re.match(rf"^\[{index}\]\s*{index}[.)]", line)
            assert "1." not in line.split(" ", 1)[0]
