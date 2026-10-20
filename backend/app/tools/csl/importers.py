"""BibTeX / RIS → CSL-JSON 导入（Phase 1 P1-E，计划 §1.5）。

本模块是 :mod:`app.tools.csl.exporters` 的**逆运算**（round-trip 测试锁定）：

- ``parse_bibtex``：支持 ``@article/@book/@inbook/@inproceedings/@phdthesis/@techreport/@misc``
  以及 ``@incollection/@conference/@mastersthesis/@unpublished/@online/...`` 等常见别名；
  字段值支持 ``{...}``、``"..."``、裸值三种形式；``@comment`` / ``@preamble`` 忽略；
  导出时的 10 个特殊字符转义（``\\& \\% \\$ \\# \\_ \\{ \\} \\textasciitilde{}
  \\textasciicircum{} \\textbackslash{}``）在此反转义。
- ``parse_ris``：按 ``TY  -`` 起、``ER  -`` 止切记录，把 RIS 标签映射为 ``CslItem`` 字段
  （``SP``+``EP`` → ``page``，``LA`` → ``language``，未知标签忽略）。

设计约定：

- **绝不静默返回半成品**：语法/语义错误一律抛 :class:`ValidationException`（带条目 key 或行号），
  不跳过坏条目继续返回部分结果；**空输入 → ``[]``**；
- 中文按 UTF-8 原样保留，不做转写；不引入新依赖；
- 类型映射与 ``exporters.py`` 的导出映射互逆（``BIBTEX_TYPE_TO_CSL`` / ``RIS_TYPE_TO_CSL``），
  一致性由 round-trip 测试锁定，避免两侧漂移。
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from app.core.exceptions import ValidationException
from app.tools.csl.models import (
    CslItem,
    CslName,
    author_sort_text,
    build_ref_id,
    detect_language,
    doi_from_url,
    normalize_doi,
    parse_csl_name,
    parse_year,
)

# ===== 类型反向映射（与 exporters.BIBTEX_TYPE_MAP / RIS_TYPE_MAP 互逆）=====

#: BibTeX 条目类型 → CSL type。
#:
#: 注意：导出映射是**多对一**的（``manuscript/dataset/webpage/standard/patent`` 都导出为
#: ``@misc``），导入只能回到一个规范类型；``misc`` 取 ``manuscript``（预印本），
#: 与 exporters 的默认类型一致（round-trip 对冻结语料成立；歧义为已知限制）。
BIBTEX_TYPE_TO_CSL: dict[str, str] = {
    "article": "article-journal",
    "book": "book",
    "booklet": "book",
    "proceedings": "book",
    "inbook": "chapter",
    "incollection": "chapter",
    "inproceedings": "paper-conference",
    "conference": "paper-conference",
    "phdthesis": "thesis",
    "mastersthesis": "thesis",
    "thesis": "thesis",
    "techreport": "report",
    "report": "report",
    "manual": "report",
    "unpublished": "manuscript",
    "misc": "manuscript",
    "online": "webpage",
    "electronic": "webpage",
    "www": "webpage",
    "dataset": "dataset",
    "patent": "patent",
    "standard": "standard",
}

#: RIS 类型标签 → CSL type（覆盖 Zotero / EndNote 常见类型）。
RIS_TYPE_TO_CSL: dict[str, str] = {
    "JOUR": "article-journal",
    "JFULL": "article-journal",
    "MGZN": "article-magazine",
    "NEWS": "article-newspaper",
    "BOOK": "book",
    "EBK": "book",
    "EDBOOK": "book",
    "SER": "book",
    "CHAP": "chapter",
    "ECHAP": "chapter",
    "CONF": "paper-conference",
    "CPAPER": "paper-conference",
    "THES": "thesis",
    "RPRT": "report",
    "GOVDOC": "report",
    "GRANT": "report",
    "UNPB": "manuscript",
    "MANSCPT": "manuscript",
    "GEN": "manuscript",
    "DATA": "dataset",
    "DBASE": "dataset",
    "ELEC": "webpage",
    "WEB": "webpage",
    "STAND": "standard",
    "PATENT": "patent",
    "PAT": "patent",
}

#: 导出转义 → 原文的单次正则反转义表（含 exporter 使用的 3 个 LaTeX 文本命令）。
#:
#: 单次替换（而不是连续 ``replace``）保证 ``\\textbackslash{}\\&`` 这类组合不会被二次反转义。
_BIBTEX_UNESCAPE_MAP: dict[str, str] = {
    r"\textbackslash{}": "\\",
    r"\textasciitilde{}": "~",
    r"\textasciicircum{}": "^",
    r"\&": "&",
    r"\%": "%",
    r"\$": "$",
    r"\#": "#",
    r"\_": "_",
    r"\{": "{",
    r"\}": "}",
    r"\~": "~",
    r"\^": "^",
    r"\\": "\\",
}
_BIBTEX_UNESCAPE_RE = re.compile(
    "|".join(re.escape(token) for token in sorted(_BIBTEX_UNESCAPE_MAP, key=len, reverse=True))
)

#: ``@comment`` / ``@preamble`` 整条忽略（内容可为任意 LaTeX，不做解析）。
_IGNORED_BIBTEX_TYPES = frozenset({"comment", "preamble"})

_WHITESPACE = " \t\r\n"

_BIBTEX_HEAD_RE = re.compile(r"@\s*(?P<etype>[A-Za-z]+)\s*(?P<brace>[{(])")
_BIBTEX_FIELD_NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")

#: RIS 标签行：``XX  - value``（最多两个空格；值可缺省，如 ``ER  -``）。
_RIS_LINE_RE = re.compile(r"^(?P<tag>[A-Z][A-Z0-9])\s{1,2}-\s?(?P<value>.*)$")

#: 页码范围分隔符（`150--160` / `150–160` / `150—160` → `150-160`）。
_PAGE_RANGE_RE = re.compile(r"\s*[-\u2013\u2014]+\s*")


# ===== public API =====


def parse_bibtex(text: str) -> list[CslItem]:
    """把 BibTeX 文本解析为 ``list[CslItem]``（空输入 → ``[]``；非法输入 → ValidationException）。"""
    if not isinstance(text, str):
        raise ValidationException("BibTeX 输入必须是字符串")
    cleaned = _strip_bibtex_comments(text)

    items: list[CslItem] = []
    position = 0
    length = len(cleaned)
    while position < length:
        at = cleaned.find("@", position)
        dangling = cleaned[position:] if at < 0 else cleaned[position:at]
        if dangling.strip():
            raise ValidationException(f"存在无法解析的 BibTeX 内容：{dangling.strip()[:40]!r}")
        if at < 0:
            break

        head = _BIBTEX_HEAD_RE.match(cleaned, at)
        if head is None:
            raise ValidationException(f"无法解析 BibTeX 条目头（位置 {at}）")
        if head.group("brace") != "{":
            raise ValidationException(f"仅支持花括号形式 @type{{...}}（位置 {at}）")
        entry_type = head.group("etype").casefold()
        body_start = head.end()

        if entry_type in _IGNORED_BIBTEX_TYPES:
            position = _skip_braced_entry(cleaned, body_start, entry_type)
            continue
        if entry_type == "string":
            raise ValidationException("不支持 @string 宏定义（请提供展开后的 .bib 文件）")

        key, fields, position = _parse_bibtex_body(cleaned, body_start, entry_type)
        items.append(_csl_item_from_bibtex_entry(entry_type, key, fields))

    return items


def parse_ris(text: str) -> list[CslItem]:
    """把 RIS 文本解析为 ``list[CslItem]``（空输入 → ``[]``；非法输入 → ValidationException）。"""
    if not isinstance(text, str):
        raise ValidationException("RIS 输入必须是字符串")

    records: list[dict[str, list[str]]] = []
    current: dict[str, list[str]] | None = None
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.rstrip()
        if not line.strip():
            continue
        match = _RIS_LINE_RE.match(line)
        if match is None:
            raise ValidationException(f"RIS 第 {line_number} 行不是合法的标签行：{line[:40]!r}")
        tag = match.group("tag")
        value = match.group("value").strip()

        if tag == "TY":
            if current is not None:
                raise ValidationException(
                    f"RIS 第 {line_number} 行出现新的 TY，但上一条记录缺少 ER"
                )
            current = {"TY": [value]}
            records.append(current)
            continue
        if current is None:
            raise ValidationException(f"RIS 第 {line_number} 行在 TY 之前出现标签 {tag}")
        if tag == "ER":
            _validate_ris_record(current)
            current = None
            continue
        current.setdefault(tag, []).append(value)

    if current is not None:
        raise ValidationException("RIS 记录缺少 ER 结束标记")
    return [_csl_item_from_ris_record(record) for record in records]


# ===== BibTeX 扫描 =====


def _strip_bibtex_comments(text: str) -> str:
    """删除**整行**注释（行首 ``%``）；行内 ``%`` 保留（导出值中的 ``%`` 已转义为 ``\\%``）。"""
    return "".join(
        line for line in text.splitlines(keepends=True) if not line.lstrip().startswith("%")
    )


def _skip_braced_entry(text: str, start: int, entry_type: str) -> int:
    """跳过 ``@comment`` / ``@preamble`` 的平衡花括号内容，返回结束括号之后的位置。"""
    depth = 0
    position = start
    length = len(text)
    while position < length:
        char = text[position]
        if char == "\\" and position + 1 < length:
            position += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            if depth == 0:
                return position + 1
            depth -= 1
        position += 1
    raise ValidationException(f"@{entry_type} 条目的花括号未闭合")


def _parse_bibtex_body(text: str, start: int, entry_type: str) -> tuple[str, dict[str, str], int]:
    """解析 ``key, field = value, ...}``，返回 (citation key, 字段表, 结束括号后位置)。"""
    length = len(text)
    position = start
    while position < length and text[position] in _WHITESPACE:
        position += 1

    key_start = position
    while position < length and text[position] not in ",}":
        if text[position] in "{(":
            raise ValidationException(f"@{entry_type} 的 citation key 非法（位置 {position}）")
        position += 1
    if position >= length:
        raise ValidationException(f"@{entry_type} 条目缺少结束花括号")

    key = text[key_start:position].strip()
    if not key:
        raise ValidationException(f"@{entry_type} 条目缺少 citation key")
    if text[position] == "}":
        raise ValidationException(f"@{entry_type}{{{key}}} 没有任何字段（缺 title）")

    fields, position = _parse_bibtex_fields(text, position + 1, entry_type, key)
    return key, fields, position


def _parse_bibtex_fields(
    text: str, start: int, entry_type: str, key: str
) -> tuple[dict[str, str], int]:
    fields: dict[str, str] = {}
    length = len(text)
    position = start
    while True:
        while position < length and (text[position] in _WHITESPACE or text[position] == ","):
            position += 1
        if position >= length:
            raise ValidationException(f"@{entry_type}{{{key}}} 缺少结束花括号")
        if text[position] == "}":
            return fields, position + 1

        match = _BIBTEX_FIELD_NAME_RE.match(text, position)
        if match is None:
            raise ValidationException(
                f"@{entry_type}{{{key}}} 字段名非法（位置 {position}）：{text[position : position + 20]!r}"
            )
        name = match.group(0).casefold()
        position = match.end()
        while position < length and text[position] in _WHITESPACE:
            position += 1
        if position >= length or text[position] != "=":
            raise ValidationException(f"@{entry_type}{{{key}}} 字段 {name} 缺少 '='")
        position += 1
        while position < length and text[position] in _WHITESPACE:
            position += 1
        if position >= length:
            raise ValidationException(f"@{entry_type}{{{key}}} 字段 {name} 缺少值")

        value, position = _read_bibtex_value(text, position, entry_type, key, name)
        # 重复字段：后者覆盖（BibTeX 消费端惯例）
        fields[name] = value.strip()

        while position < length and text[position] in _WHITESPACE:
            position += 1
        if position >= length:
            raise ValidationException(f"@{entry_type}{{{key}}} 缺少结束花括号")
        if text[position] == ",":
            position += 1
            continue
        if text[position] == "}":
            return fields, position + 1
        raise ValidationException(
            f"@{entry_type}{{{key}}} 字段 {name} 之后缺少逗号或 '}}'（位置 {position}）"
        )


def _read_bibtex_value(
    text: str, position: int, entry_type: str, key: str, name: str
) -> tuple[str, int]:
    """读取一个字段值：``{...}`` / ``"..."`` / 裸值；返回 (原文值, 值之后位置)。"""
    char = text[position]
    if char == "{":
        return _read_braced(text, position + 1, entry_type, key, name)
    if char == '"':
        return _read_quoted(text, position + 1, entry_type, key, name)

    start = position
    while position < len(text) and text[position] not in ",}" and text[position] not in _WHITESPACE:
        position += 1
    value = text[start:position]
    if not value:
        raise ValidationException(f"@{entry_type}{{{key}}} 字段 {name} 缺少值")
    return value, position


def _read_braced(text: str, position: int, entry_type: str, key: str, name: str) -> tuple[str, int]:
    """读取平衡花括号值（保留内部原文，含 LaTeX 转义），返回 (内容, 右括号后位置)。"""
    depth = 0
    start = position
    length = len(text)
    while position < length:
        char = text[position]
        if char == "\\" and position + 1 < length:
            position += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            if depth == 0:
                return text[start:position], position + 1
            depth -= 1
        position += 1
    raise ValidationException(f"@{entry_type}{{{key}}} 字段 {name} 的花括号未闭合")


def _read_quoted(text: str, position: int, entry_type: str, key: str, name: str) -> tuple[str, int]:
    """读取双引号值（支持 ``\\"`` 转义），返回 (内容, 右引号后位置)。"""
    start = position
    length = len(text)
    while position < length:
        char = text[position]
        if char == "\\" and position + 1 < length:
            position += 2
            continue
        if char == '"':
            return text[start:position], position + 1
        position += 1
    raise ValidationException(f"@{entry_type}{{{key}}} 字段 {name} 的引号未闭合")


