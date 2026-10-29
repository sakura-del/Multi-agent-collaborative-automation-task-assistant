"""Zotero 条目 → BibTeX 纯转换测试（计划 Task 4）。

脚本位于仓库根 ``scripts/zotero_export.py``（不在 ``backend`` 包内），
因此用 importlib 按绝对路径加载；产物必须能被现有
:func:`app.tools.csl.importers.parse_bibtex` 直接解析。
"""

import importlib.util
from pathlib import Path

from app.tools.csl.importers import parse_bibtex

ROOT = Path(__file__).parents[2]
spec = importlib.util.spec_from_file_location("zotero_export", ROOT / "scripts/zotero_export.py")
zotero_export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(zotero_export)


def test_items_to_bibtex_preserves_metadata_and_escapes() -> None:
    items = [
        {
            "key": "ABC123",
            "data": {
                "itemType": "journalArticle",
                "title": "Evidence {RAG} 100%",
                "creators": [{"firstName": "Wei", "lastName": "Zhang"}],
                "date": "2024-01-02",
                "DOI": "10.1234/ABC",
                "url": "https://example.org/paper",
            },
        }
    ]

    text = zotero_export.items_to_bibtex(items)

    assert text.startswith("@article{doi:10.1234/abc,")
    assert "title={Evidence \\{RAG\\} 100\\%}" in text
    assert "author={Zhang, Wei}" in text
    assert "year={2024}" in text
    assert "doi={10.1234/abc}" in text
    assert "url={https://example.org/paper}" in text
    assert parse_bibtex(text)[0].title == "Evidence {RAG} 100%"


def test_unknown_type_does_not_fabricate_year() -> None:
    items = [{"key": "X1", "data": {"itemType": "unknownType", "title": "No year"}}]

    text = zotero_export.items_to_bibtex(items)

    assert text.startswith("@misc{zotero:X1,")
    assert "1970" not in text


def test_export_is_parseable_by_existing_importer() -> None:
    items = [
        {
            "key": "ABC123",
            "data": {
                "itemType": "journalArticle",
                "title": "Evidence RAG",
                "creators": [{"firstName": "Wei", "lastName": "Zhang"}],
                "date": "2024-01-02",
                "DOI": "10.1234/ABC",
            },
        }
    ]

    text = zotero_export.items_to_bibtex(items)
    parsed = parse_bibtex(text)

    assert len(parsed) == 1
    assert parsed[0].doi == "10.1234/abc"
    assert parsed[0].issued_year == 2024
    assert parsed[0].title == "Evidence RAG"
