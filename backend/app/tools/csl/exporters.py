"""CSL-JSON → BibTeX / RIS 文本导出（Issue #4 M1 §1.4）。

设计约定：
- 纯字符串生成，**不引入新依赖**；中文按 UTF-8 原样输出（不做拼音/ASCII 转写）；
- 空输入返回空串；
- BibTeX：`@type{key,` + 每行 `  name = {value}` + `}`，条目之间空行分隔、整体以换行结尾；
  字段集合固定为 `title/author/journal|booktitle/year/volume/number/pages/publisher/address/doi/url`，
  `chapter`/`paper-conference` 用 `booktitle`，其余有容器名的类型用 `journal`；
- citation key：`{首作者姓或 literal}{年份或 nd}{序号}`；作者部分去掉空格与标点（保留中日韩字符），
  序号从 1 开始按同 base 递增，保证同一批导出内唯一；无作者用 `Anon`，无年份用 `nd`；
- RIS：`TY/AU/TI/JO|T2/PY/VL/IS/SP/EP/PB/CY/DO/UR/ER`，`chapter`/`paper-conference` 用 `T2`，
  记录之间空行分隔、整体以换行结尾。
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.tools.csl.models import CslItem, CslName

#: CSL type → BibTeX 条目类型
BIBTEX_TYPE_MAP: dict[str, str] = {
    "article-journal": "article",
    "article-magazine": "article",
    "article-newspaper": "article",
    "book": "book",
    "chapter": "inbook",
    "paper-conference": "inproceedings",
    "thesis": "phdthesis",
    "report": "techreport",
    "manuscript": "misc",
    "dataset": "misc",
    "webpage": "misc",
    "standard": "misc",
    "patent": "misc",
}
DEFAULT_BIBTEX_TYPE = "misc"

#: CSL type → RIS 类型标签
RIS_TYPE_MAP: dict[str, str] = {
    "article-journal": "JOUR",
    "article-magazine": "MGZN",
    "article-newspaper": "NEWS",
    "book": "BOOK",
    "chapter": "CHAP",
    "paper-conference": "CONF",
    "thesis": "THES",
    "report": "RPRT",
    "manuscript": "UNPB",
    "dataset": "DATA",
    "webpage": "ELEC",
    "standard": "STAND",
    "patent": "PATENT",
}
DEFAULT_RIS_TYPE = "GEN"

#: 使用 `booktitle` / RIS `T2` 的类型（容器是会议录/文集而非期刊）
_CONTAINER_AS_SECONDARY_TYPES = frozenset({"chapter", "paper-conference"})

#: BibTeX 特殊字符 → 转义（逐字符替换，天然避免二次转义）
_BIBTEX_ESCAPES: dict[str, str] = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}

_PAGE_SEPARATOR_RE = re.compile(r"\s*[-\u2013\u2014]+\s*")


def to_bibtex(items: Sequence[CslItem]) -> str:
    """把 CSL-JSON 条目导出为 BibTeX 文本；空输入 → 空串。"""
    if not items:
        return ""
    keys = _citation_keys(items)
    entries = [
        _format_bibtex_entry(
            BIBTEX_TYPE_MAP.get(item.type, DEFAULT_BIBTEX_TYPE),
            key,
            _bibtex_fields(item),
        )
        for item, key in zip(items, keys, strict=True)
    ]
    return "\n\n".join(entries) + "\n"


def to_ris(items: Sequence[CslItem]) -> str:
    """把 CSL-JSON 条目导出为 RIS 文本；空输入 → 空串。"""
    if not items:
        return ""
    return "\n\n".join(_format_ris_record(item) for item in items) + "\n"


# ===== BibTeX =====


def _format_bibtex_entry(entry_type: str, key: str, fields: Sequence[tuple[str, str]]) -> str:
    lines = [f"@{entry_type}{{{key},"]
    for index, (name, value) in enumerate(fields):
        suffix = "," if index < len(fields) - 1 else ""
        lines.append(f"  {name} = {{{value}}}{suffix}")
    lines.append("}")
    return "\n".join(lines)


def _bibtex_fields(item: CslItem) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    authors = _bibtex_authors(item.author)
    if authors:
        fields.append(("author", _escape_bibtex(authors)))
    fields.append(("title", _escape_bibtex(item.title)))
    if item.container_title:
        container_field = "booktitle" if item.type in _CONTAINER_AS_SECONDARY_TYPES else "journal"
        fields.append((container_field, _escape_bibtex(item.container_title)))
    if item.issued_year is not None:
        fields.append(("year", str(item.issued_year)))
    if item.volume:
        fields.append(("volume", _escape_bibtex(item.volume)))
    if item.issue:
        fields.append(("number", _escape_bibtex(item.issue)))
    if item.page:
        fields.append(("pages", _escape_bibtex(_normalize_pages(item.page))))
    if item.publisher:
        fields.append(("publisher", _escape_bibtex(item.publisher)))
    if item.publisher_place:
        fields.append(("address", _escape_bibtex(item.publisher_place)))
    if item.doi:
        fields.append(("doi", _escape_bibtex(item.doi)))
    if item.url:
        fields.append(("url", _escape_bibtex(item.url)))
    return fields


def _escape_bibtex(value: str) -> str:
    """转义 BibTeX 特殊字符：`& % $ # _ { } ~ ^ \\`。"""
    return "".join(_BIBTEX_ESCAPES.get(char, char) for char in value)


def _bibtex_authors(authors: Sequence[CslName]) -> str:
    return " and ".join(part for name in authors if (part := _bibtex_author(name)))


def _bibtex_author(name: CslName) -> str:
    """`family, given`；机构/中文 literal 原样；两者皆空 → 空串（跳过）。"""
    if name.family:
        given = name.given.strip()
        return f"{name.family}, {given}".rstrip(", ") if given else name.family
    return name.literal or name.given


def _normalize_pages(page: str) -> str:
    """BibTeX 页码范围用 en dash：`150-160` / `150–160` → `150--160`。"""
    return _PAGE_SEPARATOR_RE.sub("--", page.strip())


def _citation_keys(items: Sequence[CslItem]) -> list[str]:
    """生成唯一 citation key：`{首作者姓}{年份}{序号}`。"""
    keys: list[str] = []
    counters: dict[str, int] = {}
    for item in items:
        year = str(item.issued_year) if item.issued_year is not None else "nd"
        base = f"{_first_author_key_part(item)}{year}"
        counters[base] = counters.get(base, 0) + 1
        keys.append(f"{base}{counters[base]}")
    return keys


def _first_author_key_part(item: CslItem) -> str:
    for name in item.author:
        sanitized = _sanitize_key_part(name.family or name.literal or name.given)
        if sanitized:
            return sanitized
    return "Anon"


def _sanitize_key_part(value: str) -> str:
    """去掉空格与标点，保留字母/数字/中日韩字符（不做小写化，BibTeX key 大小写不敏感）。"""
    return "".join(char for char in value if char.isalnum())


# ===== RIS =====


def _format_ris_record(item: CslItem) -> str:
    lines = [f"TY  - {RIS_TYPE_MAP.get(item.type, DEFAULT_RIS_TYPE)}"]
    for name in item.author:
        author = _ris_author(name)
        if author:
            lines.append(f"AU  - {author}")
    lines.append(f"TI  - {item.title}")
    if item.container_title:
        tag = "T2" if item.type in _CONTAINER_AS_SECONDARY_TYPES else "JO"
        lines.append(f"{tag}  - {item.container_title}")
    if item.issued_year is not None:
        lines.append(f"PY  - {item.issued_year}")
    if item.volume:
        lines.append(f"VL  - {item.volume}")
    if item.issue:
        lines.append(f"IS  - {item.issue}")
    if item.page:
        start, end = _split_pages(item.page)
        lines.append(f"SP  - {start}")
        if end:
            lines.append(f"EP  - {end}")
    if item.publisher:
        lines.append(f"PB  - {item.publisher}")
    if item.publisher_place:
        lines.append(f"CY  - {item.publisher_place}")
    if item.doi:
        lines.append(f"DO  - {item.doi}")
    if item.url:
        lines.append(f"UR  - {item.url}")
    lines.append("ER  -")
    return "\n".join(lines)


def _ris_author(name: CslName) -> str:
    """RIS 作者行：`family, given`；机构/中文 literal 原样。"""
    if name.family:
        given = name.given.strip()
        return f"{name.family}, {given}".rstrip(", ") if given else name.family
    return name.literal or name.given


def _split_pages(page: str) -> tuple[str, str | None]:
    """`150-160` → (`150`, `160`)；单页 → (`42`, None)。"""
    parts = _PAGE_SEPARATOR_RE.split(page.strip(), maxsplit=1)
    if len(parts) == 2 and parts[1]:
        return parts[0], parts[1]
    return parts[0], None