def _unescape_bibtex(value: str) -> str:
    """单次正则反转义（与 exporters._escape_bibtex 互逆）。"""
    if "\\" not in value:
        return value
    return _BIBTEX_UNESCAPE_RE.sub(lambda match: _BIBTEX_UNESCAPE_MAP[match.group(0)], value)


def _first_field(fields: Mapping[str, str], *names: str) -> str:
    """返回第一个非空字段值（已反转义）。"""
    for name in names:
        raw = fields.get(name)
        if raw is None:
            continue
        value = _unescape_bibtex(raw).strip()
        if value:
            return value
    return ""


def _csl_item_from_bibtex_entry(entry_type: str, key: str, fields: Mapping[str, str]) -> CslItem:
    csl_type = BIBTEX_TYPE_TO_CSL.get(entry_type)
    if csl_type is None:
        raise ValidationException(f"不支持的 BibTeX 条目类型：@{entry_type}（条目 {key}）")

    title = _first_field(fields, "title")
    if not title:
        raise ValidationException(f"BibTeX 条目 {key} 缺少 title")

    authors = _parse_bibtex_authors(fields.get("author", ""))
    doi = normalize_doi(_first_field(fields, "doi")) or doi_from_url(_first_field(fields, "url"))
    source_id = _first_field(fields, "source_id")
    pages = _normalize_page(_first_field(fields, "pages"))

    return CslItem(
        id=f"doi:{doi}" if doi else (f"src:{source_id}" if source_id else key),
        type=csl_type,
        title=title,
        author=authors,
        container_title=_first_field(fields, "journal", "booktitle") or None,
        volume=_first_field(fields, "volume") or None,
        issue=_first_field(fields, "number") or None,
        page=pages or None,
        publisher=_first_field(fields, "publisher") or None,
        publisher_place=_first_field(fields, "address") or None,
        issued_year=parse_year(_first_field(fields, "year", "date")),
        doi=doi,
        url=_first_field(fields, "url") or None,
        language=detect_language(title=title, authors=[author_sort_text(name) for name in authors]),
    )


