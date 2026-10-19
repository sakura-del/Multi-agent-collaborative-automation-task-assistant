"""CSL-JSON 契约与映射（Issue #4 M1 计划 §1.1 / ADR 0001 §4.2）。

设计约束（不得违反）：

- 内部以 **CSL-JSON 为唯一真源**：`CslItem` 是严格模型（``extra="forbid"``），只放我们会填充的字段；
- 每条文献必须有 ``language``（zh-CN / en-US …）——它决定渲染期的逐条 locale 分组；
  缺失时按「显式 > 中文作者名 > 题名含 CJK」启发式判定（ADR §4.2）；
- ``StyleMeta`` 与 ``StyleResolver`` 协议定义在**本模块**（计划 §1.2 注释）：``styles.StyleRegistry``
  实现该协议，``renderer.py`` 只依赖协议，测试可注入 fake resolver → 避免 models ↔ styles ↔ renderer 循环依赖；
- ref_id 规则（计划 §1.5）：``doi:{归一化 DOI}`` → ``src:{source_id}`` → ``title:{归一化题名 sha256 前 12 位}``。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.exceptions import ValidationException

#: 默认样式（计划 §1.1）：自研 GB/T 7714—2025 顺序编码制。
DEFAULT_STYLE_ID = "gbt7714-2025-numeric"

#: CSL type → GB/T 7714 文献类型标识（Issue #4 要求的新标识，含 2025 新增 PP/DS/EB）。
TYPE_IDENTIFIERS: dict[str, str] = {
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

#: 缺省文献类型：``LiteratureRecord`` 目前只来自 arXiv / 离线清单（预印本）。
DEFAULT_LITERATURE_RECORD_TYPE = "manuscript"

#: bibliography 条目缺 type 时的兜底类型（reviewer 产出的条目只有题名/URL 时）。
DEFAULT_BIBLIOGRAPHY_TYPE = "article-journal"

#: CJK 表意文字（含扩展 A 与兼容区）；用于语言启发式判定。
CJK_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

_DOI_PREFIX_PATTERN = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)
_DOI_URL_PATTERN = re.compile(r"^https?://(?:dx\.)?doi\.org/", re.IGNORECASE)
_LANGUAGE_SPLIT_PATTERN = re.compile(r"[-_]")
_YEAR_PATTERN = re.compile(r"\d{4}")

Numbering = Literal["citation-number", "none"]
Ordering = Literal["citation-order", "author-date"]


# ===== 样式元数据与解析协议（计划 §1.1 / §1.2）=====


class StyleMeta(BaseModel):
    """单个 CSL 样式的元数据（``styles.StyleRegistry`` 产出，渲染器消费）。"""

    model_config = ConfigDict(extra="forbid")

    style_id: str
    """归一后的样式 ID（无别名、与 ``styles/`` 下的资产对应）。"""

    title: str
    """人类可读名（``<info><title>``）。"""

    numbering: Numbering
    """``citation-number``：应用层按排序结果分配 ``[1]``…``[n]``；``none``：不编号。"""

    ordering: Ordering
    """``citation-order``：保持条目给出的引用顺序；``author-date``：按作者/年份排序。"""

    path: Path
    """CSL 文件路径（渲染器只依赖 ``resolve`` + ``meta``，样式内容由此加载）。"""

    default_locale: str = "en-US"
    """``<style default-locale>``；条目 ``language`` 无法映射到已知 locale 时的兜底。"""

    is_default: bool = False
    """是否为默认样式。"""


@runtime_checkable
class StyleResolver(Protocol):
    """样式解析协议：``styles.StyleRegistry`` 实现它，渲染器只依赖它。"""

    def resolve(self, style_id: str) -> str:
        """别名归一：``'gbt7714'`` → ``'gbt7714-2025-numeric'``；未知 id 抛异常。"""
        ...

    def meta(self, style_id: str) -> StyleMeta:
        """返回样式元数据（含 CSL 文件路径）。"""
        ...


# ===== CSL-JSON 契约 =====


class CslName(BaseModel):
    """CSL-JSON 著者名（``literal`` 用于机构作者与中文姓名原样输出）。"""

    model_config = ConfigDict(extra="forbid")

    family: str = ""
    given: str = ""
    literal: str = ""


class CslItem(BaseModel):
    """单条参考文献（内部唯一真源 = CSL-JSON 字段子集）。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    """稳定 ref_id（见模块 docstring 的 ref_id 规则），正文占位 ``[[cite:ref_id]]`` 与之对应。"""

    type: str
    """CSL type：article-journal / book / chapter / paper-conference / thesis / report /
    dataset / webpage / manuscript（预印本）…"""

    title: str
    author: list[CslName] = Field(default_factory=list)
    container_title: str | None = None
    volume: str | None = None
    issue: str | None = None
    page: str | None = None
    publisher: str | None = None
    publisher_place: str | None = None
    issued_year: int | None = None
    doi: str | None = None
    url: str | None = None
    language: str = "zh-CN"
    """★ 决定 locale 分组（ADR §4.2）：``zh*`` → zh-CN，``en*`` → en-US。"""

    @classmethod
    def from_csl_json(cls, data: Mapping[str, Any]) -> CslItem:
        """标准 CSL-JSON dict → ``CslItem``。

        - 只取本模型建模的字段，**忽略**其它 CSL 字段（如 ``abstract`` / ``keyword``）；
        - ``id`` / ``type`` / ``title`` 缺失或空白 → :class:`ValidationException`；
        - ``issued`` 兼容 ``date-parts`` / ``raw`` / ``year`` / 纯年份字符串；
        - ``language`` 缺失时按 ADR §4.2 的字符集启发式判定（不臆造 zh-CN）。
        """
        if not isinstance(data, Mapping):
            raise ValidationException("CSL-JSON 条目必须是 mapping")

        item_id = _text(data.get("id"))
        item_type = _text(data.get("type"))
        title = _text(data.get("title"))
        missing = [
            name
            for name, value in (("id", item_id), ("type", item_type), ("title", title))
            if not value
        ]
        if missing:
            raise ValidationException(f"CSL-JSON 缺必需字段：{', '.join(missing)}")

        authors = [
            name
            for name in (_csl_name_from_json(entry) for entry in _as_sequence(data.get("author")))
            if name.family or name.given or name.literal
        ]
        language = _text(data.get("language"))
        payload: dict[str, Any] = {
            "id": item_id,
            "type": item_type,
            "title": title,
            "author": authors,
            "container_title": _text(data.get("container-title")) or None,
            "volume": _text(data.get("volume")) or None,
            "issue": _text(data.get("issue")) or None,
            "page": _text(data.get("page")) or None,
            "publisher": _text(data.get("publisher")) or None,
            "publisher_place": _text(data.get("publisher-place")) or None,
            "issued_year": parse_year(data.get("issued")),
            "doi": normalize_doi(_first_present(data, "DOI", "doi")),
            "url": _text(_first_present(data, "URL", "url")) or None,
            "language": language
            or detect_language(title=title, authors=[author_sort_text(name) for name in authors]),
        }
        try:
            return cls.model_validate(payload)
        except ValidationError as exc:  # pragma: no cover - 逐字段已清洗，防御性兜底
            raise ValidationException(f"CSL-JSON 条目非法：{exc}") from exc

    def to_csl_json(self) -> dict[str, Any]:
        """``CslItem`` → 标准 CSL-JSON dict（``None`` / 空字符串字段不落 JSON）。"""
        payload: dict[str, Any] = {"id": self.id, "type": self.type, "title": self.title}

        names = [entry for entry in (_name_to_csl_json(name) for name in self.author) if entry]
        if names:
            payload["author"] = names
        optional = {
            "container-title": self.container_title,
            "volume": self.volume,
            "issue": self.issue,
            "page": self.page,
            "publisher": self.publisher,
            "publisher-place": self.publisher_place,
        }
        for key, value in optional.items():
            if value:
                payload[key] = value
        if self.issued_year is not None:
            payload["issued"] = {"date-parts": [[self.issued_year]]}
        if self.doi:
            payload["DOI"] = self.doi
        if self.url:
            payload["URL"] = self.url
        if self.language:
            payload["language"] = self.language
        return payload


