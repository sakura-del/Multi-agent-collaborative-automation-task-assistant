"""DOI 归一与合并去重（2026-10-26 引用库遗留计划 §3.4）。

问题：幂等键是 `ref_id`。同一文献写成三种形式会产生三个 ref_id → 存三条：

    doi:10.1234/A
    doi:https://doi.org/10.1234/a
    doi:DOI: 10.1234/a

用户从不同来源（Zotero / Google Scholar / 期刊页）复制同一条文献是常态。
"""

from __future__ import annotations

import pytest

from app.tools.csl.models import CslItem, CslName


def _item(
    *,
    ref_id: str = "doi:10.18653/v1/2024.eacl-demo.16",
    doi: str | None = "10.18653/v1/2024.eacl-demo.16",
    title: str = "Ragas: Automated Evaluation of Retrieval Augmented Generation",
    year: int = 2024,
    url: str | None = None,
) -> CslItem:
    return CslItem(
        id=ref_id,
        type="paper-conference",
        title=title,
        author=[CslName(family="Es", given="Shahul")],
        issued_year=year,
        doi=doi,
        url=url,
    )


class TestNormalizeDoi:
    def test_lowercases(self) -> None:
        from app.knowledge.services.reference_library_service import normalize_doi

        assert normalize_doi("10.18653/V1/2024.EACL-DEMO.16") == "10.18653/v1/2024.eacl-demo.16"

    def test_strips_https_doi_org_prefix(self) -> None:
        from app.knowledge.services.reference_library_service import normalize_doi

        assert (
            normalize_doi("https://doi.org/10.18653/v1/2024.eacl-demo.16")
            == "10.18653/v1/2024.eacl-demo.16"
        )

    def test_strips_http_dx_doi_org_prefix(self) -> None:
        from app.knowledge.services.reference_library_service import normalize_doi

        assert (
            normalize_doi("http://dx.doi.org/10.18653/v1/2024.eacl-demo.16")
            == "10.18653/v1/2024.eacl-demo.16"
        )

    def test_strips_doi_colon_prefix(self) -> None:
        from app.knowledge.services.reference_library_service import normalize_doi

        assert normalize_doi("doi:10.18653/v1/2024.eacl-demo.16") == "10.18653/v1/2024.eacl-demo.16"
        assert (
            normalize_doi("DOI: 10.18653/v1/2024.eacl-demo.16") == "10.18653/v1/2024.eacl-demo.16"
        )

    def test_strips_whitespace(self) -> None:
        from app.knowledge.services.reference_library_service import normalize_doi

        assert normalize_doi("  10.18653/v1/2024.eacl-demo.16  ") == "10.18653/v1/2024.eacl-demo.16"

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_returns_none_for_empty(self, value: str | None) -> None:
        from app.knowledge.services.reference_library_service import normalize_doi

        assert normalize_doi(value) is None

    def test_all_three_forms_collapse_to_one(self) -> None:
        """三种写法必须归一到同一个值 —— 这是合并的前提。"""
        from app.knowledge.services.reference_library_service import normalize_doi

        forms = [
            "10.18653/v1/2024.eacl-demo.16",
            "https://doi.org/10.18653/V1/2024.EACL-DEMO.16",
            "DOI: 10.18653/v1/2024.eacl-demo.16",
        ]
        assert len({normalize_doi(f) for f in forms}) == 1


class TestFindDuplicate:
    """按归一化 DOI 判重；title 派生 id 不参与（会误合并）。"""

    def test_matches_same_doi_different_case(self) -> None:
        from app.knowledge.services.reference_library_service import same_work

        assert same_work(_item(doi="10.18653/V1/2024.EACL-DEMO.16"), _item()) is True

    def test_matches_doi_with_url_prefix(self) -> None:
        from app.knowledge.services.reference_library_service import same_work

        assert (
            same_work(_item(doi="https://doi.org/10.18653/v1/2024.eacl-demo.16"), _item()) is True
        )

    def test_different_doi_is_not_duplicate(self) -> None:
        from app.knowledge.services.reference_library_service import same_work

        assert same_work(_item(doi="10.1234/other"), _item()) is False

    def test_no_doi_on_either_side_is_not_duplicate(self) -> None:
        """没有 DOI 就不判重 —— 只靠题名会误合并（同名不同文很常见）。"""
        from app.knowledge.services.reference_library_service import same_work

        left = _item(ref_id="title:same-title", doi=None, title="A Survey")
        right = _item(ref_id="title:same-title-2", doi=None, title="A Survey")
        assert same_work(left, right) is False

    def test_one_side_missing_doi_is_not_duplicate(self) -> None:
        from app.knowledge.services.reference_library_service import same_work

        left = _item(ref_id="title:x", doi=None)
        assert same_work(left, _item()) is False
