"""Zotero 条目 → BibTeX 转换与只读 Local API 导入（计划 Task 4 / Task 5）。

本模块的边界：

- **只读**：仅向本机 ``http://localhost:23119/api`` 发 GET，不写 Zotero、
  不远程访问 localhost、不实时同步；
- **纯转换**：条目 → BibTeX 不依赖数据库；产物必须能被
  :func:`app.tools.csl.importers.parse_bibtex` 直接解析（round-trip 由测试锁定）；
- **复用现有准入**：生成的 ``.bib`` 交给现有导入页 / ``ReferenceLibraryService.save()``，
  不另写去重或准入逻辑。

真实 Zotero 验证不在自动化测试范围内：测试全部走 ``httpx.MockTransport``，
所以「fixture 通过」不等于「真实 Zotero 验收通过」。
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable
from typing import Any

import httpx

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


_SKIP_ITEM_TYPES = frozenset({"attachment", "note", "annotation"})
"""非文献条目：导出成 BibTeX 会污染导入，甚至因缺标题让整批解析失败。"""


def _is_exportable(item: dict[str, Any]) -> bool:
    """只保留有标题、且不是笔记/附件/批注的 Zotero 条目。"""
    raw_data = item.get("data")
    data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
    if _text(data.get("itemType")) in _SKIP_ITEM_TYPES:
        return False
    return bool(_text(data.get("title")))


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
    raw_data = item.get("data")
    data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
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
    return "\n".join(zotero_item_to_bibtex(item) for item in items if _is_exportable(item))


# ===== 只读 Local API 客户端（Task 5）=====

DEFAULT_BASE_URL = "http://localhost:23119/api"
MAX_EXPORT_BYTES = 1024 * 1024
"""单次导出上限 1 MB：超过就不是「一次上传」的量级，应拆集合。"""


class ZoteroError(RuntimeError):
    """可读的 Zotero 访问错误（403 / 空集合 / 版本不符等）。"""


class ZoteroLocalClient:
    """Zotero Local API 的只读客户端（分页拉取，不写回）。

    只接受调用方注入的 ``httpx.Client``；本类不提供 base URL 参数，
    所以无法被配置成访问远程主机（计划硬约束：不远程访问 localhost）。
    """

    def __init__(self, client: httpx.Client, *, page_size: int = 100) -> None:
        self._client = client
        self._page_size = max(1, page_size)

    def _get_json(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        response = self._client.get(path, params=params)
        if response.status_code == 403:
            raise ZoteroError("Zotero Local API 未启用，请在 Zotero 设置中允许本机应用访问")
        if response.status_code >= 400:
            raise ZoteroError(f"Zotero Local API 返回 {response.status_code}: {path}")
        return response.json()

    def check_version(self) -> None:
        """握手：403 = Local API 未启用；版本不是 3 = 客户端不兼容。"""
        response = self._client.get("/")
        if response.status_code == 403:
            raise ZoteroError("Zotero Local API 未启用，请在 Zotero 设置中允许本机应用访问")
        if response.status_code >= 400:
            raise ZoteroError(f"Zotero Local API 返回 {response.status_code}: /")
        if response.headers.get("Zotero-API-Version") != "3":
            raise ZoteroError("仅支持 Zotero API v3；请检查桌面客户端版本")

    def collections(self) -> list[dict[str, Any]]:
        payload = self._get_json("/users/0/collections")
        if not isinstance(payload, list):
            raise ZoteroError("Zotero 返回的集合不是列表")
        return payload

    def collection_items(self, collection: str | None) -> list[dict[str, Any]]:
        """按 ``start`` / ``limit`` 分页拉取；短页即最后一页。

        ``collection`` 为 ``None`` 时拉全部条目（``/users/0/items``）。
        """
        path = f"/users/0/collections/{collection}/items" if collection else "/users/0/items"
        items: list[dict[str, Any]] = []
        start = 0
        while True:
            page = self._get_json(path, params={"limit": self._page_size, "start": start})
            if not isinstance(page, list):
                raise ZoteroError("Zotero 返回的条目不是列表")
            items.extend(page)
            if len(page) < self._page_size:
                return items
            start += len(page)


# ===== CLI（Task 5）=====


def main(argv: list[str] | None = None, *, client: httpx.Client | None = None) -> int:
    """导出条目为 BibTeX；失败返回非 0 且**不生成输出文件**。

    ``client`` 仅用于测试注入（MockTransport）；正常执行时只连
    :data:`DEFAULT_BASE_URL`，没有远程 base URL 选项。
    """
    parser = argparse.ArgumentParser(description="Export Zotero items to BibTeX without writes")
    parser.add_argument("--collection", default=None)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    owned = client is None
    http_client = client or httpx.Client(base_url=DEFAULT_BASE_URL, timeout=5.0)
    try:
        local = ZoteroLocalClient(http_client)
        local.check_version()
        raw_items = local.collection_items(args.collection)
        items = [item for item in raw_items if _is_exportable(item)]
        if not items:
            keys = [
                str(item.get("key"))
                for item in local.collections()
                if isinstance(item, dict) and item.get("key")
            ]
            reason = "集合为空或不存在" if not raw_items else "集合内没有可导出的文献条目"
            raise ZoteroError(
                f"{reason}：{args.collection or '全部条目'}；"
                f"可选集合：{', '.join(keys) or '无'}"
            )
        text = items_to_bibtex(items)
        if len(text.encode("utf-8")) > MAX_EXPORT_BYTES:
            raise ZoteroError("导出超过 1 MB，请按集合拆分")
        # "x"：绝不覆盖已有文件（用户的导出文件可能已被手工整理过）
        with open(args.output, "x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        print(f"已导出 {len(items)} 条到 {args.output}")
        return 0
    except (ZoteroError, OSError, httpx.HTTPError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        if owned:
            http_client.close()


if __name__ == "__main__":
    raise SystemExit(main())
