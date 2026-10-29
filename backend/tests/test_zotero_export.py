"""Zotero 条目 → BibTeX 转换与只读 Local API 导入测试（计划 Task 4 / Task 5）。

脚本位于仓库根 ``scripts/zotero_export.py``（不在 ``backend`` 包内），
因此用 importlib 按绝对路径加载；产物必须能被现有
:func:`app.tools.csl.importers.parse_bibtex` 直接解析。

Local API 测试全部走 ``httpx.MockTransport``，**零真实网络**：
既保证 CI 不依赖本机 Zotero，也保证不会把 fixture 当成真实 Zotero 验证。
"""

import importlib.util
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.db.models  # noqa: F401  确保全部 ORM 注册到 Base.metadata
from app.db.base import Base
from app.knowledge.services.reference_library_service import ReferenceLibraryService
from app.tools.csl.importers import parse_bibtex

ROOT = Path(__file__).parents[2]
spec = importlib.util.spec_from_file_location("zotero_export", ROOT / "scripts/zotero_export.py")
zotero_export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(zotero_export)


def test_items_to_bibtex_preserves_metadata_and_escapes() -> None:
    items = [
        {
            "key": "ABC123",
            "data": {
                "itemType": "journalArticle",
                "title": "Evidence {RAG} 100%",
                "creators": [{"firstName": "Wei", "lastName": "Zhang"}],
                "date": "2024-01-02",
                "DOI": "10.1234/ABC",
                "url": "https://example.org/paper",
            },
        }
    ]

    text = zotero_export.items_to_bibtex(items)

    assert text.startswith("@article{doi:10.1234/abc,")
    assert "title={Evidence \\{RAG\\} 100\\%}" in text
    assert "author={Zhang, Wei}" in text
    assert "year={2024}" in text
    assert "doi={10.1234/abc}" in text
    assert "url={https://example.org/paper}" in text
    assert parse_bibtex(text)[0].title == "Evidence {RAG} 100%"


def test_unknown_type_does_not_fabricate_year() -> None:
    items = [{"key": "X1", "data": {"itemType": "unknownType", "title": "No year"}}]

    text = zotero_export.items_to_bibtex(items)

    assert text.startswith("@misc{zotero:X1,")
    assert "1970" not in text


def test_export_is_parseable_by_existing_importer() -> None:
    items = [
        {
            "key": "ABC123",
            "data": {
                "itemType": "journalArticle",
                "title": "Evidence RAG",
                "creators": [{"firstName": "Wei", "lastName": "Zhang"}],
                "date": "2024-01-02",
                "DOI": "10.1234/ABC",
            },
        }
    ]

    text = zotero_export.items_to_bibtex(items)
    parsed = parse_bibtex(text)

    assert len(parsed) == 1
    assert parsed[0].doi == "10.1234/abc"
    assert parsed[0].issued_year == 2024
    assert parsed[0].title == "Evidence RAG"


# ===== Local API（MockTransport，零真实网络）=====


def _zotero_client(handler) -> httpx.Client:
    """构造只连本机默认地址的 mock 客户端；绝不发真实请求。"""
    return httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url="http://localhost:23119/api",
    )


