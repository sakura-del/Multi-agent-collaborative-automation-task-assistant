"""引用库文献源测试（2026-10-26 引用库计划 §3.2）。

重点覆盖「不许造假」这条：
- 无年份条目**不能**变成 1970，必须被跳过；
- 无 URL 无 DOI 的条目必须被跳过；
- 关键词没命中时退回整库（用户选「用我的库」不是想看「没匹配到」）。
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.db.models  # noqa: F401  确保全部 ORM 注册到 Base.metadata
from app.db.base import Base
from app.knowledge.services.reference_library_service import ReferenceLibraryService
from app.tools.csl.models import CslItem, CslName
from app.tools.literature.library_source import (
    LibraryLiteratureSource,
    csl_item_to_record,
)
from app.tools.literature.models import LiteratureSearch

USER = uuid.UUID("33333333-3333-3333-3333-333333333333")


def csl_item(
    *,
    ref_id: str = "doi:10.1234/a",
    title: str = "检索增强生成评估方法综述",
    issued_year: int | None = 2024,
    doi: str | None = "10.1234/a",
    url: str | None = None,
) -> CslItem:
    return CslItem(
        id=ref_id,
        type="article-journal",
        title=title,
        author=[CslName(family="牛", given="耕田")],
        container_title="计算机学报",
        issued_year=issued_year,
        doi=doi,
        url=url,
        language="zh-CN",
    )


@pytest.fixture
async def session(tmp_path: object) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/lib.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


# ===== csl_item_to_record：不许造假 =====


def test_item_without_year_returns_none() -> None:
    """无年份 → None。**绝不能编 1970**：那会让按时间排序把无年份排最前，
    用户看到 1970 会以为数据错了。"""
    assert csl_item_to_record(csl_item(issued_year=None)) is None


def test_item_without_url_and_doi_returns_none() -> None:
    assert csl_item_to_record(csl_item(doi=None, url=None)) is None


def test_item_with_url_only_is_accepted() -> None:
    record = csl_item_to_record(csl_item(doi=None, url="https://example.org/p"))
    assert record is not None
    assert str(record.source_url).startswith("https://example.org/p")


def test_item_with_doi_only_derives_url() -> None:
    record = csl_item_to_record(csl_item(doi="10.1234/a", url=None))
    assert record is not None
    assert "10.1234/a" in str(record.source_url)


def test_published_at_uses_jan_first_of_issued_year() -> None:
    """只有年份时取当年 1 月 1 日：明确、可解释，不是随机值。"""
    record = csl_item_to_record(csl_item(issued_year=2024))
    assert record is not None
    assert record.published_at.year == 2024
    assert record.published_at.month == 1
    assert record.published_at.day == 1
    assert record.published_at.tzinfo == UTC


def test_abstract_is_empty_not_fabricated() -> None:
    """引用库只存元数据，没有摘要 —— 不能编一个假摘要。"""
    record = csl_item_to_record(csl_item())
    assert record is not None
    assert record.abstract == ""


# ===== LibraryLiteratureSource =====


async def test_source_returns_library_items(session: AsyncSession) -> None:
    service = ReferenceLibraryService(session)
    # ⚠️ 每条必须有不同 DOI：同 DOI 会被合并逻辑并成一条（遗留计划 §3.4）
    await service.save(
        USER,
        [
            csl_item(ref_id="doi:10.1/a", doi="10.1/a"),
            csl_item(ref_id="doi:10.1/b", doi="10.1/b"),
        ],
    )
    await session.commit()

    source = LibraryLiteratureSource(USER, session)
    records = await source.search(LiteratureSearch(query="检索", max_results=10))

    assert len(records) == 2
    assert {record.source_id for record in records} == {"doi:10.1/a", "doi:10.1/b"}


async def test_source_respects_max_results(session: AsyncSession) -> None:
    service = ReferenceLibraryService(session)
    await service.save(
        USER,
        [csl_item(ref_id=f"doi:10.1/{index}", doi=f"10.1/{index}") for index in range(5)],
    )
    await session.commit()

    source = LibraryLiteratureSource(USER, session)
    records = await source.search(LiteratureSearch(query="检索", max_results=2))

    assert len(records) == 2


async def test_source_falls_back_to_whole_library_on_query_miss(
    session: AsyncSession,
) -> None:
    """关键词没命中时退回整库。

    用户选「用我的引用库」是要用他的文献，不是要看「关键词没匹配到」。
    LLM 生成的查询词常常与库内表述不一致（例如查 "RAG evaluation" 而库里是中文题名）。
    """
    service = ReferenceLibraryService(session)
    await service.save(USER, [csl_item(title="完全无关的题名")])
    await session.commit()

    source = LibraryLiteratureSource(USER, session)
    records = await source.search(LiteratureSearch(query="RAG evaluation", max_results=10))

    assert len(records) == 1


async def test_source_matches_query_terms(session: AsyncSession) -> None:
    service = ReferenceLibraryService(session)
    await service.save(
        USER,
        [
            csl_item(ref_id="doi:10.1/rag", title="RAG evaluation methods"),
            csl_item(ref_id="doi:10.1/other", title="Unrelated topic"),
        ],
    )
    await session.commit()

    source = LibraryLiteratureSource(USER, session)
    records = await source.search(LiteratureSearch(query="RAG", max_results=10))

    assert [record.source_id for record in records] == ["doi:10.1/rag"]


async def test_source_is_owner_scoped(session: AsyncSession) -> None:
    other = uuid.UUID("44444444-4444-4444-4444-444444444444")
    service = ReferenceLibraryService(session)
    await service.save(USER, [csl_item(ref_id="doi:10.1/mine")])
    await service.save(other, [csl_item(ref_id="doi:10.1/theirs")])
    await session.commit()

    source = LibraryLiteratureSource(USER, session)
    # query 有 min_length=2（LiteratureSearch 契约）；用一定不命中的词，
    # 命中失败会退回整库 —— 正好验证「只返回自己的条目」
    records = await source.search(LiteratureSearch(query="zz", max_results=10))

    assert [record.source_id for record in records] == ["doi:10.1/mine"]


async def test_empty_library_returns_empty_list(session: AsyncSession) -> None:
    source = LibraryLiteratureSource(USER, session)
    assert await source.search(LiteratureSearch(query="ab", max_results=10)) == []


async def test_aclose_is_noop(session: AsyncSession) -> None:
    """与检索源接口对齐（容器会统一 aclose）。"""
    source = LibraryLiteratureSource(USER, session)
    assert await source.aclose() is None
