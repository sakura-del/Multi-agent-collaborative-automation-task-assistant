"""Zotero Web API 条目与附件同步测试（Phase B Task B3）。

全部用 `httpx.MockTransport` 模拟 Zotero 上游，**不联网**：

- 条目归一化为 `CslItem` 并写入引用库（同 DOI / 同标题幂等跳过）；
- `itemType=attachment` 且 `contentType=application/pdf` → 下载并走统一入库
  （`origin="zotero"`、`ref_id` 绑定父条目）；
- 附件 >30MB 跳过并计入 `skipped`；单个附件失败只跳过该附件，不影响整批；
- 凭据缺失 → 404 可读错误；凭据解密失败 → 401 可读错误；凭据内容不合法 → 422；
- 跨用户隔离：A 的 Zotero 条目不会出现在 B 的引用库；
- `provider="zotero"` 可通过既有 data-source-credentials 端点保存/查询，
  且**默认清单不变**（未配置时不出现，避免改动既有契约）。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.db.models  # noqa: F401  # 注册全部 ORM 模型（create_all 需要）
from app.api.deps import get_current_user, get_current_user_or_api_key
from app.api.v1.endpoints import data_source_credentials as credentials_endpoint
from app.api.v1.endpoints import reference_library as reference_endpoint
from app.api.v1.endpoints.reference_library import get_zotero_sync_service
from app.core.exceptions import AppException, UnauthorizedException
from app.db.base import Base
from app.db.models.data_source_credential import DataSourceCredential
from app.db.session import get_db
from app.knowledge.models.document import ChunkStrategy
from app.security.credential_cipher import CredentialCipher
from app.tools.literature.zotero_client import (
    MAX_ATTACHMENT_BYTES,
    ZoteroAuthError,
    ZoteroClient,
)
from app.tools.literature.zotero_sync import ZoteroSyncService

USER_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
USER_B = uuid.UUID("22222222-2222-2222-2222-222222222222")
TOKEN_A = "token-a"
TOKEN_B = "token-b"

API_PREFIX = "/api/v1"
LIBRARY_API = f"{API_PREFIX}/reference-library"
CREDENTIALS_API = f"{API_PREFIX}/data-source-credentials"

ZOTERO_USER_ID_A = "9001"
ZOTERO_USER_ID_B = "9002"
ZOTERO_API_KEY_A = "zotero-key-alice"
ZOTERO_API_KEY_B = "zotero-key-bob"

PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n%%EOF\n"


class _StubUser:
    """最小用户替身：端到端点只用到 id / username / is_active。"""

    def __init__(self, user_id: uuid.UUID, username: str) -> None:
        self.id = user_id
        self.username = username
        self.is_active = True


class _Env:
    def __init__(self, client: TestClient, app: FastAPI, factory: async_sessionmaker) -> None:
        self.client = client
        self.app = app
        self.factory = factory


@pytest.fixture()
def zotero_env(tmp_path: Any) -> Any:
    """隔离的 SQLite + 最小 FastAPI app（只挂引用库与数据源凭据两个路由）。"""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'zotero.db'}",
        connect_args={"timeout": 10},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    schema_state = {"ready": False}

    async def _ensure_schema() -> None:
        if schema_state["ready"]:
            return
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        schema_state["ready"] = True

    async def override_get_db() -> Any:
        await _ensure_schema()
        async with factory() as session:
            yield session

    async def override_get_current_user(request: Request) -> _StubUser:
        auth_header = request.headers.get("Authorization") or ""
        token = auth_header.removeprefix("Bearer ").strip()
        users = {TOKEN_A: _StubUser(USER_A, "alice"), TOKEN_B: _StubUser(USER_B, "bob")}
        user = users.get(token)
        if user is None:
            raise UnauthorizedException("缺少有效的鉴权令牌")
        return user

    application = FastAPI()
    application.include_router(reference_endpoint.router, prefix=API_PREFIX)
    application.include_router(credentials_endpoint.router, prefix=API_PREFIX)

    @application.exception_handler(AppException)
    async def _handle_app_exception(request: Request, exc: AppException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error_code": exc.error_code, "message": exc.message},
        )

    application.dependency_overrides[get_db] = override_get_db
    application.dependency_overrides[get_current_user] = override_get_current_user
    application.dependency_overrides[get_current_user_or_api_key] = override_get_current_user

    with TestClient(application) as client:
        yield _Env(client=client, app=application, factory=factory)

    application.dependency_overrides.clear()
    asyncio.run(engine.dispose())


# ===== 假 Zotero 上游 / 假入库服务 =====


def _zotero_item(
    *,
    key: str,
    title: str,
    item_type: str = "journalArticle",
    doi: str | None = None,
    year: int = 2024,
    creators: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """构造一条 Zotero Web API item（形状与真实响应一致：顶层 key + data）。"""
    data: dict[str, Any] = {
        "key": key,
        "itemType": item_type,
        "title": title,
        "creators": (
            creators
            if creators is not None
            else [{"creatorType": "author", "firstName": "Ada", "lastName": "Lovelace"}]
        ),
        "date": f"{year}-05-01",
        "publicationTitle": "Journal of Testing",
        "abstractNote": "这是一段摘要",
        "url": f"https://example.org/{key}",
    }
    if doi is not None:
        data["DOI"] = doi
    return {"key": key, "version": 1, "data": data}


def _attachment(
    *,
    key: str,
    parent_key: str,
    content_type: str = "application/pdf",
) -> dict[str, Any]:
    return {
        "key": key,
        "version": 1,
        "data": {
            "key": key,
            "itemType": "attachment",
            "parentItem": parent_key,
            "title": "Full Text PDF",
            "contentType": content_type,
            "filename": f"{key}.pdf",
            "linkMode": "imported_file",
        },
    }


def _zotero_transport(
    items_by_library: dict[str, list[dict[str, Any]]],
    *,
    files: dict[str, httpx.Response] | None = None,
    expected_api_key: str | None = None,
) -> httpx.MockTransport:
    """按库 ID 返回条目；`/items/{key}/file` 按 item_key 返回附件（缺省 404）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Zotero-API-Version") == "3"
        if expected_api_key is not None:
            assert request.headers.get("Zotero-API-Key") == expected_api_key
        path = request.url.path
        if path.endswith("/file"):
            item_key = path.split("/")[-2]
            if files and item_key in files:
                return files[item_key]
            return httpx.Response(404)
        for library_id, items in items_by_library.items():
            if path == f"/users/{library_id}/items":
                # limit 必须真的传了，否则上游会按默认 25 截断
                assert request.url.params.get("limit") is not None
                return httpx.Response(200, json=items)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


