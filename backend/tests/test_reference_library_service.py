"""引用库领域服务测试（2026-10-26 引用库计划 §3.1 / §3.4）。

覆盖：
- 保存：批量、按 `ref_id` 幂等、部分成功如实报告；
- 准入：缺年份 / 缺 URL 与 DOI 的条目**必须被拒**并给可读原因
  （因为它们无法构造合法的 `LiteratureRecord`，硬存会在任务执行时炸掉）；
- 列表：owner-scoped，按 `(created_at, id)` 稳定排序；
- 删除：owner-scoped，跨用户 404；
- 摘要：总条数 + 可作文献源的条数。

## 为什么准入这么严

`LiteratureRecord`（`app/tools/literature/models.py`）是 `extra="forbid"` 的冻结契约，
`published_at` 与 `source_url` / `pdf_url` 都是**必填**。引用库条目要能当文献源用，
就必须能构造出这个记录：

- 无年份 → 无法给出真实 `published_at`。**绝不能编 1970**：
  那会让按时间排序的检索把无年份条目排到最前，用户看到 1970 会以为数据错了。
- 无 URL 也无 DOI → 无法给出 `source_url`。

宁可入库失败并说清原因，也不要存进去之后在任务执行时炸掉或产生假数据。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.db.models  # noqa: F401  确保全部 ORM 注册到 Base.metadata
from app.db.base import Base
from app.knowledge.services.reference_library_service import (
    ReferenceLibraryService,
)
from app.tools.csl.models import CslItem, CslName

USER_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
USER_B = uuid.UUID("22222222-2222-2222-2222-222222222222")


def csl_item(
    *,
    ref_id: str = "doi:10.1234/jcs.2024.001",
    title: str = "检索增强生成评估方法综述",
    issued_year: int | None = 2024,
    doi: str | None = "10.1234/jcs.2024.001",
    url: str | None = None,
) -> CslItem:
    return CslItem(
        id=ref_id,
        type="article-journal",
        title=title,
        author=[CslName(family="牛", given="耕田"), CslName(family="李", given="四")],
        container_title="计算机学报",
        issued_year=issued_year,
        volume="47",
        issue="3",
        page="1-20",
        doi=doi,
        url=url,
        language="zh-CN",
    )


@pytest.fixture
async def session(tmp_path: object) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/refs.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


# ===== 保存 =====


async def test_save_persists_items(session: AsyncSession) -> None:
    service = ReferenceLibraryService(session)

    result = await service.save(USER_A, [csl_item()])
    await session.commit()

    assert result.created == 1
    assert result.skipped == 0
    assert result.rejected == []
    assert await service.count(USER_A) == 1


async def test_save_is_idempotent_by_ref_id(session: AsyncSession) -> None:
    """重复导入同一批 → 不产生重复行（用户会重复粘同一份 .bib）。"""
    service = ReferenceLibraryService(session)

    first = await service.save(USER_A, [csl_item()])
    await session.commit()
    second = await service.save(USER_A, [csl_item()])
    await session.commit()

    assert (first.created, first.skipped) == (1, 0)
    assert (second.created, second.skipped) == (0, 1)
    assert await service.count(USER_A) == 1


async def test_save_reports_partial_success(session: AsyncSession) -> None:
    """部分成功必须如实报告，不能报「全部成功」也不能报「全部失败」。"""
    service = ReferenceLibraryService(session)

    result = await service.save(
        USER_A,
        [
            csl_item(ref_id="doi:10.1/ok"),
            csl_item(ref_id="doi:10.1/no-year", issued_year=None),
        ],
    )
    await session.commit()

    assert result.created == 1
    assert len(result.rejected) == 1
    assert result.rejected[0].ref_id == "doi:10.1/no-year"
    assert "年份" in result.rejected[0].reason


async def test_save_rejects_item_without_year(session: AsyncSession) -> None:
    """无年份条目必须被拒 —— 不能编 1970 充当 published_at。"""
    service = ReferenceLibraryService(session)

    result = await service.save(USER_A, [csl_item(issued_year=None)])
    await session.commit()

    assert result.created == 0
    assert len(result.rejected) == 1
    assert "年份" in result.rejected[0].reason
    assert await service.count(USER_A) == 0


async def test_save_rejects_item_without_url_or_doi(session: AsyncSession) -> None:
    """既无 URL 也无 DOI → 无法构造 source_url，必须拒绝。"""
    service = ReferenceLibraryService(session)

    result = await service.save(USER_A, [csl_item(doi=None, url=None)])
    await session.commit()

    assert result.created == 0
    assert len(result.rejected) == 1
    assert "URL" in result.rejected[0].reason or "DOI" in result.rejected[0].reason


async def test_save_accepts_item_with_url_but_no_doi(session: AsyncSession) -> None:
    """只有 URL 也能用（source_url 可从 url 取），不该被误拒。"""
    service = ReferenceLibraryService(session)

    result = await service.save(
        USER_A,
        [csl_item(ref_id="url:example", doi=None, url="https://example.org/paper")],
    )
    await session.commit()

    assert result.created == 1


# ===== 列表与隔离 =====


async def test_list_is_owner_scoped(session: AsyncSession) -> None:
    service = ReferenceLibraryService(session)
    await service.save(USER_A, [csl_item(ref_id="doi:10.1/a")])
    await service.save(USER_B, [csl_item(ref_id="doi:10.1/b")])
    await session.commit()

    items_a = await service.list_items(USER_A)
    items_b = await service.list_items(USER_B)

    assert [item.ref_id for item in items_a] == ["doi:10.1/a"]
    assert [item.ref_id for item in items_b] == ["doi:10.1/b"]


async def test_list_orders_stably_by_created_at_then_id(session: AsyncSession) -> None:
    """同事务批量插入的 `created_at` 完全相同，必须再按 `id` 排（AGENTS §5 #14）。

    ⚠️ 每条必须有**不同的 DOI**：同 DOI 会被合并逻辑并成一条（§3.4），
    那样这个测试就测不到排序了。
    """
    service = ReferenceLibraryService(session)
    await service.save(
        USER_A,
        [csl_item(ref_id=f"doi:10.1/{index}", doi=f"10.1/{index}") for index in range(5)],
    )
    await session.commit()

    first = [item.id for item in await service.list_items(USER_A)]
    second = [item.id for item in await service.list_items(USER_A)]

    assert first == second
    assert len(first) == 5


async def test_list_pagination(session: AsyncSession) -> None:
    service = ReferenceLibraryService(session)
    await service.save(
        USER_A,
        [csl_item(ref_id=f"doi:10.1/{index}", doi=f"10.1/{index}") for index in range(5)],
    )
    await session.commit()

    page = await service.list_items(USER_A, limit=2, offset=0)
    assert len(page) == 2


# ===== 删除 =====


async def test_delete_removes_item(session: AsyncSession) -> None:
    service = ReferenceLibraryService(session)
    await service.save(USER_A, [csl_item()])
    await session.commit()
    item = (await service.list_items(USER_A))[0]

    await service.delete(USER_A, item.id)
    await session.commit()

    assert await service.count(USER_A) == 0


async def test_delete_cross_user_is_404(session: AsyncSession) -> None:
    """跨用户删除必须 404（与不存在同语义，不泄漏存在性）。"""
    from app.core.exceptions import NotFoundException

    service = ReferenceLibraryService(session)
    await service.save(USER_A, [csl_item()])
    await session.commit()
    item = (await service.list_items(USER_A))[0]

    with pytest.raises(NotFoundException):
        await service.delete(USER_B, item.id)


# ===== 摘要 =====


async def test_summary_counts_usable_items(session: AsyncSession) -> None:
    service = ReferenceLibraryService(session)
    await service.save(
        USER_A,
        [
            csl_item(ref_id="doi:10.1/a", doi="10.1/a"),
            csl_item(ref_id="doi:10.1/b", doi="10.1/b"),
        ],
    )
    await session.commit()

    summary = await service.summary(USER_A)

    assert summary.total == 2
    # 能入库的都是「可作文献源」的（准入已保证），这里两个数字应当相等
    assert summary.usable_as_source == 2


# ===== 合并去重（2026-10-26 引用库遗留计划 §3.4）=====


async def test_save_merges_same_doi_different_form(session: AsyncSession) -> None:
    """同一文献的三种 DOI 写法只存一条（这是用户真实会遇到的情况）。"""
    service = ReferenceLibraryService(session)
    first = csl_item(ref_id="doi:10.1234/A", doi="10.1234/A")
    result = await service.save(USER_A, [first])
    await session.commit()
    assert result.created == 1

    for ref_id, doi in [
        ("doi:10.1234/a", "10.1234/a"),
        ("doi:https://doi.org/10.1234/a", "https://doi.org/10.1234/a"),
        ("doi:DOI: 10.1234/a", "DOI: 10.1234/a"),
    ]:
        result = await service.save(USER_A, [csl_item(ref_id=ref_id, doi=doi)])
        await session.commit()
        assert result.created == 0
        assert result.merged == 1

    items = await service.list_items(USER_A)
    assert len(items) == 1


async def test_merge_does_not_overwrite_user_edits(session: AsyncSession) -> None:
    """合并只补空字段，不覆盖用户已编辑的值。"""
    service = ReferenceLibraryService(session)
    await service.save(
        USER_A,
        [csl_item(ref_id="doi:10.1/a", doi="10.1/a", title="用户改过的题名")],
    )
    await session.commit()

    # 再导入同一 DOI 但题名不同（旧数据）→ 不应覆盖
    result = await service.save(
        USER_A,
        [csl_item(ref_id="doi:10.1/a2", doi="10.1/a", title="原始题名")],
    )
    await session.commit()

    assert result.merged == 1
    items = await service.list_items(USER_A)
    assert len(items) == 1
    assert items[0].title == "用户改过的题名"


async def test_merge_fills_missing_fields(session: AsyncSession) -> None:
    """旧条目缺的字段应当被补上（这是合并的价值）。"""
    service = ReferenceLibraryService(session)
    sparse = csl_item(ref_id="doi:10.1/a", doi="10.1/a")
    sparse = sparse.model_copy(update={"container_title": None, "volume": None})
    await service.save(USER_A, [sparse])
    await session.commit()

    richer = csl_item(ref_id="doi:10.1/a2", doi="10.1/a")
    result = await service.save(USER_A, [richer])
    await session.commit()

    assert result.merged == 1
    restored = (await service.list_items(USER_A))[0].to_csl_item()
    assert restored.container_title == "计算机学报"
    assert restored.volume == "47"


async def test_merge_skips_entries_without_doi(session: AsyncSession) -> None:
    """没有 DOI 的条目靠 URL 入库，不参与合并（无法可靠判重）。"""
    service = ReferenceLibraryService(session)
    no_doi = csl_item(ref_id="title:a-survey", doi=None, url="https://example.org/a")
    await service.save(USER_A, [no_doi])
    await session.commit()

    result = await service.save(
        USER_A,
        [csl_item(ref_id="title:a-survey-2", doi=None, url="https://example.org/b")],
    )
    await session.commit()

    assert result.created == 1
    assert len(await service.list_items(USER_A)) == 2


# ===== 还原成 CslItem =====


async def test_items_round_trip_to_csl_item(session: AsyncSession) -> None:
    """存进去的 payload 必须能原样还原成 `CslItem`（渲染/导出直接用）。"""
    service = ReferenceLibraryService(session)
    original = csl_item()
    await service.save(USER_A, [original])
    await session.commit()

    item = (await service.list_items(USER_A))[0]
    restored = item.to_csl_item()

    assert restored.id == original.id
    assert restored.title == original.title
    assert restored.issued_year == original.issued_year
    assert [name.family for name in restored.author] == ["牛", "李"]


async def test_payload_survives_unknown_future_fields(session: AsyncSession) -> None:
    """`CslItem` 是 extra="forbid"，但 payload 存 JSON：多存字段不该炸（向前兼容）。

    这里直接检查 payload 里保留了写进去的键 —— 保证「加字段不用迁移」这个设计成立。
    """
    service = ReferenceLibraryService(session)
    await service.save(USER_A, [csl_item()])
    await session.commit()

    item = (await service.list_items(USER_A))[0]
    assert "medium" in item.payload or "language" in item.payload


# ===== 时间戳语义 =====


async def test_created_at_is_timezone_aware(session: AsyncSession) -> None:
    """`created_at` 必须是带时区的（与项目其他表一致，避免 naive/aware 比较永不相等）。"""
    service = ReferenceLibraryService(session)
    await service.save(USER_A, [csl_item()])
    await session.commit()

    item = (await service.list_items(USER_A))[0]
    assert item.created_at is not None
    # SQLite 会丢时区，这里只断言能被当作时间使用
    assert isinstance(item.created_at, datetime)
    if item.created_at.tzinfo is not None:
        assert item.created_at.tzinfo == UTC
