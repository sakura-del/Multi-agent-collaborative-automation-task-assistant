"""上传文档 ↔ 引用库条目匹配（Issue #73 Phase B / Task B1）。

为什么需要这层：`KnowledgeDoc.meta_info` 记录 `ref_id` / `doi` 之后，Phase B 的
「全文补全」才能把 OA 正文挂到引用库里那一条文献上，而不是在知识库里多出一篇
看不出出处的文档。

匹配顺序（计划 Task B1）：`ref_id` 精确 → 归一化 DOI → 归一化标题。

为什么标题只在「归一化长度 ≥20 且候选唯一」时生效：标题是弱标识。短标题
（"A Survey"、"Introduction"）和同名文献（会议版 / 期刊版、转载）都很常见，
一旦误关联，后续取全文可能把 A 论文的正文当成 B 论文的证据 —— 关联错了比不
关联更糟（与引用库判重 `same_work` 只认 DOI 的取舍同源）。
"""

from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.reference_library import ReferenceLibraryItem
from app.knowledge.services.reference_library_service import normalize_doi

__all__ = ["ReferenceLinker", "doi_from_ref_id", "normalize_doi", "normalize_title"]

#: 标题归一：去掉标点与空白（保留 `\w` 与 CJK），用于宽松比较。
_PUNCT_RE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)

#: 归一化标题的最短长度；低于它的标题区分度不足，不参与关联（计划 Task B1）。
_MIN_TITLE_LENGTH = 20


def normalize_title(raw: str | None) -> str | None:
    """题名归一（用于上传 ↔ 引用库匹配）；归一化后短于 20 字符返回 None。

    与 `app.tools.csl.models.normalize_title`（构造 ref_id 用）不同：这里必须
    **去掉标点与空白**，否则 "RAG: A Survey" 与 "rag a survey" 因冒号差异比较
    不等；长度门槛是弱标识的防误关联策略。
    """
    if not raw:
        return None
    value = _PUNCT_RE.sub("", raw).lower()
    return value if len(value) >= _MIN_TITLE_LENGTH else None


def doi_from_ref_id(ref_id: str | None) -> str | None:
    """从 `doi:<DOI>` 形式的 ref_id 提取归一化 DOI；其它 ref_id 返回 None。

    ref_id 形态由 `app.tools.csl.models.build_ref_id` 约定（DOI → src: → title:）。
    上传方显式带 `ref_id="doi:10.1234/abc"` 时同时当作 DOI 提示：引用库里那条
    文献的 ref_id 与上传方不完全一致（历史条目可能是 `src:`/`title:`）时，仍能
    按 DOI 命中；而 `src:`/`title:` 形态的 ref_id 绝不能写进 `meta_info["doi"]`。
    """
    if ref_id is None or not ref_id.strip().lower().startswith("doi:"):
        return None
    return normalize_doi(ref_id)


class ReferenceLinker:
    """把上传提示（ref_id / DOI / 标题）解析为**当前用户**的引用库条目。"""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def match(
        self,
        user_id: UUID,
        *,
        ref_id: str | None,
        doi: str | None,
        title: str | None,
    ) -> ReferenceLibraryItem | None:
        """按 `ref_id` 精确 → 归一化 DOI → 归一化标题的顺序匹配；无命中返回 None。

        全部查询都带 `user_id` 过滤：引用库是 owner-scoped，跨用户关联会把别人
        的文献当成自己的证据来源。
        """
        if ref_id:
            item = await self._by_ref_id(user_id, ref_id)
            if item is not None:
                return item

        normalized_doi = normalize_doi(doi)
        normalized_title = normalize_title(title)
        if normalized_doi is None and normalized_title is None:
            return None

        items = await self._user_items(user_id)
        if normalized_doi is not None:
            for item in items:
                if normalize_doi(item.doi) == normalized_doi:
                    return item
        if normalized_title is not None:
            candidates = [item for item in items if normalize_title(item.title) == normalized_title]
            # 同名文献不止一条时无法判定是哪一篇，返回 None 而不是赌一条
            if len(candidates) == 1:
                return candidates[0]
        return None

    async def _by_ref_id(self, user_id: UUID, ref_id: str) -> ReferenceLibraryItem | None:
        stmt = select(ReferenceLibraryItem).where(
            ReferenceLibraryItem.user_id == user_id,
            ReferenceLibraryItem.ref_id == ref_id,
        )
        return (await self._db.execute(stmt)).scalars().first()

    async def _user_items(self, user_id: UUID) -> list[ReferenceLibraryItem]:
        """取该用户的全部引用库条目（DOI 列存原样值，无法在 SQL 里归一）。

        与 `ReferenceLibraryService._existing_by_normalized_doi` 同一取舍：引用库
        owner-scoped 且条目量级在千以内（个人文献库），一次取回内存比较比加归一化
        列 + 迁移更简单，也不会漏判。
        """
        stmt = select(ReferenceLibraryItem).where(ReferenceLibraryItem.user_id == user_id)
        return list((await self._db.execute(stmt)).scalars().all())
