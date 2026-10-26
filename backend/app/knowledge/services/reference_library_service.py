"""引用库领域服务（2026-10-26 引用库计划 §3.4 / §3.5）。

## 核心职责

1. **保存**：把导入预览的 `CslItem` 批量入库，按 `ref_id` 幂等；
2. **准入**：拒绝无法当文献源用的条目，并给出**可读原因**；
3. **查询**：owner-scoped 列表（稳定排序）+ 摘要；
4. **删除**：owner-scoped，跨用户 404。

## 为什么准入这么严（本模块最重要的设计决定）

引用库条目要能当**文献源**用，就必须能构造出 `LiteratureRecord`
（`app/tools/literature/models.py`，`extra="forbid"` 的冻结契约）：

```python
class LiteratureRecord(BaseModel):
    source_id: str
    title: str
    published_at: datetime      # ← 必填
    source_url: HttpUrl         # ← 必填
    pdf_url: HttpUrl            # ← 必填
    ...
```

于是两条硬约束：

| 缺失 | 后果（如果不拒绝） | 所以 |
| --- | --- | --- |
| `issued_year` | 无法给出真实 `published_at`。编一个 1970 会让「按时间排序」把无年份条目排最前，用户看到 1970 会以为数据错了 | **拒绝入库** |
| `url` 且 `doi` | 无法给出 `source_url` | **拒绝入库** |

宁可入库失败并说清原因，也不要存进去之后在任务执行时炸掉、或产生假数据。
这和模板页那轮学到的教训同源（AGENTS §5 #28）：
**契约能防「少填」，防不住「填假的」，所以别造假。**

## 为什么 payload 存整份 CslItem

见 `app/db/models/reference_library.py` 的模块说明：字段会随 CSL 演进增加，
拆列每次都要迁移。冗余列（`title`/`issued_year`/`doi`）只为查询，
真源永远是 `payload`。

## 事务边界

本层负责 `commit` / `rollback`（与 `CitationStyleService` 同一约定：
Repository 只 flush，Service 提交）。所以 `save` / `delete` / `delete_all`
都在成功路径上显式 `commit`。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException, ValidationException
from app.core.logging import get_logger
from app.db.models.reference_library import ReferenceLibraryItem
from app.tools.csl.models import CslItem

logger = get_logger(__name__)

__all__ = [
    "LibrarySummary",
    "ReferenceLibraryService",
    "RejectedItem",
    "SaveResult",
    "normalize_doi",
    "same_work",
]


# ===== DOI 归一与判重（2026-10-26 引用库遗留计划 §3.4）=====

_DOI_URL_PREFIXES = (
    "https://doi.org/",
    "http://doi.org/",
    "https://dx.doi.org/",
    "http://dx.doi.org/",
    "doi.org/",
    "dx.doi.org/",
)
"""DOI 的 URL 包装前缀（按长度降序匹配，先长后短）。"""


def normalize_doi(value: str | None) -> str | None:
    """DOI 归一：去 URL 前缀、去 `doi:` 前缀、小写、去空白。

    用户从不同来源复制同一条文献是常态：

        doi:10.1234/A
        doi:https://doi.org/10.1234/a
        doi:DOI: 10.1234/a

    三种写法归一后必须相同，否则同一条文献会被存成三条。
    空值/纯空白返回 ``None``（**不返回空串** —— 空串参与判重会误合并）。
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    lowered = text.lower()
    for prefix in _DOI_URL_PREFIXES:
        if lowered.startswith(prefix):
            text = text[len(prefix) :]
            lowered = text.lower()
            break
    # `doi:` / `DOI:` / `doi :` 这类前缀可能叠在 URL 前缀之内或之外
    for prefix in ("doi:", "doi :", "doi："):
        if lowered.startswith(prefix):
            text = text[len(prefix) :]
            break
    text = text.strip().lower()
    return text or None


def same_work(left: CslItem, right: CslItem) -> bool:
    """两条 CSL 条目是否是同一篇文献（**仅按归一化 DOI 判定**）。

    为什么不用题名：同名不同文非常常见（"A Survey"、"Introduction"），
    靠题名合并会把不同文献错误地并成一条 —— 那比重复更糟（用户丢了文献）。

    为什么任一侧缺 DOI 就不判重：没有可靠标识时「不合并」是安全侧。
    """
    left_doi = normalize_doi(left.doi)
    right_doi = normalize_doi(right.doi)
    if left_doi is None or right_doi is None:
        return False
    return left_doi == right_doi