class _FakeDocumentService:
    """记录 ingest 调用的替身（不碰真实解析 / 向量库）。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.docs: list[Any] = []

    async def ingest(
        self,
        user_id: uuid.UUID,
        upload: Any,
        *,
        title: str,
        strategy: ChunkStrategy,
        ref_id: str | None = None,
    ) -> Any:
        content = await upload.read()
        doc = SimpleNamespace(meta_info={"ref_id": ref_id} if ref_id else {})
        self.calls.append(
            {
                "user_id": user_id,
                "title": title,
                "strategy": strategy,
                "ref_id": ref_id,
                "filename": upload.filename,
                "content": content,
            }
        )
        self.docs.append(doc)
        return doc


# ===== 小工具 =====


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _credential_payload(zotero_user_id: str, api_key: str) -> str:
    """凭据 JSON：**紧凑格式**（无空格）。

    `DataSourceCredentialUpsert` 的校验器拒绝内部空白字符，所以用户粘贴的
    Zotero 凭据必须是紧凑 JSON —— 与同步端解密后 `json.loads` 的语义一致。
    """
    return json.dumps(
        {"zotero_user_id": zotero_user_id, "zotero_api_key": api_key},
        separators=(",", ":"),
    )


def _configure_zotero(env: _Env, token: str, zotero_user_id: str, api_key: str) -> httpx.Response:
    response = env.client.put(
        f"{CREDENTIALS_API}/zotero",
        json={"api_key": _credential_payload(zotero_user_id, api_key)},
        headers=_auth(token),
    )
    assert response.status_code == 200, response.text
    return response


def _install_zotero(
    env: _Env,
    transport: httpx.MockTransport,
    *,
    document_service: Any = None,
) -> None:
    """把同步服务的 Zotero 客户端替换成 MockTransport（不联网）。"""

    def override_zotero_service(db: Any = Depends(get_db)) -> ZoteroSyncService:
        return ZoteroSyncService(
            db,
            client_factory=lambda api_key: ZoteroClient(api_key, transport=transport),
            document_service=document_service,
        )

    env.app.dependency_overrides[get_zotero_sync_service] = override_zotero_service


def _sync(env: _Env, *, token: str = TOKEN_A, **payload: Any) -> httpx.Response:
    body = payload or {"limit": 50}
    return env.client.post(f"{LIBRARY_API}/sync-zotero", json=body, headers=_auth(token))


def _library_list(env: _Env, token: str = TOKEN_A) -> dict[str, Any]:
    response = env.client.get(LIBRARY_API, headers=_auth(token))
    assert response.status_code == 200, response.text
    return response.json()


def _library_titles(env: _Env, token: str = TOKEN_A) -> list[str]:
    return [item["title"] for item in _library_list(env, token)["items"]]


def _read_ciphertext(env: _Env, user_id: uuid.UUID) -> str | None:
    async def _query() -> str | None:
        async with env.factory() as session:
            row = await session.scalar(
                select(DataSourceCredential).where(
                    DataSourceCredential.user_id == user_id,
                    DataSourceCredential.provider == "zotero",
                )
            )
            return None if row is None else row.api_key_ciphertext

    return env.client.portal.call(_query)


def _write_raw_ciphertext(env: _Env, user_id: uuid.UUID, value: str) -> None:
    async def _update() -> None:
        async with env.factory() as session:
            row = await session.scalar(
                select(DataSourceCredential).where(
                    DataSourceCredential.user_id == user_id,
                    DataSourceCredential.provider == "zotero",
                )
            )
            assert row is not None
            row.api_key_ciphertext = value
            await session.commit()

    env.client.portal.call(_update)


# ===== 条目同步 =====


def test_sync_imports_items_and_is_idempotent(zotero_env: _Env) -> None:
    env = zotero_env
    _configure_zotero(env, TOKEN_A, ZOTERO_USER_ID_A, ZOTERO_API_KEY_A)
    items = [
        _zotero_item(key="ITEM0001", title="Retrieval Augmented Generation", doi="10.1000/rag.001"),
        _zotero_item(key="ITEM0002", title="没有 DOI 的预印本"),
        _zotero_item(
            key="ITEM0003",
            title="同一篇文献的另一种写法",
            doi="https://doi.org/10.1000/RAG.001",
        ),
    ]
    _install_zotero(env, _zotero_transport({"9001": items}, expected_api_key=ZOTERO_API_KEY_A))

    first = _sync(env)
    assert first.status_code == 200, first.text
    assert first.json() == {"imported": 2, "skipped": 1, "attachments": 0, "failed": 0}
    assert _library_list(env)["total"] == 2

    second = _sync(env)
    assert second.status_code == 200, second.text
    assert second.json() == {"imported": 0, "skipped": 3, "attachments": 0, "failed": 0}
    assert _library_list(env)["total"] == 2


def test_sync_downloads_pdf_attachment_and_binds_ref_id(zotero_env: _Env) -> None:
    env = zotero_env
    _configure_zotero(env, TOKEN_A, ZOTERO_USER_ID_A, ZOTERO_API_KEY_A)
    parent = _zotero_item(key="PARENT01", title="带附件论文", doi="10.1000/fulltext.001")
    items = [
        parent,
        _attachment(key="ATTACH01", parent_key="PARENT01"),
        _attachment(key="ATTACH02", parent_key="PARENT01"),
        _attachment(key="ATTACH03", parent_key="PARENT01", content_type="text/html"),
    ]
    transport = _zotero_transport(
        {"9001": items},
        files={
            "ATTACH01": httpx.Response(
                200, headers={"content-type": "application/pdf"}, content=PDF_BYTES
            )
        },
    )
    fake = _FakeDocumentService()
    _install_zotero(env, transport, document_service=fake)

    response = _sync(env)
    assert response.status_code == 200, response.text
    assert response.json() == {"imported": 1, "skipped": 1, "attachments": 1, "failed": 1}

    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["user_id"] == USER_A
    assert call["title"] == "带附件论文"
    assert call["ref_id"] == "doi:10.1000/fulltext.001"
    assert call["filename"] == "ATTACH01.pdf"
    assert call["content"] == PDF_BYTES
    assert call["strategy"] is ChunkStrategy.RECURSIVE
    assert fake.docs[0].meta_info["origin"] == "zotero"
    assert fake.docs[0].meta_info["ref_id"] == "doi:10.1000/fulltext.001"
    assert _library_titles(env) == ["带附件论文"]


def test_sync_skips_attachment_over_30mb(zotero_env: _Env) -> None:
    env = zotero_env
    _configure_zotero(env, TOKEN_A, ZOTERO_USER_ID_A, ZOTERO_API_KEY_A)
    items = [
        _zotero_item(key="PARENT02", title="超大附件论文", doi="10.1000/big.001"),
        _attachment(key="ATTACHBIG", parent_key="PARENT02"),
    ]
    oversized = httpx.Response(
        200,
        headers={
            "content-type": "application/pdf",
            "content-length": str(MAX_ATTACHMENT_BYTES + 1),
        },
    )
    fake = _FakeDocumentService()
    _install_zotero(
        env,
        _zotero_transport({"9001": items}, files={"ATTACHBIG": oversized}),
        document_service=fake,
    )

    response = _sync(env)
    assert response.status_code == 200, response.text
    assert response.json() == {"imported": 1, "skipped": 1, "attachments": 0, "failed": 0}
    assert fake.calls == []


# ===== 凭据错误 =====


def test_sync_without_credential_returns_readable_404(zotero_env: _Env) -> None:
    env = zotero_env
    called = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - 不应被调用
        called["count"] += 1
        return httpx.Response(500)

    _install_zotero(env, httpx.MockTransport(handler))

    response = _sync(env)
    assert response.status_code == 404
    body = response.json()
    assert body["error_code"] == "NOT_FOUND"
    assert "Zotero" in body["message"]
    assert called["count"] == 0
    assert _library_list(env)["total"] == 0


def test_sync_with_broken_credential_returns_readable_401(zotero_env: _Env) -> None:
    env = zotero_env
    _configure_zotero(env, TOKEN_A, ZOTERO_USER_ID_A, ZOTERO_API_KEY_A)
    _write_raw_ciphertext(env, USER_A, "v1:broken")
    _install_zotero(env, _zotero_transport({"9001": []}))

    response = _sync(env)
    assert response.status_code == 401
    body = response.json()
    assert body["error_code"] == "UNAUTHORIZED"
    assert "解密" in body["message"]


def test_sync_with_incomplete_credential_returns_readable_422(zotero_env: _Env) -> None:
    env = zotero_env
    env.client.put(
        f"{CREDENTIALS_API}/zotero",
        json={"api_key": '{"zotero_user_id":"9001"}'},
        headers=_auth(TOKEN_A),
    )
    _install_zotero(env, _zotero_transport({"9001": []}))

    response = _sync(env)
    assert response.status_code == 422
    assert "zotero_api_key" in response.json()["message"]


def test_sync_maps_zotero_auth_error_to_401(zotero_env: _Env) -> None:
    env = zotero_env
    _configure_zotero(env, TOKEN_A, ZOTERO_USER_ID_A, ZOTERO_API_KEY_A)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "forbidden"})

    _install_zotero(env, httpx.MockTransport(handler))

    response = _sync(env)
    assert response.status_code == 401
    assert "Zotero" in response.json()["message"]


# ===== 跨用户隔离 =====


def test_cross_user_zotero_items_are_isolated(zotero_env: _Env) -> None:
    env = zotero_env
    _configure_zotero(env, TOKEN_A, ZOTERO_USER_ID_A, ZOTERO_API_KEY_A)
    _configure_zotero(env, TOKEN_B, ZOTERO_USER_ID_B, ZOTERO_API_KEY_B)
    _install_zotero(
        env,
        _zotero_transport(
            {
                "9001": [
                    _zotero_item(
                        key="ALICE001", title="Alice 的 Zotero 论文", doi="10.1000/alice.001"
                    )
                ],
                "9002": [
                    _zotero_item(key="BOB00001", title="Bob 的 Zotero 论文", doi="10.1000/bob.001")
                ],
            }
        ),
    )

    assert _sync(env, token=TOKEN_A).json()["imported"] == 1
    assert _sync(env, token=TOKEN_B).json()["imported"] == 1

    assert _library_titles(env, TOKEN_A) == ["Alice 的 Zotero 论文"]
    assert _library_titles(env, TOKEN_B) == ["Bob 的 Zotero 论文"]
    assert "Alice 的 Zotero 论文" not in _library_titles(env, TOKEN_B)


# ===== 凭据端点（provider="zotero"）=====


def test_zotero_credential_can_be_saved_and_listed(zotero_env: _Env) -> None:
    env = zotero_env

    default = env.client.get(CREDENTIALS_API, headers=_auth(TOKEN_A)).json()
    assert [item["provider"] for item in default] == ["wanfang"]  # 既有契约不变

    response = _configure_zotero(env, TOKEN_A, ZOTERO_USER_ID_A, ZOTERO_API_KEY_A)
    body = response.json()
    assert body["provider"] == "zotero"
    assert body["configured"] is True
    assert body["status"] == "active"
    assert ZOTERO_API_KEY_A not in response.text

    listed = env.client.get(CREDENTIALS_API, headers=_auth(TOKEN_A)).json()
    assert [item["provider"] for item in listed] == ["wanfang", "zotero"]

    ciphertext = _read_ciphertext(env, USER_A)
    assert ciphertext is not None
    assert ciphertext.startswith("v1:")
    decrypted = CredentialCipher.from_settings().decrypt(ciphertext, user_id=USER_A)
    assert json.loads(decrypted) == {
        "zotero_user_id": ZOTERO_USER_ID_A,
        "zotero_api_key": ZOTERO_API_KEY_A,
    }

    deleted = env.client.delete(f"{CREDENTIALS_API}/zotero", headers=_auth(TOKEN_A))
    assert deleted.status_code == 204
    after = env.client.get(CREDENTIALS_API, headers=_auth(TOKEN_A)).json()
    assert [item["provider"] for item in after] == ["wanfang"]


# ===== 请求校验 / 客户端单元 =====


def test_sync_request_validation(zotero_env: _Env) -> None:
    env = zotero_env
    too_many = env.client.post(
        f"{LIBRARY_API}/sync-zotero", json={"limit": 101}, headers=_auth(TOKEN_A)
    )
    assert too_many.status_code == 422

    extra_field = env.client.post(
        f"{LIBRARY_API}/sync-zotero", json={"unexpected": 1}, headers=_auth(TOKEN_A)
    )
    assert extra_field.status_code == 422


def test_client_uses_zotero_web_api_headers_and_group_path() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        seen["version"] = request.headers.get("Zotero-API-Version")
        seen["key"] = request.headers.get("Zotero-API-Key")
        return httpx.Response(200, json=[_zotero_item(key="GRP00001", title="群组条目")])

    async def _run() -> list[dict[str, Any]]:
        async with ZoteroClient("key-group", transport=httpx.MockTransport(handler)) as client:
            return await client.fetch_items(
                library_type="group",
                library_id="555",
                collection_key="COLL1",
                limit=5,
            )

    items = asyncio.run(_run())
    assert seen["path"] == "/groups/555/items"
    assert seen["params"] == {"limit": "5", "collection": "COLL1"}
    assert seen["version"] == "3"
    assert seen["key"] == "key-group"
    assert len(items) == 1


def test_client_maps_auth_error_to_readable_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "forbidden"})

    async def _run() -> ZoteroAuthError:
        async with ZoteroClient("bad-key", transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ZoteroAuthError) as excinfo:
                await client.fetch_items(library_type="user", library_id="9001")
            return excinfo.value

    error = asyncio.run(_run())
    assert "Key" in str(error)
