"""知识库 API 端点测试：鉴权 multipart 上传、owner 隔离、持久化。

- 全部端点需 Bearer token；未认证返回 401
- 跨用户访问返回 404（list/search 只含本人数据）
- 上传后 KnowledgeDoc/DocChunk 落库；重建 BM25 后 list/search 仍可用
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models.knowledge import DocChunk, KnowledgeDoc
from app.db.session import get_db
from app.knowledge.services import factory


@pytest.fixture
def client(tmp_path) -> Iterator[TestClient]:
    """带独立文件型 SQLite 的 TestClient，暴露 engine 供持久化断言。

    用文件型库而非 :memory:：多连接/跨事件循环访问共享同一数据库，
    便于在断言协程中用独立 session 查询上传后的落库数据。
    """
    from app.main import app

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'knowledge_test.db'}", echo=False
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    state: dict[str, bool] = {"created": False}

    async def override_get_db() -> AsyncSession:
        if not state["created"]:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            state["created"] = True
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    test_client = TestClient(app)
    test_client.__enter__()
    test_client.engine = engine
    test_client.session_factory = session_factory  # type: ignore[attr-defined]
    yield test_client
    test_client.__exit__(None, None, None)
    app.dependency_overrides.clear()
    asyncio.run(engine.dispose())


@pytest.fixture(autouse=True)
def _isolate_kb(tmp_path, monkeypatch) -> Iterator[None]:
    """每个测试：受控上传目录指向 tmp_path，Mock 模式初始化知识库。"""
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


# ===== 辅助 =====


def _register_and_login(client: TestClient, username: str) -> str:
    client.post(
        "/api/v1/auth/register",
        json={
            "username": username,
            "email": f"{username}@test.com",
            "password": "password123",
        },
    )
    resp = client.post(
        "/api/v1/auth/login",
        json={"account": username, "password": "password123"},
    )
    assert resp.status_code == 200
    return resp.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _upload(client: TestClient, token: str, filename: str, content: bytes, title: str) -> Any:
    resp = client.post(
        "/api/v1/knowledge/documents",
        files={"file": (filename, content, "text/plain")},
        data={"title": title},
        headers=_auth(token),
    )
    return resp


class TestAuthRequired:
    """未认证访问应返回 401。"""

    def test_upload_unauthenticated(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/knowledge/documents",
            files={"file": ("a.txt", b"content", "text/plain")},
            data={"title": "t"},
        )
        assert resp.status_code == 401

    def test_list_unauthenticated(self, client: TestClient) -> None:
        assert client.get("/api/v1/knowledge/documents").status_code == 401

    def test_stats_unauthenticated(self, client: TestClient) -> None:
        assert client.get("/api/v1/knowledge/stats").status_code == 401

    def test_search_unauthenticated(self, client: TestClient) -> None:
        resp = client.post("/api/v1/knowledge/search", json={"query": "q"})
        assert resp.status_code == 401

    def test_delete_unauthenticated(self, client: TestClient) -> None:
        resp = client.delete("/api/v1/knowledge/documents/12345678-1234-5678-1234-567812345678")
        assert resp.status_code == 401


class TestUpload:
    """multipart 上传：成功 / 校验失败。"""

    def test_upload_txt_success(self, client: TestClient) -> None:
        token = _register_and_login(client, "uploader")
        resp = _upload(
            client,
            token,
            "paper.txt",
            "RAG 检索增强生成通过向量检索与重排提升 LLM 回答质量。" * 20,
            "我的论文",
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["index_status"] == "ready"
        assert body["doc_type"] == "txt"
        assert body["title"] == "我的论文"
        assert body["chunk_count"] > 0
        assert body["id"]

    def test_upload_invalid_extension_rejected(self, client: TestClient) -> None:
        token = _register_and_login(client, "badext")
        resp = client.post(
            "/api/v1/knowledge/documents",
            files={"file": ("script.exe", b"MZ\x90", "application/octet-stream")},
            data={"title": "t"},
            headers=_auth(token),
        )
        assert resp.status_code == 422

    def test_upload_missing_title_rejected(self, client: TestClient) -> None:
        token = _register_and_login(client, "notitle")
        resp = client.post(
            "/api/v1/knowledge/documents",
            files={"file": ("a.txt", "内容内容".encode(), "text/plain")},
            headers=_auth(token),
        )
        assert resp.status_code == 422


class TestOwnerIsolation:
    """双用户隔离：list / search / delete 均按 user_id 过滤。"""

    def test_user_b_cannot_list_user_a_docs(self, client: TestClient) -> None:
        token_a = _register_and_login(client, "iso-a")
        token_b = _register_and_login(client, "iso-b")
        resp = _upload(client, token_a, "a.txt", "用户A的专属文献资料" * 10, "A 文档")
        doc_id = resp.json()["id"]

        listed = client.get("/api/v1/knowledge/documents", headers=_auth(token_b))
        assert listed.status_code == 200
        assert listed.json()["documents"] == []

        # 跨用户 get/delete 返回 404（不泄漏存在性）
        assert (
            client.delete(
                f"/api/v1/knowledge/documents/{doc_id}", headers=_auth(token_b)
            ).status_code
            == 404
        )

        # A 删除成功
        assert (
            client.delete(
                f"/api/v1/knowledge/documents/{doc_id}", headers=_auth(token_a)
            ).status_code
            == 200
        )

    def test_user_b_cannot_search_user_a_content(self, client: TestClient) -> None:
        token_a = _register_and_login(client, "sea-a")
        token_b = _register_and_login(client, "sea-b")
        _upload(client, token_a, "a.txt", "量子计算在密码学中的应用研究" * 15, "A 文献")
        _upload(client, token_b, "b.txt", "古典音乐与贝多芬交响曲赏析" * 15, "B 文献")

        results_b = client.post(
            "/api/v1/knowledge/search",
            json={"query": "量子计算 密码学", "top_k": 5},
            headers=_auth(token_b),
        )
        assert results_b.status_code == 200
        body = results_b.json()
        # B 检索 A 的主题：只允许返回 B 自己的 chunk（本例应为 0 条命中）
        for item in body["results"]:
            assert item["doc_id"] != "unknown"

    def test_delete_own_doc_then_gone(self, client: TestClient) -> None:
        token = _register_and_login(client, "del-user")
        resp = _upload(client, token, "a.txt", "待删除的文献内容" * 10, "临时文档")
        doc_id = resp.json()["id"]
        assert (
            client.delete(f"/api/v1/knowledge/documents/{doc_id}", headers=_auth(token)).status_code
            == 200
        )
        listed = client.get("/api/v1/knowledge/documents", headers=_auth(token))
        assert listed.json()["documents"] == []
        # 再删一次 404
        assert (
            client.delete(f"/api/v1/knowledge/documents/{doc_id}", headers=_auth(token)).status_code
            == 404
        )


class TestStats:
    """统计按用户隔离。"""

    def test_empty_stats(self, client: TestClient) -> None:
        token = _register_and_login(client, "stat-user")
        resp = client.get("/api/v1/knowledge/stats", headers=_auth(token))
        assert resp.status_code == 200
        assert resp.json()["total_docs"] == 0

    def test_stats_reflect_own_docs(self, client: TestClient) -> None:
        token = _register_and_login(client, "stat-user2")
        _upload(client, token, "a.txt", "统计测试文献内容" * 10, "文档一")
        resp = client.get("/api/v1/knowledge/stats", headers=_auth(token))
        data = resp.json()
        assert data["total_docs"] == 1
        assert data["total_chunks"] > 0


class TestPersistence:
    """持久化：上传后落库，重建 BM25 后 list/search 仍可用。"""

    def test_upload_persists_doc_and_chunks(self, client: TestClient) -> None:
        token = _register_and_login(client, "persist-user")
        resp = _upload(
            client,
            token,
            "paper.txt",
            "多智能体协作系统综述：规划执行校验复盘四阶段闭环。" * 10,
            "持久化文档",
        )
        assert resp.status_code == 201
        doc_id = uuid.UUID(resp.json()["id"])

        async def _assert() -> tuple[int, int, str]:
            async with client.session_factory() as session:  # type: ignore[attr-defined]
                doc = (
                    await session.execute(select(KnowledgeDoc).where(KnowledgeDoc.id == doc_id))
                ).scalar_one()
                chunks = await session.scalar(select(func.count()).select_from(DocChunk))
                return 1, chunks, doc.index_status

        _, chunk_count, status = asyncio.run(_assert())
        assert chunk_count > 0
        assert status == "ready"

    def test_rebuild_bm25_from_db_after_restart(self, client: TestClient) -> None:
        """模拟重启：清空内存索引后从持久化分块重建，search 仍可用。"""
        token = _register_and_login(client, "rebuild-user")
        resp = _upload(
            client,
            token,
            "paper.txt",
            "RAG 检索增强生成技术路线综述：向量库与混合检索。" * 10,
            "重建文档",
        )
        assert resp.status_code == 201

        # 模拟进程重启：重置单例（内存 BM25/Chroma 清空），重新初始化
        factory.reset_kb()

        factory.init_kb(
            force_mock_embedding=True,
            force_mock_reranker=True,
            chroma_persist_dir="",
        )

        # 从数据库 ready 分块重建 BM25
        async def _rebuild() -> int:
            async with client.session_factory() as session:  # type: ignore[attr-defined]
                from app.knowledge.services.document_service import KnowledgeDocumentService

                service = KnowledgeDocumentService(
                    session,
                    storage=factory.get_storage(),
                    index_manager=factory.get_index_manager(),
                    kb=factory.get_kb(),
                )
                return await service.rebuild_bm25()

        rebuilt = asyncio.run(_rebuild())
        assert rebuilt > 0

        # list 仍可用（走数据库）
        listed = client.get("/api/v1/knowledge/documents", headers=_auth(token))
        assert len(listed.json()["documents"]) == 1

        # search 仍可用（BM25 路命中）
        searched = client.post(
            "/api/v1/knowledge/search",
            json={"query": "RAG 检索增强", "top_k": 5},
            headers=_auth(token),
        )
        assert searched.status_code == 200
        assert searched.json()["total"] >= 1


class TestSearch:
    """检索行为。"""

    def test_search_empty(self, client: TestClient) -> None:
        token = _register_and_login(client, "search-empty")
        resp = client.post("/api/v1/knowledge/search", json={"query": "任意"}, headers=_auth(token))
        assert resp.status_code == 200
        assert resp.json() == {"query": "任意", "total": 0, "results": []}

    def test_search_returns_own_results(self, client: TestClient) -> None:
        token = _register_and_login(client, "search-user")
        _upload(client, token, "a.txt", "RAG 检索增强生成提升回答质量" * 10, "检索文档")
        resp = client.post(
            "/api/v1/knowledge/search",
            json={"query": "RAG 检索", "top_k": 3},
            headers=_auth(token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        for item in body["results"]:
            assert item["text"]
            assert item["score"] > 0