@dataclass(frozen=True)
class RejectedItem:
    """被拒绝入库的条目（带可读原因，前端要逐条展示）。"""

    ref_id: str
    title: str
    reason: str


@dataclass
class SaveResult:
    """保存结果：**必须能表达部分成功**。

    `created` / `skipped` / `merged` / `rejected` 四个数字都要在，
    用户才知道「哪些进去了、哪些本来就在、哪些合并进了已有条目、哪些没进去以及为什么」。

    `merged` 与 `skipped` 的区别：
    - `skipped`：`ref_id` 完全相同（重复导入同一份文件）；
    - `merged`：`ref_id` 不同但归一化 DOI 相同（同一文献的不同写法）。
    """

    created: int = 0
    skipped: int = 0
    merged: int = 0
    rejected: list[RejectedItem] = field(default_factory=list)


@dataclass(frozen=True)
class LibrarySummary:
    """引用库摘要（前端用来提示「将有 N 篇文献可用」）。"""

    total: int
    usable_as_source: int


class ReferenceLibraryService:
    """引用库的保存 / 查询 / 删除（全部 owner-scoped）。"""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ===== 准入 =====

    @staticmethod
    def rejection_reason(item: CslItem) -> str | None:
        """返回拒绝原因；可入库则返回 None。

        判据来自 `LiteratureRecord` 的必填字段，见模块 docstring。
        """
        if item.issued_year is None:
            return "缺出版年份：无法确定发表时间，不能作为文献源使用"
        if not item.url and not item.doi:
            return "缺 URL 与 DOI：无法生成可追溯的来源链接，不能作为文献源使用"
        return None

    # ===== 写入 =====

    async def save(self, user_id: uuid.UUID, items: list[CslItem]) -> SaveResult:
        """批量保存；按 `ref_id` 幂等，并按**归一化 DOI** 合并重复文献。

        ## 为什么有两种去重

        - `ref_id` 完全相同 → `skipped`（重复导入同一份文件，最常见）；
        - `ref_id` 不同但归一化 DOI 相同 → `merged`（同一文献的不同写法，
          例如 `10.1234/A` 与 `https://doi.org/10.1234/a`）。

        只做 `ref_id` 判重的话，用户从 Zotero / Scholar / 期刊页各复制一次
        就会存三条，摘要里同一篇文献被引用三次。

        ## 为什么不整批失败

        用户粘一份 `.bib` 里常有几条缺年份的（预印本、网页引用）。
        整批 400 会让用户完全没法保存另外 19 条 —— 但他确实需要那 19 条。
        所以逐条判定：能进的进，不能进的带原因返回。
        **只有全部被拒时**，端点才回 400（见 `endpoints/reference_library.py`）。
        """
        result = SaveResult()
        if not items:
            return result

        existing = await self._existing_ref_ids(user_id, [item.id for item in items])
        # 库里已有的 DOI → 已有条目（合并时补充空字段用）
        existing_by_doi = await self._existing_by_normalized_doi(user_id, items)

        seen_in_batch: set[str] = set()
        seen_doi_in_batch: dict[str, CslItem] = {}
        for item in items:
            if item.id in existing or item.id in seen_in_batch:
                result.skipped += 1
                continue
            reason = self.rejection_reason(item)
            if reason is not None:
                result.rejected.append(
                    RejectedItem(ref_id=item.id, title=item.title, reason=reason)
                )
                continue

            normalized = normalize_doi(item.doi)
            if normalized is not None:
                # ① 与库里已有条目同 DOI → 合并（只补空字段，不覆盖用户编辑）
                row = existing_by_doi.get(normalized)
                if row is not None:
                    if self._fill_missing(row, item):
                        await self._db.flush()
                        await self._db.commit()
                    result.merged += 1
                    continue
                # ② 与本批次前面的条目同 DOI → 合并进那条
                earlier = seen_doi_in_batch.get(normalized)
                if earlier is not None:
                    result.merged += 1
                    continue
                seen_doi_in_batch[normalized] = item

            self._db.add(
                ReferenceLibraryItem(
                    user_id=user_id,
                    ref_id=item.id,
                    payload=item.model_dump(mode="json"),
                    title=item.title,
                    issued_year=item.issued_year,
                    doi=item.doi,
                )
            )
            seen_in_batch.add(item.id)
            result.created += 1

        if result.created:
            await self._db.flush()
            await self._db.commit()
        logger.info(
            "reference_library_saved",
            user_id=str(user_id),
            created=result.created,
            skipped=result.skipped,
            merged=result.merged,
            rejected=len(result.rejected),
        )
        return result

    @staticmethod
    def _fill_missing(row: ReferenceLibraryItem, item: CslItem) -> bool:
        """把 `item` 里**旧条目缺失**的字段补进去；返回是否有变化。

        为什么不直接覆盖：用户可能手动编辑过旧条目（改错字、补卷期），
        重复导入旧数据不该把他的修改冲掉。只补空位是安全侧。
        """
        current = CslItem.model_validate(row.payload)
        updates: dict[str, object] = {}
        for name in (
            "title",
            "container_title",
            "volume",
            "issue",
            "page",
            "publisher",
            "publisher_place",
            "issued_year",
            "doi",
            "url",
            "type",
            "language",
            "medium",
        ):
            old_value = getattr(current, name, None)
            new_value = getattr(item, name, None)
            if (old_value is None or old_value == "") and new_value not in (None, ""):
                updates[name] = new_value
        if not current.author and item.author:
            updates["author"] = [name.model_dump(mode="json") for name in item.author]
        if not updates:
            return False
        merged = current.model_copy(update=updates)
        row.payload = merged.model_dump(mode="json")
        # 冗余列同步（列表查询与排序读它们）
        row.title = merged.title
        row.issued_year = merged.issued_year
        row.doi = merged.doi
        return True

    async def _existing_by_normalized_doi(
        self, user_id: uuid.UUID, items: list[CslItem]
    ) -> dict[str, ReferenceLibraryItem]:
        """把该用户库里与入参 DOI 归一同值的条目查出来（一次查询）。"""
        wanted = {normalize_doi(item.doi) for item in items}
        wanted.discard(None)
        if not wanted:  # type: ignore[arg-type]
            return {}
        # DOI 列存的是原样值，无法在 SQL 里归一 → 取该用户的 doi 列再在内存里归一。
        # 引用库是 owner-scoped 且条目量级在千以内（个人文献库），
        # 这个取舍比加一个归一化列 + 迁移更简单，也不会漏判。
        stmt = select(ReferenceLibraryItem).where(
            ReferenceLibraryItem.user_id == user_id,
            ReferenceLibraryItem.doi.is_not(None),
        )
        rows = (await self._db.execute(stmt)).scalars().all()
        result: dict[str, ReferenceLibraryItem] = {}
        for row in rows:
            normalized = normalize_doi(row.doi)
            if normalized is not None and normalized in wanted:
                result[normalized] = row
        return result

    async def _existing_ref_ids(self, user_id: uuid.UUID, ref_ids: list[str]) -> set[str]:
        """查已存在的 `ref_id`（一次查询，避免逐条 SELECT）。"""
        if not ref_ids:
            return set()
        stmt = select(ReferenceLibraryItem.ref_id).where(
            ReferenceLibraryItem.user_id == user_id,
            ReferenceLibraryItem.ref_id.in_(ref_ids),
        )
        return set((await self._db.execute(stmt)).scalars().all())

    # ===== 查询 =====

    async def list_items(
        self,
        user_id: uuid.UUID,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ReferenceLibraryItem]:
        """列出条目。

        排序必须带 `id`：同事务批量插入的多行 `created_at` 完全相同，
        PostgreSQL 对并列行返回任意顺序（AGENTS §5 #14，已因此踩过两次）。
        """
        stmt = (
            select(ReferenceLibraryItem)
            .where(ReferenceLibraryItem.user_id == user_id)
            .order_by(
                ReferenceLibraryItem.created_at.desc(),
                ReferenceLibraryItem.id.desc(),
            )
            .limit(max(1, min(limit, 500)))
            .offset(max(0, offset))
        )
        return list((await self._db.execute(stmt)).scalars().all())

    async def count(self, user_id: uuid.UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(ReferenceLibraryItem)
            .where(ReferenceLibraryItem.user_id == user_id)
        )
        return int((await self._db.execute(stmt)).scalar_one())

    async def summary(self, user_id: uuid.UUID) -> LibrarySummary:
        """摘要：总条数 + 可作文献源的条数。

        入库准入已保证「能存进来的都能用」，所以两个数字当前相等；
        仍然分两个字段返回，是为了将来放宽准入（例如允许存「仅备查」条目）时
        前端不用改契约。
        """
        total = await self.count(user_id)
        stmt = (
            select(func.count())
            .select_from(ReferenceLibraryItem)
            .where(
                ReferenceLibraryItem.user_id == user_id,
                ReferenceLibraryItem.issued_year.is_not(None),
            )
        )
        usable = int((await self._db.execute(stmt)).scalar_one())
        return LibrarySummary(total=total, usable_as_source=usable)

    async def get_owned(self, user_id: uuid.UUID, item_id: uuid.UUID) -> ReferenceLibraryItem:
        """取当前用户拥有的条目；不存在或跨用户 → 404（同语义，不泄漏存在性）。"""
        stmt = select(ReferenceLibraryItem).where(
            ReferenceLibraryItem.id == item_id,
            ReferenceLibraryItem.user_id == user_id,
        )
        row = (await self._db.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise NotFoundException("引用库条目不存在")
        return row

    async def all_csl_items(self, user_id: uuid.UUID) -> list[CslItem]:
        """取出该用户全部条目并还原成 `CslItem`（供文献源使用）。

        分页拉取：单个用户的引用库可能上千条，一次性 `all()` 会占满内存。
        """
        items: list[CslItem] = []
        offset = 0
        page_size = 500
        while True:
            page = await self.list_items(user_id, limit=page_size, offset=offset)
            if not page:
                break
            items.extend(row.to_csl_item() for row in page)
            if len(page) < page_size:
                break
            offset += page_size
        return items

    # ===== 编辑 =====

    async def update(
        self, user_id: uuid.UUID, item_id: uuid.UUID, changes: dict[str, object]
    ) -> ReferenceLibraryItem:
        """部分更新条目；改完后**重新跑准入检查**。

        ## 为什么要重新准入

        准入规则（缺年份 / 缺 URL 且缺 DOI 不许入库）如果只在保存时检查，
        用户就能用 PATCH 把年份改成空 —— 库里出现一条**不可用**的条目，
        而 `usable_as_source` 仍把它算作可用。所以更新后必须再验一次，
        不通过就整体回滚（不留半改状态）。

        ## 为什么不能改 `ref_id`

        它是幂等键。改了会破坏「重复导入不重复入库」的语义，
        也会让 `same_work()` 的判重依据与库内数据不一致。端点层用
        `extra="forbid"` 拒绝，这里也不接受 `id`。
        """
        row = await self.get_owned(user_id, item_id)
        current = CslItem.model_validate(row.payload)
        if not changes:
            return row

        updated = current.model_copy(update=changes)
        reason = self.rejection_reason(updated)
        if reason is not None:
            raise ValidationException(f"修改后不符合入库条件：{reason}")

        row.payload = updated.model_dump(mode="json")
        row.title = updated.title
        row.issued_year = updated.issued_year
        row.doi = updated.doi
        await self._db.flush()
        await self._db.commit()
        logger.info(
            "reference_library_updated",
            user_id=str(user_id),
            item_id=str(item_id),
            ref_id=row.ref_id,
            fields=sorted(changes),
        )
        return row

    # ===== 删除 =====

    async def delete(self, user_id: uuid.UUID, item_id: uuid.UUID) -> None:
        """删除单条；跨用户 404。"""
        row = await self.get_owned(user_id, item_id)
        await self._db.delete(row)
        await self._db.flush()
        await self._db.commit()
        logger.info(
            "reference_library_deleted",
            user_id=str(user_id),
            item_id=str(item_id),
            ref_id=row.ref_id,
        )

    async def delete_all(self, user_id: uuid.UUID) -> int:
        """清空该用户的引用库（前端「清空」用）。返回删除条数。"""
        # 先数一遍再删：`Result.rowcount` 在 mypy 的类型里不存在
        # （`CursorResult` 才有），用 count 更明确也更可移植。
        total = await self.count(user_id)
        stmt = delete(ReferenceLibraryItem).where(ReferenceLibraryItem.user_id == user_id)
        await self._db.execute(stmt)
        await self._db.flush()
        await self._db.commit()
        return total
