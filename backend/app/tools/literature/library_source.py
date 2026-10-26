"""引用库文献源：把用户的个人引用库适配成工作流需要的检索源。

## 为什么需要它

取证发现导入页能正确解析 BibTeX / RIS，但解析完**无法用于任何任务** ——
工作流的文献来自检索源（arXiv / OpenAlex / PubMed），导入结果没有入口。
用户自带文献库（Zotero / EndNote）是真实需求：综述要引的往往就是自己读过的那批。

## 与检索源的关键差异

| | 检索源（arXiv 等） | 引用库 |
| --- | --- | --- |
| 数据来源 | 外部 HTTP | 本地 DB |
| 可复现性 | 依赖外部服务当时的内容 | **完全可复现**（用户自己的库） |
| 摘要 | 有 | **没有**（只存元数据） |
| 数量 | 按 `max_results` 截断 | 按 `max_results` 截断 |

**没有摘要是有意的非目标**（引用库计划 §2）：综述的证据来自元数据与正文引用，
摘要不是必需；且存摘要会引入版权与存储成本。

## `LiteratureRecord` 是冻结契约

它 `extra="forbid"`，`published_at` / `source_url` / `pdf_url` 都是必填
（`app/tools/literature/models.py`）。所以入库时已经用准入保证
「有年份、有 URL 或 DOI」（`ReferenceLibraryService.rejection_reason`）。
本模块**仍然**对缺字段的条目做防御性跳过 + warning，
因为库里的历史数据可能来自放宽准入之前的版本。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import HttpUrl
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.knowledge.services.reference_library_service import ReferenceLibraryService
from app.tools.csl.models import CslItem, CslName
from app.tools.literature.models import LiteratureRecord, LiteratureSearch

logger = get_logger(__name__)

__all__ = ["LibraryLiteratureSource", "csl_item_to_record"]


def _author_text(name: CslName) -> str:
    """`CslName` → `"Family, Given"`（与导入器输出口径一致）。"""
    if name.literal:
        return str(name.literal)
    family = name.family or ""
    given = name.given or ""
    if family and given:
        return f"{family}, {given}"
    return str(family or given)


def csl_item_to_record(item: CslItem) -> LiteratureRecord | None:
    """`CslItem` → `LiteratureRecord`；无法构造时返回 None（调用方跳过并记 warning）。

    ## 为什么不编造缺失字段

    - 无 `issued_year`：**不写 1970**。那会让按时间排序的检索把无年份条目排最前，
      用户看到 1970 会以为数据错了。
    - 无 `url` 也无 `doi`：无法给出 `source_url`，直接跳过。

    这与模板页那轮的教训同源（AGENTS §5 #28）：别为了让契约通过而造假数据。
    """
    if item.issued_year is None:
        return None

    url = item.url or (f"https://doi.org/{item.doi}" if item.doi else None)
    if not url:
        return None

    try:
        return LiteratureRecord(
            source_id=item.id[:64],
            title=item.title[:500],
            authors=[_author_text(name) for name in item.author],
            abstract="",
            # 只有年份没有月日：取当年 1 月 1 日（明确、可解释，不是随机值）
            published_at=datetime(item.issued_year, 1, 1, tzinfo=UTC),
            source_url=HttpUrl(url),
            pdf_url=HttpUrl(url),
            categories=[],
        )
    except Exception as exc:  # 防御：HttpUrl 校验失败等
        logger.warning(
            "reference_library_record_invalid",
            ref_id=item.id,
            error=str(exc),
        )
        return None


class LibraryLiteratureSource:
    """从用户引用库取文献（不访问网络）。

    `search()` 的 `query` 用于**本地关键词过滤**（题名/作者/刊名包含匹配），
    而不是外呼检索 —— 用户的库是他自己挑的，不该因为 LLM 生成的查询词
    而丢掉大部分条目。空 query 或无法匹配时返回库里前 `max_results` 条。
    """

    def __init__(self, user_id: uuid.UUID, session: AsyncSession) -> None:
        self._user_id = user_id
        self._session = session

    async def search(self, search: LiteratureSearch) -> list[LiteratureRecord]:
        service = ReferenceLibraryService(self._session)
        items = await service.all_csl_items(self._user_id)

        records: list[LiteratureRecord] = []
        skipped = 0
        for item in items:
            record = csl_item_to_record(item)
            if record is None:
                skipped += 1
                continue
            records.append(record)

        if skipped:
            # 历史数据可能来自放宽准入之前的版本：跳过但留痕，便于排查
            logger.warning(
                "reference_library_source_skipped_invalid",
                user_id=str(self._user_id),
                skipped=skipped,
                total=len(items),
            )

        matched = _filter_by_query(records, search.query)
        if not matched:
            # 关键词没命中时退回整库：用户选「用我的引用库」是要用他的文献，
            # 不是要看「关键词没匹配到」。LLM 生成的查询词常常与库内表述不一致。
            logger.info(
                "reference_library_source_query_miss",
                user_id=str(self._user_id),
                query=search.query,
                library_size=len(records),
            )
            matched = records

        return matched[: search.max_results]

    async def aclose(self) -> None:
        """与检索源接口对齐；引用库没有连接需要释放。"""
        return None


def _filter_by_query(records: list[LiteratureRecord], query: str) -> list[LiteratureRecord]:
    """本地关键词过滤：按空白切词，**全部词都命中**才算匹配（AND 语义）。

    匹配范围：题名 + 作者 + 来源标识。大小写不敏感。
    """
    terms = [term for term in query.strip().lower().split() if term]
    if not terms:
        return list(records)

    matched: list[LiteratureRecord] = []
    for record in records:
        haystack = " ".join([record.title, *record.authors, record.source_id]).lower()
        if all(term in haystack for term in terms):
            matched.append(record)
    return matched