def test_cli_reads_collection_and_writes_parseable_bibtex(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/":
            return httpx.Response(200, headers={"Zotero-API-Version": "3"}, json={})
        if request.url.path == "/api/users/0/collections/ABC/items":
            return httpx.Response(
                200,
                json=[
                    {
                        "key": "ABC123",
                        "data": {
                            "itemType": "journalArticle",
                            "title": "Evidence RAG",
                            "creators": [{"firstName": "Wei", "lastName": "Zhang"}],
                            "date": "2024-01-02",
                            "DOI": "10.1234/ABC",
                        },
                    }
                ],
            )
        if request.url.path == "/api/users/0/collections":
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    output = tmp_path / "zotero.bib"
    with _zotero_client(handler) as client:
        exit_code = zotero_export.main(
            ["--collection", "ABC", "--output", str(output)],
            client=client,
        )

    assert exit_code == 0
    parsed = parse_bibtex(output.read_text(encoding="utf-8"))
    assert len(parsed) == 1
    assert parsed[0].doi == "10.1234/abc"


def test_client_paginates_collection_items_with_start_and_limit() -> None:
    """分页必须由服务端 `start`/`limit` 驱动，不能只靠返回条数猜。"""
    requests: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(dict(request.url.params))
        start = int(request.url.params.get("start", "0"))
        page = [
            {"key": f"K{start + 1}", "data": {"itemType": "misc", "title": "A"}},
            {"key": f"K{start + 2}", "data": {"itemType": "misc", "title": "B"}},
        ]
        return httpx.Response(200, json=page if start == 0 else page[:1])

    with _zotero_client(handler) as http_client:
        client = zotero_export.ZoteroLocalClient(http_client, page_size=2)
        items = client.collection_items("ABC")

    assert [item["key"] for item in items] == ["K1", "K2", "K3"]
    assert requests == [
        {"limit": "2", "start": "0"},
        {"limit": "2", "start": "2"},
    ]


def test_cli_rejects_disabled_local_api(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403)

    output = tmp_path / "disabled.bib"
    with _zotero_client(handler) as client:
        exit_code = zotero_export.main(["--output", str(output)], client=client)

    assert exit_code == 1
    assert not output.exists()
    assert "未启用" in capsys.readouterr().err


def test_cli_rejects_empty_collection(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/":
            return httpx.Response(200, headers={"Zotero-API-Version": "3"}, json={})
        if request.url.path in {
            "/api/users/0/collections",
            "/api/users/0/collections/EMPTY/items",
        }:
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    output = tmp_path / "empty.bib"
    with _zotero_client(handler) as client:
        exit_code = zotero_export.main(
            ["--collection", "EMPTY", "--output", str(output)],
            client=client,
        )

    assert exit_code == 1
    assert not output.exists()
    assert "EMPTY" in capsys.readouterr().err


def _success_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/":
        return httpx.Response(200, headers={"Zotero-API-Version": "3"}, json={})
    if request.url.path == "/api/users/0/items":
        return httpx.Response(
            200,
            json=[{"key": "K1", "data": {"itemType": "misc", "title": "A"}}],
        )
    return httpx.Response(404)


def test_cli_never_overwrites_existing_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "existing.bib"
    output.write_text("用户手工整理过的内容", encoding="utf-8")

    with _zotero_client(_success_handler) as client:
        exit_code = zotero_export.main(["--output", str(output)], client=client)

    assert exit_code == 1
    assert output.read_text(encoding="utf-8") == "用户手工整理过的内容"
    assert capsys.readouterr().err


def test_cli_rejects_export_over_one_megabyte(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/":
            return httpx.Response(200, headers={"Zotero-API-Version": "3"}, json={})
        if request.url.path == "/api/users/0/items":
            return httpx.Response(
                200,
                json=[
                    {
                        "key": "HUGE",
                        "data": {"itemType": "misc", "title": "A" * (1024 * 1024)},
                    }
                ],
            )
        return httpx.Response(404)

    output = tmp_path / "huge.bib"
    with _zotero_client(handler) as client:
        exit_code = zotero_export.main(["--output", str(output)], client=client)

    assert exit_code == 1
    assert not output.exists()
    assert "1 MB" in capsys.readouterr().err


# ===== 与现有引用库集成（独立临时 SQLite，不共享其他测试文件状态）=====

USER_A = uuid.UUID("11111111-1111-1111-1111-111111111111")


@pytest.fixture
async def session(tmp_path: Path) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/refs.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


async def test_zotero_bibtex_uses_existing_save_and_idempotency(
    session: AsyncSession,
) -> None:
    """Zotero 导出的 BibTeX 必须直接复用现有 `ReferenceLibraryService.save()`。

    不另写去重/准入：同一份导出第二次导入按既有 `ref_id` 幂等规则 skipped。
    """
    service = ReferenceLibraryService(session)
    items = parse_bibtex(
        zotero_export.items_to_bibtex(
            [
                {
                    "key": "ABC123",
                    "data": {
                        "itemType": "journalArticle",
                        "title": "Evidence RAG",
                        "creators": [{"firstName": "Wei", "lastName": "Zhang"}],
                        "date": "2024-01-02",
                        "DOI": "10.1234/ABC",
                    },
                }
            ]
        )
    )

    first = await service.save(USER_A, items)
    await session.commit()
    second = await service.save(USER_A, items)
    await session.commit()

    assert first.created == 1
    assert second.created == 0
    assert second.skipped == 1
    assert await service.count(USER_A) == 1


async def test_zotero_bibtex_missing_year_uses_existing_rejection(
    session: AsyncSession,
) -> None:
    """缺年份沿用现有准入：拒绝并给可读原因，**不伪造 1970**。"""
    service = ReferenceLibraryService(session)
    text = zotero_export.items_to_bibtex(
        [
            {
                "key": "NODATE",
                "data": {
                    "itemType": "journalArticle",
                    "title": "Preprint without date",
                    "creators": [{"firstName": "Wei", "lastName": "Zhang"}],
                    "DOI": "10.1234/NODATE",
                },
            }
        ]
    )
    assert "1970" not in text

    result = await service.save(USER_A, parse_bibtex(text))
    await session.commit()

    assert result.created == 0
    assert len(result.rejected) == 1
    assert "年份" in result.rejected[0].reason
    assert await service.count(USER_A) == 0
