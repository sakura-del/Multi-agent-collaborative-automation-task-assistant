"""fulltext_cache 模型测试（Issue #73 Phase A · Task 2）。

## 本文件锁定

1. 列齐全：DOI / arXiv / PMID / sha256 四个业务去重键 + `parsed_sections` / `paper_card`；
2. py 侧默认值（`id=uuid4`、`status="active"`、`created_at=now`）在真实写入时生效；
3. `parsed_sections` 的 JSON 载荷可原样持久化。

## 为什么不在构造后直接断言

`mapped_column(default=...)` 是 **ColumnDefault**，在 flush（INSERT）时才执行：
`FulltextCache(...)` 刚构造完时 `id` / `status` 仍是 `None`。因此这里走
`add` + `flush` 的真实调用序列再断言，否则断言的是错误的心智模型
（构造期就期望默认值会产生假测试）。
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FulltextCache

_SECTIONS = [
    {
        "title": "1 Introduction",
        "role": "background",
        "page_start": 1,
        "page_end": 2,
        "text": "...",
    }
]


async def test_fulltext_cache_columns_and_defaults(db_session: AsyncSession) -> None:
    row = FulltextCache(
        doi="10.1234/abc",
        arxiv_id=None,
        pmid=None,
        sha256="a" * 64,
        provider="arxiv_pdf",
        source_url="https://arxiv.org/pdf/2401.00001",
        license="arxiv",
        content_format="pdf",
        page_count=12,
        parsed_sections=_SECTIONS,
        paper_card=None,
    )
    db_session.add(row)
    # id / status / created_at 均为 ColumnDefault，flush 时才落值
    await db_session.flush()

    assert isinstance(row.id, uuid.UUID)
    assert row.status == "active"
    assert row.created_at is not None
    assert row.page_count == 12
    assert row.parsed_sections == _SECTIONS