# ===== 文本 / 标识归一 =====


def normalize_doi(value: Any) -> str | None:
    """DOI 归一：去 ``https://doi.org/`` / ``doi:`` 前缀、去空白、小写；空 → ``None``。"""
    text = _text(value)
    if not text:
        return None
    text = _DOI_PREFIX_PATTERN.sub("", text).strip().lower()
    return text or None


def doi_from_url(value: Any) -> str | None:
    """从 ``https://doi.org/10.xxxx/yyy`` 形式的链接提取归一化 DOI；非 DOI 链接 → ``None``。

    与核验器 ``reference_verifier._doi_from_url`` 语义一致：reviewer 产出的 bibliography
    条目只有 ``source_url`` 时，也应命中 §1.5 的「DOI 优先」ref_id 规则。
    """
    text = _text(value)
    if not _DOI_URL_PATTERN.match(text):
        return None
    return normalize_doi(text)


def normalize_title(title: Any) -> str:
    """题名归一：NFKC + casefold + 折叠空白（用于 ref_id 哈希与作者-出版年排序）。"""
    normalized = unicodedata.normalize("NFKC", _text(title))
    return " ".join(normalized.split()).casefold()


def build_ref_id(
    *,
    doi: Any = None,
    source_id: Any = None,
    title: Any = None,
) -> str:
    """按计划 §1.5 构造稳定 ref_id：DOI → source_id → 题名哈希前 12 位。"""
    normalized_doi = normalize_doi(doi)
    if normalized_doi:
        return f"doi:{normalized_doi}"
    clean_source = _text(source_id)
    if clean_source:
        return f"src:{clean_source}"
    normalized_title = normalize_title(title)
    if normalized_title:
        digest = sha256(normalized_title.encode("utf-8")).hexdigest()[:12]
        return f"title:{digest}"
    raise ValidationException("无法构造 ref_id：doi / source_id / title 均为空")


