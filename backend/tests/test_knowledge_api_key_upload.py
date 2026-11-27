"""知识库上传的 API Key 通道（剪藏扩展附带 PDF 场景）。

背景：剪藏扩展没有登录态，只有用户自己生成的 `X-API-Key`。Issue #67 Step 2
起扩展可只保存题录；Phase B 起扩展还会把用户刚下载的 PDF 一并 POST 到
`/api/v1/knowledge/documents`。该端点原先是 JWT-only，导致真实环境上传 401。

本文件用**真实鉴权依赖**（不覆盖 `get_current_user_or_api_key`）验证：
1. 带 `knowledge:write` scope 的 Key 可以上传；
2. Key 缺 scope → 403（可读错误，指出缺哪个 scope）；
3. 未知/无效 Key → 401；
4. JWT 通道行为不变（回归，既有 `test_knowledge_api.py` 覆盖主路径）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models.user import User
from app.db.session import get_db
from app.knowledge.services import factory
from app.security.api_key_service import ApiKeyService


@pytest.fixture(autouse=True)
def _isolate_kb(tmp_path, monkeypatch) -> Iterator[None]:
    """受控上传目录 + Mock 知识库（与 test_knowledge_api.py 同口径）。"""
    from app.core.config import get_settings

    get_settings().app.upload_root = str(tmp_path / "uploads")
    factory.reset_kb()
    factory.init_kb(
        force_mock_embedding=True,
        force_mock_reranker=True,
        chroma_persist_dir="",
    )
    yield
    factory.reset_kb()


@pytest.fixture
def env(tmp_path) -> Iterator[dict[str, Any]]:
    """真实鉴权依赖 + 独立 SQLite；准备「有写权限」与「无写权限」两个 Key。"""
    from app.main import create_app

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'kb_api_key.db'}",
        connect_args={"timeout": 10},
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _seed() -> tuple[str, str]:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with session_factory() as session:
            user = User(
                username="clipkb",
                email="clipkb@example.com",
                password_hash="x",
                is_active=True,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
        service = ApiKeyService(session_factory)
        _, writable = await service.create(user.id, name="clipper", scopes=["knowledge:write"])
        _, readonly = await service.create(user.id, name="readonly", scopes=["tasks:read"])
        return writable, readonly

    async def override_get_db() -> Any:
        async with session_factory() as session:
            yield session

    writable_key, readonly_key = asyncio.run(_seed())
    app = create_app()
    app.dependency_overrides[get_db] = override_get_db

    client = TestClient(app)
    client.__enter__()
    try:
        yield {
            "client": client,
            "writable_key": writable_key,
            "readonly_key": readonly_key,
        }
    finally:
        client.__exit__(None, None, None)
        app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


def _upload(client: TestClient, headers: dict[str, str]) -> Any:
    return client.post(
        "/api/v1/knowledge/documents",
        files={"file": ("clip.txt", b"RAG evaluation benchmark notes.", "text/plain")},
        data={"title": "剪藏全文测试", "ref_id": "doi:10.1234/kbkey.2026"},
        headers=headers,
    )


class TestKnowledgeUploadApiKey:
    def test_api_key_with_knowledge_scope_can_upload(self, env: dict[str, Any]) -> None:
        """剪藏扩展的真实路径：X-API-Key + knowledge:write → 201。"""
        resp = _upload(env["client"], {"X-API-Key": env["writable_key"]})

        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["title"] == "剪藏全文测试"
        assert body["source"] == "upload"
        assert body["index_status"] in {"ready", "indexing"}

    def test_api_key_without_knowledge_scope_is_forbidden(self, env: dict[str, Any]) -> None:
        """只读 Key 不得上传，且错误信息要指出缺少的 scope。"""
        resp = _upload(env["client"], {"X-API-Key": env["readonly_key"]})

        assert resp.status_code == 403, resp.text
        assert "knowledge:write" in resp.text

    def test_invalid_api_key_is_unauthorized(self, env: dict[str, Any]) -> None:
        resp = _upload(env["client"], {"X-API-Key": "maa_doesnotexist0000000000"})

        assert resp.status_code == 401
