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

from app.core.exceptions import NotFoundException
from app.core.logging import get_logger
from app.db.models.reference_library import ReferenceLibraryItem
from app.tools.csl.models import CslItem

logger = get_logger(__name__)

__all__ = [
    "LibrarySummary",
    "ReferenceLibraryService",
    "RejectedItem",
    "SaveResult",
]


@dataclass(frozen=True)
class RejectedItem:
    """被拒绝入库的条目（带可读原因，前端要逐条展示）。"""

    ref_id: str
    title: str
    reason: str


@dataclass
class SaveResult:
    """保存结果：**必须能表达部分成功**。

    只有 `created` / `skipped` / `rejected` 三个数字都在，
    用户才知道「哪些进去了、哪些本来就在、哪些没进去以及为什么」。
    """

    created: int = 0
    skipped: int = 0
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
        """批量保存；按 `ref_id` 幂等（已存在的计入 `skipped`）。

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

        seen_in_batch: set[str] = set()
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
            rejected=len(result.rejected),
        )
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