def author_sort_text(name: CslName) -> str:
    """著者名的可排序/可读文本：``literal`` 优先，否则 ``family given``。"""
    if name.literal:
        return name.literal
    return " ".join(part for part in (name.family, name.given) if part)


def parse_csl_name(text: Any) -> CslName:
    """姓名字符串 → ``CslName``。

    - 含 CJK → ``literal``（中文/日文姓名与机构名原样输出，不拆姓/名）；
    - ``"Zhang, Wei"`` → family=Zhang / given=Wei；
    - ``"Wei Zhang"`` → family=Zhang / given=Wei（西文「名 姓」惯例）；
    - 无法拆分时整体作为 ``family``。

    ⚠️ 罗马化中文姓名（如 ``"Niu Gengtian"``）会被按西文惯例拆成 family=Gengtian，
    这是无姓名库时的已知限制；调用方应优先传 ``literal`` / 已归一的名字。
    """
    raw = _text(text)
    if not raw:
        return CslName()
    if CJK_PATTERN.search(raw):
        return CslName(literal=raw)
    if "," in raw:
        family, _, given = raw.partition(",")
        return CslName(family=family.strip(), given=given.strip())
    parts = raw.split()
    if len(parts) == 1:
        return CslName(family=parts[0])
    return CslName(family=parts[-1], given=" ".join(parts[:-1]))


def detect_language(*, title: str, authors: Sequence[str] = (), explicit: str | None = None) -> str:
    """判定条目语言：显式 ``language`` > 中文作者名 > 题名含 CJK → zh-CN，否则 en-US。

    显式值会归一化：``zh`` / ``zh-TW`` → ``zh-CN``，``en`` / ``en-GB`` → ``en-US``，
    其它（如 ``ja-JP``）原样返回（渲染时回落样式 ``default_locale``）。
    """
    explicit_text = _text(explicit)
    if explicit_text:
        return _normalize_language(explicit_text)
    for author in authors:
        if CJK_PATTERN.search(_text(author)):
            return "zh-CN"
    if CJK_PATTERN.search(_text(title)):
        return "zh-CN"
    return "en-US"


