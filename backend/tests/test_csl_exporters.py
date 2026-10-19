"""CSL-JSON → BibTeX / RIS 导出测试（Issue #4 M1 §1.4）。

覆盖：
- 6 条冻结语料（`tests/fixtures/csl/corpus.json`）全部可导出，type 映射正确；
- BibTeX 特殊字符转义（`& % $ # _ { } ~ ^ \\`）与中文 UTF-8 原样输出；
- citation key 规则（首作者姓或机构 literal + 年份 + 序号）与边界：
  无作者、无年份、重名（同作者同年）、姓含连字符、literal 含空格；
- RIS 标签行（TY/AU/TI/JO|T2/PY/VL/IS/SP/EP/PB/CY/DO/UR/ER）与 page 拆分；
- 空列表返回空串；字段级回读（输出 → 解析 → 与源字段比对）。

纯字符串生成、无真实网络。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from app.tools.csl.exporters import to_bibtex, to_ris
from app.tools.csl.models import CslItem, CslName

CORPUS_PATH = Path(__file__).parent / "fixtures" / "csl" / "corpus.json"
CORPUS: list[dict[str, Any]] = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))["items"]


def _item_from_corpus(entry: dict[str, Any]) -> CslItem:
    """冻结语料（CSL-JSON）→ `CslItem`，走 `models.CslItem.from_csl_json` 正式路径。"""
    return CslItem.from_csl_json(entry)


CORPUS_ITEMS: list[CslItem] = [_item_from_corpus(entry) for entry in CORPUS]


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


_BIBTEX_ENTRY_RE = re.compile(r"@(?P<type>\w+)\{(?P<key>[^,]+),(?P<body>.*?)\n\}", re.DOTALL)
_BIBTEX_FIELD_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z]+)\s*=\s*\{(?P<value>.*?)\}(?P<comma>,?)$", re.MULTILINE
)


def _bibtex_entries(text: str) -> dict[str, dict[str, str]]:
    """极简 BibTeX 回读：citation key → {字段: 值, "__type__": 条目类型}。"""
    entries: dict[str, dict[str, str]] = {}
    for match in _BIBTEX_ENTRY_RE.finditer(text):
        fields: dict[str, str] = {"__type__": match.group("type")}
        for field in _BIBTEX_FIELD_RE.finditer(match.group("body")):
            fields[field.group("name")] = field.group("value")
        entries[match.group("key")] = fields
    return entries


_RIS_LINE_RE = re.compile(r"^(?P<tag>[A-Z][A-Z0-9])  - ?(?P<value>.*)$")


def _ris_records(text: str) -> list[dict[str, list[str]]]:
    """极简 RIS 回读：按 `TY` 起始分组，返回 标签 → 值列表（`ER  -` 无值也计入）。"""
    records: list[dict[str, list[str]]] = []
    current: dict[str, list[str]] | None = None
    for line in text.splitlines():
        match = _RIS_LINE_RE.match(line)
        if match is None:
            continue
        tag = match.group("tag")
        if tag == "TY":
            current = {}
            records.append(current)
        if current is None:
            continue
        current.setdefault(tag, []).append(match.group("value").strip())
    return records


class TestBibtexFrozenCorpus:
    def test_all_six_items_export_with_expected_types(self) -> None:
        text = to_bibtex(CORPUS_ITEMS)
        entries = _bibtex_entries(text)
        assert len(entries) == 6
        types = sorted(fields["__type__"] for fields in entries.values())
        assert types == [
            "article",
            "article",
            "book",
            "inproceedings",
            "misc",
            "phdthesis",
        ]

    def test_article_fields_are_mapped(self) -> None:
        entries = _bibtex_entries(to_bibtex(CORPUS_ITEMS))
        entry = next(f for f in entries.values() if "Ragas" in f["title"])
        assert entry["__type__"] == "article"
        assert entry["author"] == (
            "Es, Shahul and James, Jithin and Espinosa-Anke, Luis and Schockaert, Steven"
        )
        assert entry["journal"] == (
            "Proceedings of the 1st Workshop on Evaluation and Comparison of NLP Systems"
        )
        assert entry["year"] == "2023"
        assert entry["volume"] == "12"
        assert entry["number"] == "3"
        assert entry["pages"] == "150--160"
        assert entry["doi"] == "10.18653/v1/2023.evalnlp-1.16"
        assert entry["url"] == "https://arxiv.org/abs/2309.15217v2"

    def test_chinese_literal_authors_and_publisher_are_preserved(self) -> None:
        text = to_bibtex(CORPUS_ITEMS)
        entries = _bibtex_entries(text)
        book = next(f for f in entries.values() if "多智能体系统导论" in f["title"])
        assert book["__type__"] == "book"
        assert book["author"] == "陈国强 and 刘洋"
        assert book["publisher"] == "科学出版社"
        assert book["address"] == "北京"
        assert book["year"] == "2022"
        assert "中国科技期刊研究" in text
        assert "证据可追溯的自动文献综述方法研究" in text

    def test_conference_uses_booktitle(self) -> None:
        entries = _bibtex_entries(to_bibtex(CORPUS_ITEMS))
        conf = next(f for f in entries.values() if "Multi-Agent Task Orchestration" in f["title"])
        assert conf["__type__"] == "inproceedings"
        assert conf["booktitle"].startswith("Proceedings of the 2024 International Conference")
        assert "journal" not in conf
        assert conf["publisher"] == "ACM"
        assert conf["address"] == "New York"
        assert conf["pages"] == "55--70"

    def test_thesis_and_manuscript_types(self) -> None:
        entries = _bibtex_entries(to_bibtex(CORPUS_ITEMS))
        thesis = next(
            f for f in entries.values() if "证据可追溯的自动文献综述方法研究" in f["title"]
        )
        assert thesis["__type__"] == "phdthesis"
        assert thesis["publisher"] == "清华大学"
        manuscript = next(f for f in entries.values() if "FAIR-RAG" in f["title"])
        assert manuscript["__type__"] == "misc"
        assert manuscript["journal"] == "arXiv"
        assert manuscript["url"] == "https://arxiv.org/abs/2502.01234v1"

    def test_entries_are_separated_by_blank_line(self) -> None:
        text = to_bibtex(CORPUS_ITEMS)
        assert text.endswith("}\n")
        assert "\n\n@" in text


class TestBibtexEscaping:
    def test_special_characters_are_escaped(self) -> None:
        item = _item(title=r"A & B: 100% $ # _ { } ~ ^ \ end")
        text = to_bibtex([item])
        assert r"A \& B: 100\% \$ \# \_ \{ \} \textasciitilde{} \textasciicircum{}" in text
        assert r"\textbackslash{} end" in text

    def test_escaped_values_round_trip_through_reader(self) -> None:
        item = _item(title="M&M 100%")
        entry = next(iter(_bibtex_entries(to_bibtex([item])).values()))
        assert entry["title"] == r"M\&M 100\%"

    @pytest.mark.parametrize(
        ("raw", "escaped"),
        [
            ("&", r"\&"),
            ("%", r"\%"),
            ("$", r"\$"),
            ("#", r"\#"),
            ("_", r"\_"),
            ("{", r"\{"),
            ("}", r"\}"),
            ("~", r"\textasciitilde{}"),
            ("^", r"\textasciicircum{}"),
            ("\\", r"\textbackslash{}"),
        ],
    )
    def test_each_special_character(self, raw: str, escaped: str) -> None:
        text = to_bibtex([_item(title=f"x{raw}y")])
        assert f"title = {{x{escaped}y}}" in text


class TestCitationKey:
    def test_first_author_family_plus_year_plus_index(self) -> None:
        entries = _bibtex_entries(to_bibtex(CORPUS_ITEMS))
        assert "Es20231" in entries
        assert "牛耕田20241" in entries
        assert "陈国强20221" in entries
        assert "Smith20241" in entries
        assert "赵敏20251" in entries
        assert "Garcia20251" in entries

    def test_no_author_falls_back_to_anon(self) -> None:
        keys = list(_bibtex_entries(to_bibtex([_item(author=[])])))
        assert keys == ["Anon20241"]

    def test_no_year_uses_nd_placeholder(self) -> None:
        keys = list(_bibtex_entries(to_bibtex([_item(issued_year=None)])))
        assert keys == ["Doend1"]

    def test_duplicate_author_and_year_get_unique_keys(self) -> None:
        first = _item(id="a")
        second = _item(id="b", title="另一篇")
        entries = _bibtex_entries(to_bibtex([first, second]))
        assert list(entries) == ["Doe20241", "Doe20242"]

    def test_punctuation_and_spaces_are_stripped_from_key(self) -> None:
        hyphenated = _item(author=[CslName(family="Espinosa-Anke", given="Luis")])
        literal = _item(author=[CslName(literal="Evidence Review Consortium")])
        entries = _bibtex_entries(to_bibtex([hyphenated, literal]))
        assert list(entries) == ["EspinosaAnke20241", "EvidenceReviewConsortium20241"]

    def test_keys_are_unique_across_corpus(self) -> None:
        entries = _bibtex_entries(to_bibtex(CORPUS_ITEMS))
        assert len(entries) == len(CORPUS_ITEMS)


class TestRisFrozenCorpus:
    def test_all_six_items_export_with_expected_types(self) -> None:
        records = _ris_records(to_ris(CORPUS_ITEMS))
        assert len(records) == 6
        assert [record["TY"][0] for record in records] == [
            "JOUR",
            "JOUR",
            "BOOK",
            "CONF",
            "THES",
            "UNPB",
        ]
        assert all(record["ER"] == [""] for record in records)

    def test_journal_record_fields(self) -> None:
        records = _ris_records(to_ris(CORPUS_ITEMS))
        record = next(r for r in records if "Ragas" in r["TI"][0])
        assert record["AU"] == [
            "Es, Shahul",
            "James, Jithin",
            "Espinosa-Anke, Luis",
            "Schockaert, Steven",
        ]
        assert record["JO"][0].startswith("Proceedings of the 1st Workshop")
        assert record["PY"] == ["2023"]
        assert record["VL"] == ["12"]
        assert record["IS"] == ["3"]
        assert record["SP"] == ["150"]
        assert record["EP"] == ["160"]
        assert record["DO"] == ["10.18653/v1/2023.evalnlp-1.16"]
        assert record["UR"] == ["https://arxiv.org/abs/2309.15217v2"]

    def test_chinese_literals_are_preserved(self) -> None:
        text = to_ris(CORPUS_ITEMS)
        records = _ris_records(text)
        record = next(r for r in records if "面向学位论文的中文参考文献著录规则研究" in r["TI"][0])
        assert record["AU"] == ["牛耕田", "李泽", "张三", "王五"]
        assert record["JO"] == ["中国科技期刊研究"]
        assert record["PY"] == ["2024"]

    def test_conference_uses_t2_and_book_fields(self) -> None:
        records = _ris_records(to_ris(CORPUS_ITEMS))
        conf = next(r for r in records if "Multi-Agent Task Orchestration" in r["TI"][0])
        assert conf["T2"][0].startswith("Proceedings of the 2024 International Conference")
        assert "JO" not in conf
        assert conf["PB"] == ["ACM"]
        assert conf["CY"] == ["New York"]
        book = next(r for r in records if "多智能体系统导论" in r["TI"][0])
        assert book["PB"] == ["科学出版社"]
        assert book["CY"] == ["北京"]

    def test_single_page_has_no_end_page(self) -> None:
        records = _ris_records(to_ris([_item(page="42")]))
        assert records[0]["SP"] == ["42"]
        assert "EP" not in records[0]

    def test_records_are_separated_by_blank_line(self) -> None:
        text = to_ris(CORPUS_ITEMS)
        assert text.endswith("ER  -\n")
        assert "\n\nTY  - " in text


class TestFieldReadback:
    @pytest.mark.parametrize("item", CORPUS_ITEMS, ids=[item.id for item in CORPUS_ITEMS])
    def test_bibtex_round_trip_keeps_core_fields(self, item: CslItem) -> None:
        entries = _bibtex_entries(to_bibtex([item]))
        assert len(entries) == 1
        entry = next(iter(entries.values()))
        assert entry["title"] == item.title
        if item.issued_year is not None:
            assert entry["year"] == str(item.issued_year)
        if item.container_title is not None:
            container = entry.get("journal") or entry.get("booktitle")
            assert container == item.container_title

    @pytest.mark.parametrize("item", CORPUS_ITEMS, ids=[item.id for item in CORPUS_ITEMS])
    def test_ris_round_trip_keeps_core_fields(self, item: CslItem) -> None:
        records = _ris_records(to_ris([item]))
        assert len(records) == 1
        record = records[0]
        assert record["TI"] == [item.title]
        if item.issued_year is not None:
            assert record["PY"] == [str(item.issued_year)]
        expected_authors = [
            f"{name.family}, {name.given}".strip(", ") if name.family else name.literal
            for name in item.author
        ]
        assert record.get("AU", []) == expected_authors


class TestEmptyInput:
    def test_bibtex_empty_returns_empty_string(self) -> None:
        assert to_bibtex([]) == ""

    def test_ris_empty_returns_empty_string(self) -> None:
        assert to_ris([]) == ""