def _parse_bibtex_authors(raw: str) -> list[CslName]:
    """``author`` 字段 → 姓名列表：``and`` 分隔；``{...}`` 包裹视为机构/整体作者（literal）。"""
    unescaped = _unescape_bibtex(raw)
    if not unescaped.strip():
        return []
    authors: list[CslName] = []
    # `and` 分隔（容忍末尾多余的 `and`，Zotero/手工文件常见）
    for part in re.split(r"\s+and(?:\s+|$)", unescaped):
        text = part.strip()
        if not text:
            continue
        if _is_fully_braced(text):
            authors.append(CslName(literal=text[1:-1].strip()))
            continue
        name = parse_csl_name(text)
        if name.family or name.given or name.literal:
            authors.append(name)
    return authors


def _is_fully_braced(text: str) -> bool:
    """判断 ``text`` 是否被一层花括号完整包裹（BibTeX 整体保护写法）。"""
    if len(text) < 2 or text[0] != "{" or text[-1] != "}":
        return False
    depth = 0
    for index, char in enumerate(text):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and index != len(text) - 1:
                return False
    return depth == 0


def _normalize_page(value: str) -> str:
    """``150--160`` / ``150–160`` → ``150-160``（与 RIS 的 SP-EP 形态统一）。"""
    return _PAGE_RANGE_RE.sub("-", value.strip()) if value else ""