def parse_year(value: Any) -> int | None:
    """从 CSL ``issued``（或 ``year`` / 年份字符串 / ``date`` / ``datetime``）提取年份。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        match = _YEAR_PATTERN.search(value)
        return int(match.group()) if match else None
    year_attr = getattr(value, "year", None)
    if isinstance(year_attr, int) and not isinstance(year_attr, bool):
        return year_attr
    if isinstance(value, Mapping):
        for key in ("date-parts", "year", "raw", "literal", "date"):
            if key in value:
                year = parse_year(value[key])
                if year is not None:
                    return year
        return None
    if isinstance(value, Sequence):
        for entry in value:
            year = parse_year(entry)
            if year is not None:
                return year
        return None
    return None


# ===== 业务对象 → CslItem 映射（计划 §1.5）=====


def csl_item_from_literature_record(record: Any, *, ref_id: str | None = None) -> CslItem:
    """``LiteratureRecord``（或同形 mapping / 对象）→ ``CslItem``。

    ``LiteratureRecord`` 目前只来自 arXiv / 离线清单，故默认类型为 ``manuscript``（预印本 → PP）；
    若来源对象带 ``type`` 字段则优先采用。``ref_id`` 显式给出时覆盖自动规则。
    """
    source_id = _text(_field(record, "source_id"))
    title = _text(_field(record, "title"))
    authors = [_name_from_any(entry) for entry in _as_sequence(_field(record, "authors"))]
    authors = [name for name in authors if name.family or name.given or name.literal]
    author_texts = [author_sort_text(name) for name in authors]
    # §1.5「DOI 优先」：显式 doi 字段，缺失时尝试 doi.org 形式的 URL
    doi = _field(record, "doi") or doi_from_url(_field(record, "source_url", "url"))

    return CslItem(
        id=ref_id or build_ref_id(doi=doi, source_id=source_id, title=title),
        type=_text(_field(record, "type")) or DEFAULT_LITERATURE_RECORD_TYPE,
        title=title,
        author=authors,
        container_title=_text(
            _field(record, "container_title", "container-title", "journal", "venue")
        )
        or None,
        volume=_text(_field(record, "volume")) or None,
        issue=_text(_field(record, "issue")) or None,
        page=_text(_field(record, "page")) or None,
        publisher=_text(_field(record, "publisher")) or None,
        publisher_place=_text(_field(record, "publisher_place", "publisher-place")) or None,
        issued_year=parse_year(_field(record, "published_at", "issued", "year")),
        doi=normalize_doi(doi),
        url=_text(_field(record, "source_url", "url")) or None,
        language=detect_language(
            title=title,
            authors=author_texts,
            explicit=_text(_field(record, "language")) or None,
        ),
    )


def csl_item_from_bibliography_entry(entry: Mapping[str, Any]) -> CslItem | None:
    """reviewer 产出的 bibliography 条目（计划 §1.5 增强键）→ ``CslItem``；缺 title → ``None``。

    优先使用条目里的 ``csl_json``（唯一真源）；否则用扁平键
    （``title`` / ``authors`` / ``year`` / ``language`` / ``doi`` / ``source_id`` / ``source_url`` …）；
    ``ref_id`` 键可显式覆盖，否则按 §1.5 规则构造。
    """
    if not isinstance(entry, Mapping):
        raise ValidationException("bibliography 条目必须是 mapping")

    title = _text(entry.get("title"))
    explicit_id = _text(entry.get("ref_id")) or None
    # §1.5「DOI 优先」：reviewer 条目可能只带 doi.org 形式的 source_url（与核验器同规则）
    doi = _first_present(entry, "doi", "DOI") or doi_from_url(
        _first_present(entry, "source_url", "url")
    )
    source_id = _text(entry.get("source_id")) or None

    csl_json = entry.get("csl_json")
    if isinstance(csl_json, Mapping) and csl_json:
        payload = dict(csl_json)
        # csl_json 是唯一真源：它自己的 title 优先，条目扁平 title 只在 csl_json 缺失时兜底
        payload_title = _text(payload.get("title")) or title
        if not payload_title:
            return None
        payload["title"] = payload_title
        if not _first_present(payload, "DOI", "doi"):
            derived_doi = doi_from_url(_first_present(payload, "URL", "url"))
            if derived_doi:
                payload["DOI"] = derived_doi
        if not _text(payload.get("id")):
            payload["id"] = explicit_id or build_ref_id(
                doi=_first_present(payload, "DOI", "doi") or doi,
                source_id=source_id,
                title=payload_title,
            )
        if not _text(payload.get("type")):
            payload["type"] = _text(entry.get("type")) or DEFAULT_BIBLIOGRAPHY_TYPE
        if not _text(payload.get("language")):
            language = _text(entry.get("language"))
            if language:
                payload["language"] = language
        try:
            return CslItem.from_csl_json(payload)
        except ValidationException as exc:
            raise ValidationException(f"bibliography 条目的 csl_json 非法：{exc.message}") from exc

    if not title:
        return None

    authors = [_name_from_any(item) for item in _as_sequence(entry.get("authors"))]
    authors = [name for name in authors if name.family or name.given or name.literal]
    author_texts = [author_sort_text(name) for name in authors]

    return CslItem(
        id=explicit_id or build_ref_id(doi=doi, source_id=source_id, title=title),
        type=_text(entry.get("type")) or DEFAULT_BIBLIOGRAPHY_TYPE,
        title=title,
        author=authors,
        container_title=_text(
            _first_present(entry, "container_title", "container-title", "journal", "venue")
        )
        or None,
        volume=_text(entry.get("volume")) or None,
        issue=_text(entry.get("issue")) or None,
        page=_text(entry.get("page")) or None,
        publisher=_text(entry.get("publisher")) or None,
        publisher_place=_text(_first_present(entry, "publisher_place", "publisher-place")) or None,
        issued_year=parse_year(_first_present(entry, "year", "issued", "published_at")),
        doi=normalize_doi(doi),
        url=_text(_first_present(entry, "source_url", "url")) or None,
        language=detect_language(
            title=title,
            authors=author_texts,
            explicit=_text(entry.get("language")) or None,
        ),
    )


# ===== 内部工具 =====


def _text(value: Any) -> str:
    """任意值 → 去空白字符串（``None`` → ``""``）。"""
    if value is None:
        return ""
    return str(value).strip()


def _as_sequence(value: Any) -> list[Any]:
    """把「单个值 / 字符串 / mapping / 序列」统一成 list。"""
    if value is None:
        return []
    if isinstance(value, (str, bytes, Mapping)):
        return [value]
    if isinstance(value, Sequence):
        return list(value)
    return [value]


def _first_present(source: Mapping[str, Any], *keys: str) -> Any:
    """返回第一个「非 None」的值（用于 ``DOI``/``URL`` 等大小写差异）。"""
    for key in keys:
        if key in source and source[key] is not None:
            return source[key]
    return None


def _field(source: Any, *names: str) -> Any:
    """从 mapping 或对象按属性名取第一个非 ``None`` 值。"""
    if source is None:
        return None
    for name in names:
        if isinstance(source, Mapping):
            if name in source and source[name] is not None:
                return source[name]
        else:
            value = getattr(source, name, None)
            if value is not None:
                return value
    return None


def _normalize_language(value: str) -> str:
    """显式语言归一：``zh*`` → zh-CN，``en*`` → en-US，其它原样。"""
    head = _LANGUAGE_SPLIT_PATTERN.split(value, maxsplit=1)[0].casefold()
    if head == "zh":
        return "zh-CN"
    if head == "en":
        return "en-US"
    return value


def _name_from_any(value: Any) -> CslName:
    """字符串或 CSL-JSON 姓名 dict → ``CslName``。"""
    if isinstance(value, Mapping):
        return _csl_name_from_json(value)
    return parse_csl_name(value)


def _csl_name_from_json(value: Any) -> CslName:
    """CSL-JSON ``author[i]`` → ``CslName``（忽略 ``affiliation`` 等未建模字段）。"""
    if isinstance(value, Mapping):
        return CslName(
            family=_text(value.get("family")),
            given=_text(value.get("given")),
            literal=_text(value.get("literal")),
        )
    if isinstance(value, str):
        return parse_csl_name(value)
    return CslName()


def _name_to_csl_json(name: CslName) -> dict[str, str]:
    """``CslName`` → CSL-JSON 姓名 dict（全空返回空 dict，由调用方过滤）。"""
    if name.literal:
        return {"literal": name.literal}
    entry: dict[str, str] = {}
    if name.family:
        entry["family"] = name.family
    if name.given:
        entry["given"] = name.given
    return entry
