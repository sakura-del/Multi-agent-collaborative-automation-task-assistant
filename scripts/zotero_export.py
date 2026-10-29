"""Zotero 条目 → BibTeX 纯转换（计划 Task 4）。

本模块只做**纯转换**：不访问 Zotero Local API、不读写文件、不依赖第三方库；
Local API 拉取与 CLI 属于 Task 5。产物必须能被
:func:`app.tools.csl.importers.parse_bibtex` 直接解析（round-trip 由测试锁定）。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

BIBTEX_TYPES = {
    "journalArticle": "article",
    "book": "book",
    "conferencePaper": "inproceedings",
    "report": "techreport",
    "thesis": "phdthesis",
    "webpage": "misc",
}
YEAR_RE = re.compile(r"\d{4}")


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


_BIBTEX_ESCAPE_MAP = {
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "%": r"\%",
    "&": r"\&",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def _escape(value: Any) -> str:
    text = re.sub(r"[\r\n]+", " ", _text(value))
    return "".join(_BIBTEX_ESCAPE_MAP.get(char, char) for char in text)


def _year(value: Any) -> int | None:
    match = YEAR_RE.search(_text(value))
    return int(match.group(0)) if match else None


def _authors(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    names = []
    for creator in value:
        if not isinstance(creator, dict):
            continue
        literal = _text(creator.get("name"))
        if literal:
            names.append(literal)
            continue
        first, last = _text(creator.get("firstName")), _text(creator.get("lastName"))
        if first and last:
            names.append(f"{last}, {first}")
        elif first or last:
            names.append(first or last)
    return " and ".join(names)


def _doi(data: dict[str, Any]) -> str:
    return _text(data.get("DOI") or data.get("doi")).lower()


def zotero_item_to_bibtex(item: dict[str, Any]) -> str:
    data = item.get("data") if isinstance(item.get("data"), dict) else {}
    key = _text(item.get("key")) or "unknown"
    doi = _doi(data)
    cite_key = f"doi:{doi}" if doi else f"zotero:{key}"
    entry_type = BIBTEX_TYPES.get(_text(data.get("itemType")), "misc")
    fields = {
        "title": _text(data.get("title")),
        "author": _authors(data.get("creators")),
    }
    year = _year(data.get("date") or data.get("year"))
    if year is not None:
        fields["year"] = str(year)
    if doi:
        fields["doi"] = doi
    url = _text(data.get("url")) or (f"https://doi.org/{doi}" if doi else "")
    if url:
        fields["url"] = url
    body = ",\n".join(f"  {name}={{{_escape(value)}}}" for name, value in fields.items() if value)
    return f"@{entry_type}{{{cite_key},\n{body}\n}}\n"


def items_to_bibtex(items: Iterable[dict[str, Any]]) -> str:
    return "\n".join(zotero_item_to_bibtex(item) for item in items)
