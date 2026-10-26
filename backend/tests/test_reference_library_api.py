"""引用库 API 契约测试（引用库计划 §3.5）。

覆盖：
- 全部端点要求鉴权（无 token → 401）；
- owner 隔离：A 的条目 B 看不到、删不掉（跨用户 404，不泄漏存在性）；
- 保存：幂等、部分成功如实报告（created/skipped/rejected 三个数字）；
- **全部被拒 → 400** 且逐条原因可读；
- 摘要：total 与 usable_as_source；
- 清空：只清自己的。

测试不依赖 Lead 的 `router.py`：用 `create_app()` 构造真实应用，只额外 include
本端点；DB 为临时文件型 SQLite，鉴权用最小依赖覆盖，**不产生外部依赖**。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.db.models  # noqa: F401  确保全部 ORM 注册到 Base.metadata
from app.api.deps import get_current_user
from app.api.v1.endpoints.reference_library import (
    router as reference_library_router,
)
from app.core.exceptions import UnauthorizedException
from app.db.base import Base
from app.db.session import get_db

USER_A = UUID("11111111-1111-1111-1111-111111111111")
USER_B = UUID("22222222-2222-2222-2222-222222222222")
TOKEN_A = "token-a"
TOKEN_B = "token-b"

API = "/api/v1/reference-library"


# ===== 素材 =====


def csl_payload(
    *,
    ref_id: str = "doi:10.1234/a",
    title: str = "检索增强生成评估方法综述",
    issued_year: int | None = 2024,
    doi: str | None = "10.1234/a",
    url: str | None = None,
) -> dict[str, Any]:
    """一条 `CslItem` 形态的载荷（与导入预览返回的结构一致）。"""
    return {
        "id": ref_id,
        "type": "article-journal",
        "title": title,
        "author": [{"family": "牛", "given": "耕田"}],
        "container_title": "计算机学报",
        "issued_year": issued_year,
        "doi": doi,
        "url": url,
        "language": "zh-CN",
    }


# ===== 夹具 =====


class _StubUser:
    def __init__(self, user_id: UUID) -> None:
        self.id = user_id
        self.is_active = True


@dataclass
class LibraryEnv:
    client: TestClient
    app: FastAPI


@pytest.fixture
def library_env(tmp_path: Path) -> Iterator[LibraryEnv]:
    from app.main import create_app

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'library.db'}",
        connect_args={"timeout": 10},
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    created = {"done": False}

    async def override_get_db() -> Any:
        if not created["done"]:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            created["done"] = True
        async with session_factory() as session:
            yield session

    async def override_get_current_user(request: Request) -> Any:
        header = request.headers.get("Authorization") or ""
        token = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
        if token == TOKEN_A:
            return _StubUser(USER_A)
        if token == TOKEN_B:
            return _StubUser(USER_B)
        raise UnauthorizedException("缺少有效的鉴权令牌")

    app: FastAPI = create_app()
    app.include_router(reference_library_router, prefix="/api/v1")
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user

    client = TestClient(app)
    client.__enter__()
    try:
        yield LibraryEnv(client=client, app=app)
    finally:
        client.__exit__(None, None, None)
        app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _save(env: LibraryEnv, items: list[dict[str, Any]], *, token: str = TOKEN_A) -> Any:
    return env.client.post(f"{API}", json={"items": items}, headers=_auth(token))


# ===== 鉴权 =====


def test_list_requires_auth(library_env: LibraryEnv) -> None:
    assert library_env.client.get(API).status_code == 401


def test_save_requires_auth(library_env: LibraryEnv) -> None:
    resp = library_env.client.post(API, json={"items": [csl_payload()]})
    assert resp.status_code == 401


def test_summary_requires_auth(library_env: LibraryEnv) -> None:
    assert library_env.client.get(f"{API}/summary").status_code == 401


# ===== 保存 =====


def test_save_returns_201_with_counts(library_env: LibraryEnv) -> None:
    resp = _save(library_env, [csl_payload()])

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["created"] == 1
    assert body["skipped"] == 0
    assert body["rejected"] == []


def test_save_is_idempotent(library_env: LibraryEnv) -> None:
    _save(library_env, [csl_payload()])
    second = _save(library_env, [csl_payload()])

    assert second.status_code == 201
    assert second.json()["created"] == 0
    assert second.json()["skipped"] == 1


def test_save_reports_partial_success(library_env: LibraryEnv) -> None:
    """部分成功：能进的进，不能进的带原因返回 —— 不能报全成功也不能报全失败。"""
    resp = _save(
        library_env,
        [
            csl_payload(ref_id="doi:10.1/ok"),
            csl_payload(ref_id="doi:10.1/no-year", issued_year=None),
        ],
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["created"] == 1
    assert len(body["rejected"]) == 1
    assert body["rejected"][0]["ref_id"] == "doi:10.1/no-year"
    assert "年份" in body["rejected"][0]["reason"]


def test_save_all_rejected_returns_400_with_reasons(library_env: LibraryEnv) -> None:
    """全部被拒 → 400 + 逐条原因（比 201 且 created=0 更清楚）。"""
    resp = _save(library_env, [csl_payload(issued_year=None)])

    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "年份" in detail


def test_save_empty_items_returns_400(library_env: LibraryEnv) -> None:
    assert _save(library_env, []).status_code == 400


def test_save_rejects_malformed_item(library_env: LibraryEnv) -> None:
    """结构不合法的条目（缺 title）→ 400 且原因可读。"""
    resp = _save(library_env, [{"id": "x", "type": "article-journal"}])

    assert resp.status_code == 400


# ===== 列表与隔离 =====


def test_list_returns_own_items_only(library_env: LibraryEnv) -> None:
    _save(library_env, [csl_payload(ref_id="doi:10.1/a")], token=TOKEN_A)
    _save(library_env, [csl_payload(ref_id="doi:10.1/b")], token=TOKEN_B)

    body_a = library_env.client.get(API, headers=_auth(TOKEN_A)).json()
    body_b = library_env.client.get(API, headers=_auth(TOKEN_B)).json()

    assert [item["ref_id"] for item in body_a["items"]] == ["doi:10.1/a"]
    assert [item["ref_id"] for item in body_b["items"]] == ["doi:10.1/b"]
    assert body_a["total"] == 1


def test_list_item_shape(library_env: LibraryEnv) -> None:
    _save(library_env, [csl_payload()])

    item = library_env.client.get(API, headers=_auth(TOKEN_A)).json()["items"][0]

    assert item["ref_id"] == "doi:10.1234/a"
    assert item["title"] == "检索增强生成评估方法综述"
    assert item["year"] == 2024
    assert item["doi"] == "10.1234/a"
    assert item["type"] == "article-journal"
    assert item["authors"] == ["牛耕田"]
    assert item["item_id"]


def test_list_pagination(library_env: LibraryEnv) -> None:
    # ⚠️ 每条必须有不同 DOI：同 DOI 会被合并逻辑并成一条（计划 §3.4）
    _save(
        library_env,
        [csl_payload(ref_id=f"doi:10.1/{index}", doi=f"10.1/{index}") for index in range(5)],
    )

    page = library_env.client.get(
        API, params={"limit": 2, "offset": 0}, headers=_auth(TOKEN_A)
    ).json()

    assert len(page["items"]) == 2
    assert page["total"] == 5


# ===== 摘要 =====


def test_summary_counts(library_env: LibraryEnv) -> None:
    _save(
        library_env,
        [
            csl_payload(ref_id="doi:10.1/a", doi="10.1/a"),
            csl_payload(ref_id="doi:10.1/b", doi="10.1/b"),
        ],
    )

    body = library_env.client.get(f"{API}/summary", headers=_auth(TOKEN_A)).json()

    assert body["total"] == 2
    assert body["usable_as_source"] == 2


def test_summary_is_owner_scoped(library_env: LibraryEnv) -> None:
    _save(library_env, [csl_payload()], token=TOKEN_A)

    body = library_env.client.get(f"{API}/summary", headers=_auth(TOKEN_B)).json()

    assert body["total"] == 0


# ===== 删除 =====


def test_delete_removes_item(library_env: LibraryEnv) -> None:
    _save(library_env, [csl_payload()])
    item_id = library_env.client.get(API, headers=_auth(TOKEN_A)).json()["items"][0]["item_id"]

    resp = library_env.client.delete(f"{API}/{item_id}", headers=_auth(TOKEN_A))

    assert resp.status_code == 204
    assert library_env.client.get(API, headers=_auth(TOKEN_A)).json()["total"] == 0


def test_delete_cross_user_is_404(library_env: LibraryEnv) -> None:
    """跨用户删除 → 404（与不存在同语义，不泄漏存在性）。"""
    _save(library_env, [csl_payload()])
    item_id = library_env.client.get(API, headers=_auth(TOKEN_A)).json()["items"][0]["item_id"]

    resp = library_env.client.delete(f"{API}/{item_id}", headers=_auth(TOKEN_B))

    assert resp.status_code == 404
    # 且 A 的条目仍在
    assert library_env.client.get(API, headers=_auth(TOKEN_A)).json()["total"] == 1


def test_delete_missing_is_404(library_env: LibraryEnv) -> None:
    resp = library_env.client.delete(
        f"{API}/00000000-0000-0000-0000-000000000000", headers=_auth(TOKEN_A)
    )
    assert resp.status_code == 404


def test_delete_malformed_uuid_is_422(library_env: LibraryEnv) -> None:
    resp = library_env.client.delete(f"{API}/not-a-uuid", headers=_auth(TOKEN_A))
    assert resp.status_code == 422


# ===== 清空 =====


def test_clear_only_affects_own_library(library_env: LibraryEnv) -> None:
    _save(library_env, [csl_payload(ref_id="doi:10.1/a")], token=TOKEN_A)
    _save(library_env, [csl_payload(ref_id="doi:10.1/b")], token=TOKEN_B)

    resp = library_env.client.delete(API, headers=_auth(TOKEN_A))

    assert resp.status_code == 200
    assert resp.json()["deleted"] == 1
    assert library_env.client.get(API, headers=_auth(TOKEN_A)).json()["total"] == 0
    assert library_env.client.get(API, headers=_auth(TOKEN_B)).json()["total"] == 1


# ===== 编辑（PATCH，计划 §3.5）=====


def _first_item_id(env: LibraryEnv, token: str = TOKEN_A) -> str:
    return env.client.get(API, headers=_auth(token)).json()["items"][0]["item_id"]


def test_patch_updates_title(library_env: LibraryEnv) -> None:
    _save(library_env, [csl_payload()])
    item_id = _first_item_id(library_env)

    resp = library_env.client.patch(
        f"{API}/{item_id}", json={"title": "改过的题名"}, headers=_auth(TOKEN_A)
    )

    assert resp.status_code == 200
    assert resp.json()["title"] == "改过的题名"
    # 其他字段不受影响
    assert resp.json()["year"] == 2024


def test_patch_is_partial(library_env: LibraryEnv) -> None:
    """只更新提供了的字段，没提的保持不变。"""
    _save(library_env, [csl_payload()])
    item_id = _first_item_id(library_env)

    resp = library_env.client.patch(
        f"{API}/{item_id}", json={"container_title": "新期刊"}, headers=_auth(TOKEN_A)
    )

    assert resp.status_code == 200
    assert resp.json()["container_title"] == "新期刊"
    assert resp.json()["title"] == "检索增强生成评估方法综述"


def test_patch_rejects_item_that_fails_admission(library_env: LibraryEnv) -> None:
    """把年份改成空 → 重新准入检查必须拒绝（否则库里出现不可用条目）。"""
    _save(library_env, [csl_payload()])
    item_id = _first_item_id(library_env)

    resp = library_env.client.patch(
        f"{API}/{item_id}", json={"issued_year": None}, headers=_auth(TOKEN_A)
    )

    assert resp.status_code == 400
    assert "年份" in resp.json()["detail"]
    # 且条目没被改动
    assert library_env.client.get(API, headers=_auth(TOKEN_A)).json()["items"][0]["year"] == 2024


def test_patch_cannot_change_ref_id(library_env: LibraryEnv) -> None:
    """`ref_id` 是幂等键，改它会破坏去重语义 → 契约层拒绝。"""
    _save(library_env, [csl_payload()])
    item_id = _first_item_id(library_env)

    resp = library_env.client.patch(
        f"{API}/{item_id}", json={"id": "doi:changed"}, headers=_auth(TOKEN_A)
    )

    assert resp.status_code == 422


def test_patch_cross_user_is_404(library_env: LibraryEnv) -> None:
    _save(library_env, [csl_payload()])
    item_id = _first_item_id(library_env)

    resp = library_env.client.patch(
        f"{API}/{item_id}", json={"title": "B 改的"}, headers=_auth(TOKEN_B)
    )

    assert resp.status_code == 404


def test_patch_requires_auth(library_env: LibraryEnv) -> None:
    _save(library_env, [csl_payload()])
    item_id = _first_item_id(library_env)

    resp = library_env.client.patch(f"{API}/{item_id}", json={"title": "x"})

    assert resp.status_code == 401


def test_patch_missing_is_404(library_env: LibraryEnv) -> None:
    resp = library_env.client.patch(
        f"{API}/00000000-0000-0000-0000-000000000000",
        json={"title": "x"},
        headers=_auth(TOKEN_A),
    )
    assert resp.status_code == 404


# ===== 合并计数（计划 §3.4）=====


def test_save_reports_merged_separately_from_skipped(library_env: LibraryEnv) -> None:
    """`merged` 与 `skipped` 是两种不同的去重，必须分开报告。"""
    _save(library_env, [csl_payload(ref_id="doi:10.1234/A", doi="10.1234/A")])

    # 同 DOI 不同写法 → merged
    body = _save(
        library_env, [csl_payload(ref_id="doi:10.1234/a", doi="https://doi.org/10.1234/a")]
    ).json()
    assert body["merged"] == 1
    assert body["skipped"] == 0
    assert body["created"] == 0

    # ref_id 完全相同 → skipped
    body = _save(library_env, [csl_payload(ref_id="doi:10.1234/A", doi="10.1234/A")]).json()
    assert body["skipped"] == 1
    assert body["merged"] == 0


def test_save_merged_only_is_not_400(library_env: LibraryEnv) -> None:
    """合并成功算成功，不能报 400。"""
    _save(library_env, [csl_payload(ref_id="doi:10.1234/A", doi="10.1234/A")])

    resp = library_env.client.post(
        API,
        json={"items": [csl_payload(ref_id="doi:10.1234/a", doi="10.1234/a")]},
        headers=_auth(TOKEN_A),
    )

    assert resp.status_code == 201
    assert resp.json()["merged"] == 1
