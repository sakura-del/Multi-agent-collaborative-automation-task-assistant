"""样式注册表与准入校验测试（Issue #4 M1 §1.2 / ADR 0001 §4.1）。

覆盖：
- 预置 5 样式、默认样式、`gbt7714` 别名归一、numbering/ordering 声明；
- 未知 style_id 的行为差异：`get()`/`resolve()` → ValidationException，`meta()` → ValueError；
- `resolve("")` → 默认样式；`meta()` 对目录内自定义样式按文件推断；
- `validate_csl_file()`：合法标准 CSL 1.0.2 通过（含 numbering/ordering 推断与 title 提取）；
- 5 类 CSL-M 构造逐个被**显式拒绝**，reasons 含构造名 + 行号；
- 坏 XML / 缺 version / 版本不受支持 / 缺 info+title / 缺 bibliography|citation /
  根元素错误 / 文件不存在 → StyleRejected；
- `StyleRejected` 是 `ValidationException` 子类且携带逐条 reasons（绝不静默降级）。

无真实网络。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.exceptions import ValidationException
from app.tools.csl import styles as styles_module
from app.tools.csl.models import DEFAULT_STYLE_ID, StyleMeta
from app.tools.csl.styles import CSL_M_PATTERNS, StyleRegistry, StyleRejected

EXPECTED_STYLE_IDS = [
    "gbt7714-2025-numeric",
    "gbt7714-2015-numeric",
    "apa",
    "ieee",
    "nature",
]

EXPECTED_NUMBERING: dict[str, tuple[str, str]] = {
    "gbt7714-2025-numeric": ("citation-number", "citation-order"),
    "gbt7714-2015-numeric": ("citation-number", "citation-order"),
    "apa": ("none", "author-date"),
    "ieee": ("citation-number", "citation-order"),
    "nature": ("citation-number", "citation-order"),
}

INFO_BLOCK = (
    "  <info>\n"
    "    <title>自研测试样式</title>\n"
    "    <id>https://example.org/styles/test</id>\n"
    "  </info>\n"
)
AUTHOR_DATE_CITATION = (
    "  <citation>\n"
    "    <layout>\n"
    '      <text variable="title"/>\n'
    "    </layout>\n"
    "  </citation>\n"
)
NUMERIC_CITATION = (
    '  <citation collapse="citation-number">\n'
    "    <layout>\n"
    '      <text variable="citation-number" prefix="[" suffix="]"/>\n'
    "    </layout>\n"
    "  </citation>\n"
)
BIBLIOGRAPHY_BLOCK = (
    "  <bibliography>\n"
    "    <layout>\n"
    '      <text variable="title"/>\n'
    "    </layout>\n"
    "  </bibliography>\n"
)

CSL_M_CASES: list[tuple[str, str]] = [
    ("layout-locale", '<layout locale="en">\n      <text variable="title"/>\n    </layout>'),
    ("institution", "<institution/>"),
    ("has-day", '<date variable="issued" has-day="true"/>'),
    ("citation-range-delimiter", '<layout citation-range-delimiter="—"/>'),
    ("term-percent-s", "<term>%s</term>"),
]


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def _style_document(
    body: str,
    *,
    version_attr: str = ' version="1.0"',
    root: str = "style",
    root_attrs: str = "",
) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<{root} xmlns="http://purl.org/net/xbiblio/csl"{version_attr}{root_attrs}>\n'
        f"{body}"
        f"</{root}>\n"
    )


def _valid_style(
    *,
    numeric: bool = False,
    title: str = "自研测试样式",
    default_locale: str | None = None,
) -> str:
    citation = NUMERIC_CITATION if numeric else AUTHOR_DATE_CITATION
    info = (
        "  <info>\n"
        f"    <title>{title}</title>\n"
        "    <id>https://example.org/styles/test</id>\n"
        "  </info>\n"
    )
    root_attrs = f' default-locale="{default_locale}"' if default_locale else ""
    return _style_document(info + citation + BIBLIOGRAPHY_BLOCK, root_attrs=root_attrs)


def _style_with_construct(construct_xml: str) -> str:
    """构造固定出现在第 6 行，便于断言「构造名 + 行号」。"""
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<style xmlns="http://purl.org/net/xbiblio/csl" version="1.0">\n'
        "  <info>\n"
        "    <title>含 CSL-M 构造</title>\n"
        "  </info>\n"
        f"  {construct_xml}\n"
        "  <bibliography>\n"
        "    <layout>\n"
        '      <text variable="title"/>\n'
        "    </layout>\n"
        "  </bibliography>\n"
        "</style>\n"
    )


class TestPresets:
    def test_default_style_id(self) -> None:
        assert DEFAULT_STYLE_ID == "gbt7714-2025-numeric"
        assert styles_module.DEFAULT_STYLE_ID == DEFAULT_STYLE_ID

    def test_list_styles_returns_five_presets_in_order(self) -> None:
        metas = StyleRegistry().list_styles()
        assert [meta.style_id for meta in metas] == EXPECTED_STYLE_IDS
        assert all(isinstance(meta, StyleMeta) for meta in metas)
        assert metas[0].style_id == DEFAULT_STYLE_ID
        assert metas[0].is_default is True
        assert all(meta.is_default is False for meta in metas[1:])

    def test_preset_metas_point_at_style_files(self) -> None:
        registry = StyleRegistry()
        metas = {meta.style_id: meta for meta in registry.list_styles()}
        for style_id in EXPECTED_STYLE_IDS:
            path = metas[style_id].path
            assert path.name == f"{style_id}.csl"
            assert path.parent == registry.styles_dir

    def test_presets_declare_numbering_and_ordering(self) -> None:
        metas = {meta.style_id: meta for meta in StyleRegistry().list_styles()}
        for style_id, (numbering, ordering) in EXPECTED_NUMBERING.items():
            assert metas[style_id].numbering == numbering, style_id
            assert metas[style_id].ordering == ordering, style_id

    def test_presets_have_human_readable_titles(self) -> None:
        for meta in StyleRegistry().list_styles():
            assert meta.title.strip()
            assert meta.title != meta.style_id

    def test_presets_default_locale_comes_from_style_file(self, tmp_path: Path) -> None:
        """`default_locale` 必须按样式文件声明读取，不得硬编码为统一值（R-M1-1）。"""
        declared = {
            "gbt7714-2025-numeric": "zh-CN",
            "gbt7714-2015-numeric": "zh-CN",
            "apa": "zh-CN",  # 故意与 en-US 默认回退不同，证明值来自文件声明
            "ieee": "en-US",
            "nature": "en-US",
        }
        for style_id, locale in declared.items():
            _write(tmp_path, f"{style_id}.csl", _valid_style(default_locale=locale))

        registry = StyleRegistry(styles_dir=tmp_path)
        metas = {meta.style_id: meta for meta in registry.list_styles()}
        assert {style_id: metas[style_id].default_locale for style_id in declared} == declared

    def test_presets_default_locale_fallback_without_assets(self, tmp_path: Path) -> None:
        """资产缺失时按样式族回退：GB/T 7714 系列 zh-CN、Nature en-GB、其余 en-US。"""
        metas = {meta.style_id: meta for meta in StyleRegistry(styles_dir=tmp_path).list_styles()}
        assert metas["gbt7714-2025-numeric"].default_locale == "zh-CN"
        assert metas["gbt7714-2015-numeric"].default_locale == "zh-CN"
        assert metas["apa"].default_locale == "en-US"
        assert metas["ieee"].default_locale == "en-US"
        assert metas["nature"].default_locale == "en-GB"


class TestAliasAndLookup:
    def test_resolve_alias_gbt7714(self) -> None:
        registry = StyleRegistry()
        assert registry.resolve("gbt7714") == DEFAULT_STYLE_ID
        assert registry.meta("gbt7714").style_id == DEFAULT_STYLE_ID

    def test_resolve_known_id_passthrough(self) -> None:
        registry = StyleRegistry()
        for style_id in EXPECTED_STYLE_IDS:
            assert registry.resolve(style_id) == style_id

    def test_resolve_strips_whitespace(self) -> None:
        assert StyleRegistry().resolve("  apa  ") == "apa"

    def test_resolve_empty_returns_default(self) -> None:
        assert StyleRegistry().resolve("") == DEFAULT_STYLE_ID

    def test_resolve_unknown_raises_validation_error(self) -> None:
        with pytest.raises(ValidationException):
            StyleRegistry().resolve("no-such-style")

    def test_get_unknown_id_raises_validation_error(self, tmp_path: Path) -> None:
        with pytest.raises(ValidationException):
            StyleRegistry(styles_dir=tmp_path).get("no-such-style")

    def test_meta_unknown_raises_value_error(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="no-such-style"):
            StyleRegistry(styles_dir=tmp_path).meta("no-such-style")

    def test_get_resolves_alias_to_default_style_file(self, tmp_path: Path) -> None:
        for style_id in EXPECTED_STYLE_IDS:
            _write(tmp_path, f"{style_id}.csl", _valid_style())
        registry = StyleRegistry(styles_dir=tmp_path)
        path = registry.get("gbt7714")
        assert path.name == "gbt7714-2025-numeric.csl"
        assert path.parent == tmp_path
        assert registry.get("apa").name == "apa.csl"

    def test_get_missing_style_file_raises_validation_error(self, tmp_path: Path) -> None:
        # 预置样式元数据是静态表，但资产缺失必须显式报错（不返回不存在的路径）
        with pytest.raises(ValidationException, match="apa"):
            StyleRegistry(styles_dir=tmp_path).get("apa")

    def test_meta_works_without_any_asset_file(self, tmp_path: Path) -> None:
        meta = StyleRegistry(styles_dir=tmp_path).meta("apa")
        assert meta.style_id == "apa"
        assert meta.numbering == "none"
        assert meta.path == tmp_path / "apa.csl"

    def test_get_returns_custom_style_file_in_styles_dir(self, tmp_path: Path) -> None:
        custom = _write(tmp_path, "custom-lab.csl", _valid_style(title="实验室自定义样式"))
        registry = StyleRegistry(styles_dir=tmp_path)
        assert registry.get("custom-lab") == custom
        assert registry.resolve("custom-lab") == "custom-lab"

    def test_meta_infers_custom_style_from_file(self, tmp_path: Path) -> None:
        _write(tmp_path, "custom-lab.csl", _valid_style(numeric=True, title="自定义数字制"))
        meta = StyleRegistry(styles_dir=tmp_path).meta("custom-lab")
        assert meta.style_id == "custom-lab"
        assert meta.title == "自定义数字制"
        assert meta.numbering == "citation-number"
        assert meta.ordering == "citation-order"
        assert meta.path == tmp_path / "custom-lab.csl"


class TestValidateCslFileStructure:
    def test_valid_author_date_style_passes(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "my-style.csl", _valid_style())
        meta = StyleRegistry(styles_dir=tmp_path).validate_csl_file(path)
        assert isinstance(meta, StyleMeta)
        assert meta.style_id == "my-style"
        assert meta.title == "自研测试样式"
        assert meta.numbering == "none"
        assert meta.ordering == "author-date"
        assert meta.path == path

    def test_valid_numeric_style_infers_citation_number(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "my-numeric.csl", _valid_style(numeric=True))
        meta = StyleRegistry(styles_dir=tmp_path).validate_csl_file(path)
        assert meta.numbering == "citation-number"
        assert meta.ordering == "citation-order"

    def test_default_locale_is_read_from_root_attribute(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            "zh.csl",
            _style_document(
                INFO_BLOCK + AUTHOR_DATE_CITATION + BIBLIOGRAPHY_BLOCK,
                root_attrs=' default-locale="zh-CN"',
            ),
        )
        meta = StyleRegistry(styles_dir=tmp_path).validate_csl_file(path)
        assert meta.default_locale == "zh-CN"

    def test_default_locale_falls_back_to_en_us(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "nolocale.csl", _valid_style())
        meta = StyleRegistry(styles_dir=tmp_path).validate_csl_file(path)
        assert meta.default_locale == "en-US"

    def test_missing_version_is_rejected(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "noversion.csl", _style_document(INFO_BLOCK, version_attr=""))
        with pytest.raises(StyleRejected) as excinfo:
            StyleRegistry(styles_dir=tmp_path).validate_csl_file(path)
        assert any("version" in reason for reason in excinfo.value.reasons)

    def test_unsupported_version_is_rejected(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path, "v2.csl", _style_document(INFO_BLOCK, version_attr=' version="2.0"')
        )
        with pytest.raises(StyleRejected) as excinfo:
            StyleRegistry(styles_dir=tmp_path).validate_csl_file(path)
        assert any("2.0" in reason for reason in excinfo.value.reasons)

    def test_missing_info_is_rejected(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path, "noinfo.csl", _style_document(AUTHOR_DATE_CITATION + BIBLIOGRAPHY_BLOCK)
        )
        with pytest.raises(StyleRejected) as excinfo:
            StyleRegistry(styles_dir=tmp_path).validate_csl_file(path)
        assert any("info" in reason or "title" in reason for reason in excinfo.value.reasons)

    def test_empty_title_is_rejected(self, tmp_path: Path) -> None:
        body = (
            "  <info>\n"
            "    <title>   </title>\n"
            "  </info>\n" + AUTHOR_DATE_CITATION + BIBLIOGRAPHY_BLOCK
        )
        path = _write(tmp_path, "emptytitle.csl", _style_document(body))
        with pytest.raises(StyleRejected) as excinfo:
            StyleRegistry(styles_dir=tmp_path).validate_csl_file(path)
        assert any("title" in reason for reason in excinfo.value.reasons)

    def test_missing_bibliography_and_citation_is_rejected(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "nolayout.csl", _style_document(INFO_BLOCK))
        with pytest.raises(StyleRejected) as excinfo:
            StyleRegistry(styles_dir=tmp_path).validate_csl_file(path)
        assert any(
            "bibliography" in reason or "citation" in reason for reason in excinfo.value.reasons
        )

    def test_wrong_root_element_is_rejected(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            "notstyle.csl",
            _style_document(INFO_BLOCK + BIBLIOGRAPHY_BLOCK, root="notstyle"),
        )
        with pytest.raises(StyleRejected) as excinfo:
            StyleRegistry(styles_dir=tmp_path).validate_csl_file(path)
        assert any("style" in reason for reason in excinfo.value.reasons)

    def test_invalid_xml_is_rejected(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "broken.csl", "<style><info><title>缺闭合\n")
        with pytest.raises(StyleRejected) as excinfo:
            StyleRegistry(styles_dir=tmp_path).validate_csl_file(path)
        assert any("XML" in reason for reason in excinfo.value.reasons)

    def test_missing_file_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(StyleRejected) as excinfo:
            StyleRegistry(styles_dir=tmp_path).validate_csl_file(tmp_path / "ghost.csl")
        assert excinfo.value.reasons


class TestCslMRejection:
    def test_patterns_constant_matches_plan(self) -> None:
        assert [name for name, _ in CSL_M_PATTERNS] == [
            "layout-locale",
            "institution",
            "has-day",
            "citation-range-delimiter",
            "term-percent-s",
        ]
        assert all(pattern for _, pattern in CSL_M_PATTERNS)

    @pytest.mark.parametrize(("construct_name", "construct_xml"), CSL_M_CASES)
    def test_construct_is_rejected_with_name_and_line(
        self, tmp_path: Path, construct_name: str, construct_xml: str
    ) -> None:
        path = _write(tmp_path, f"{construct_name}.csl", _style_with_construct(construct_xml))
        with pytest.raises(StyleRejected) as excinfo:
            StyleRegistry(styles_dir=tmp_path).validate_csl_file(path)

        reasons = excinfo.value.reasons
        assert reasons, "拒绝必须给出 reasons"
        assert any(construct_name in reason for reason in reasons), reasons
        assert any(":6:" in reason for reason in reasons), reasons

    def test_all_hits_are_listed(self, tmp_path: Path) -> None:
        body = (
            "  <info>\n"
            "    <title>多个 CSL-M 构造</title>\n"
            "  </info>\n"
            "  <institution/>\n"
            '  <date variable="issued" has-day="true"/>\n' + BIBLIOGRAPHY_BLOCK
        )
        path = _write(tmp_path, "multi-cslm.csl", _style_document(body))
        with pytest.raises(StyleRejected) as excinfo:
            StyleRegistry(styles_dir=tmp_path).validate_csl_file(path)
        reasons = excinfo.value.reasons
        assert any("institution" in reason for reason in reasons), reasons
        assert any("has-day" in reason for reason in reasons), reasons

    def test_valid_standard_style_is_not_rejected(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "clean.csl", _valid_style())
        meta = StyleRegistry(styles_dir=tmp_path).validate_csl_file(path)
        assert meta.style_id == "clean"

    def test_commented_out_csl_m_constructs_are_ignored(self, tmp_path: Path) -> None:
        """注释里的 CSL-M 片段不是生效构造，不得据此拒绝（vendored 2015 GB/T 样式即如此）。"""
        body = (
            "  <info>\n"
            "    <title>注释中含 CSL-M 引用</title>\n"
            "  </info>\n"
            '  <!-- 取消注释可开启 CSL-M 多语言：<layout locale="en">'
            '<text variable="title"/></layout> -->\n'
            "  <!-- <institution/> -->\n"
            '  <!-- <date variable="issued" has-day="true"/> -->\n'
            "  <!-- <term>%s</term> -->\n"
            '  <!-- <layout citation-range-delimiter="—"/> -->\n' + BIBLIOGRAPHY_BLOCK
        )
        path = _write(tmp_path, "commented.csl", _style_document(body))
        meta = StyleRegistry(styles_dir=tmp_path).validate_csl_file(path)
        assert meta.style_id == "commented"

    def test_line_numbers_stay_accurate_after_comments(self, tmp_path: Path) -> None:
        body = (
            "  <info>\n"
            "    <title>注释之后仍有真实构造</title>\n"
            "  </info>\n"
            "  <!-- <institution/> -->\n"
            "  <institution/>\n" + BIBLIOGRAPHY_BLOCK
        )
        path = _write(tmp_path, "after-comment.csl", _style_document(body))
        with pytest.raises(StyleRejected) as excinfo:
            StyleRegistry(styles_dir=tmp_path).validate_csl_file(path)
        reasons = excinfo.value.reasons
        assert any("institution" in reason and ":7:" in reason for reason in reasons), reasons
        assert all(":6:" not in reason for reason in reasons), reasons


class TestStyleRejectedContract:
    def test_is_validation_exception(self) -> None:
        assert issubclass(StyleRejected, ValidationException)
        with pytest.raises(ValidationException):
            raise StyleRejected(["x"])

    def test_reasons_are_preserved_in_order(self) -> None:
        rejected = StyleRejected(["first:1: a", "second:2: b"])
        assert rejected.reasons == ["first:1: a", "second:2: b"]
        assert "first:1: a" in str(rejected)

    def test_reasons_property_returns_copy(self) -> None:
        rejected = StyleRejected(["only"])
        rejected.reasons.append("mutated")
        assert rejected.reasons == ["only"]

    def test_empty_reasons_still_yields_message(self) -> None:
        rejected = StyleRejected([])
        assert rejected.reasons
        assert str(rejected)