# ===== RIS 映射 =====


def _ris_values(record: Mapping[str, list[str]], *tags: str) -> list[str]:
    """按标签顺序收集所有值。"""
    values: list[str] = []
    for tag in tags:
        values.extend(record.get(tag, []))
    return values


def _ris_first(record: Mapping[str, list[str]], *tags: str) -> str:
    """返回第一个非空标签值。"""
    for value in _ris_values(record, *tags):
        if value.strip():
            return value.strip()
    return ""


def _validate_ris_record(record: Mapping[str, list[str]]) -> None:
    if not (record.get("TY") or [""])[0].strip():
        raise ValidationException("RIS 记录的 TY（类型）为空")
    if not (_ris_first(record, "TI", "T1")):
        raise ValidationException(f"RIS 记录缺少标题（TI）：类型 {record['TY'][0]!r}")


def _csl_item_from_ris_record(record: Mapping[str, list[str]]) -> CslItem:
    ris_type = (record.get("TY") or [""])[0].strip().upper()
    csl_type = RIS_TYPE_TO_CSL.get(ris_type)
    if csl_type is None:
        raise ValidationException(f"不支持的 RIS 类型：{ris_type!r}")

    # 记录完整性（TY 非空、TI 存在）已由 `_validate_ris_record` 在扫描期校验
    title = _ris_first(record, "TI", "T1")

    authors = [parse_csl_name(value) for value in _ris_values(record, "AU", "A1") if value.strip()]
    url = _ris_first(record, "UR", "L1") or None
    doi = normalize_doi(_ris_first(record, "DO")) or doi_from_url(url)

    page = _ris_page(_ris_first(record, "SP"), _ris_first(record, "EP"))
    return CslItem(
        id=f"doi:{doi}" if doi else build_ref_id(doi=None, source_id=None, title=title),
        type=csl_type,
        title=title,
        author=authors,
        container_title=_ris_first(record, "JO", "T2", "JA", "JF", "BT") or None,
        volume=_ris_first(record, "VL") or None,
        issue=_ris_first(record, "IS") or None,
        page=page,
        publisher=_ris_first(record, "PB") or None,
        publisher_place=_ris_first(record, "CY") or None,
        issued_year=parse_year(_ris_first(record, "PY", "Y1", "DA")),
        doi=doi,
        url=url,
        language=detect_language(
            title=title,
            authors=[author_sort_text(name) for name in authors],
            explicit=_ris_first(record, "LA") or None,
        ),
    )


def _ris_page(start: str, end: str) -> str | None:
    """``SP`` + ``EP`` → ``100-110``；只有一端时返回该端。"""
    if start and end:
        return f"{start}-{end}"
    return start or end or None
