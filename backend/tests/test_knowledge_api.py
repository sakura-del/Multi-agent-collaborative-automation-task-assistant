"""知识库 API 端点测试：文档上传/删除/检索/统计/列表。

使用 Mock 模式初始化知识库（无模型下载），内存 Chroma（无持久化）。
每个测试前后清理全局单例，避免相互污染。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.knowledge.services import factory


@pytest.fixture(autouse=True)
def _isolate_kb():
    """每个测试前后清理全局单例。"""
    factory.reset_kb()
    factory.init_kb(
        force_mock_embedding=True,
        force_mock_reranker=True,
        chroma_persist_dir="",
    )
    yield
    factory.reset_kb()


@pytest.fixture
def client() -> TestClient:
    """构造 TestClient，复用全局 app。"""
    from app.main import app

    with TestClient(app) as c:
        yield c


class TestUploadDocument:
    """POST /api/v1/knowledge/documents：文档上传。"""

    def test_upload_text_success(self, client: TestClient) -> None:
        """上传文本应成功并返回 doc_id 与统计信息。"""
        resp = client.post(
            "/api/v1/knowledge/documents",
            json={"text": "RAG 检索增强生成系统", "doc_id": "doc-1"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["doc_id"] == "doc-1"
        assert data["doc_type"] == "txt"
        assert data["text_length"] > 0

    def test_upload_without_text_or_path_returns_400(self, client: TestClient) -> None:
        """未提供 text 与 file_path 应返回 400。"""
        resp = client.post("/api/v1/knowledge/documents", json={})
        assert resp.status_code == 400

    def test_upload_with_strategy(self, client: TestClient) -> None:
        """指定 strategy 应被正确解析。"""
        text = "段落一\n\n段落二\n\n段落三"
        resp = client.post(
            "/api/v1/knowledge/documents",
            json={"text": text, "strategy": "semantic"},
        )
        assert resp.status_code == 200

    def test_upload_invalid_strategy_falls_back(self, client: TestClient) -> None:
        """无效 strategy 应回退为 recursive。"""
        resp = client.post(
            "/api/v1/knowledge/documents",
            json={"text": "内容", "strategy": "invalid"},
        )
        assert resp.status_code == 200


class TestDeleteDocument:
    """DELETE /api/v1/knowledge/documents/{doc_id}：文档删除。"""

    def test_delete_existing_doc(self, client: TestClient) -> None:
        """删除已存在文档应成功。"""
        client.post(
            "/api/v1/knowledge/documents",
            json={"text": "待删除内容", "doc_id": "del-1"},
        )
        resp = client.delete("/api/v1/knowledge/documents/del-1")
        assert resp.status_code == 200
        assert resp.json()["doc_id"] == "del-1"

    def test_delete_nonexistent_returns_404(self, client: TestClient) -> None:
        """删除不存在的文档应返回 404。"""
        resp = client.delete("/api/v1/knowledge/documents/nonexistent")
        assert resp.status_code == 404


class TestStats:
    """GET /api/v1/knowledge/stats：索引统计。"""

    def test_empty_stats(self, client: TestClient) -> None:
        """空索引统计应为零。"""
        resp = client.get("/api/v1/knowledge/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_docs"] == 0
        assert data["total_chunks"] == 0

    def test_stats_after_upload(self, client: TestClient) -> None:
        """上传后统计应反映文档数。"""
        client.post(
            "/api/v1/knowledge/documents",
            json={"text": "内容一\n\n内容二", "doc_id": "s1"},
        )
        resp = client.get("/api/v1/knowledge/stats")
        data = resp.json()
        assert data["total_docs"] == 1
        assert data["total_chunks"] > 0
        assert data["vector_index_ready"] is True
        assert data["bm25_index_ready"] is True


class TestListDocuments:
    """GET /api/v1/knowledge/documents：文档列表。"""

    def test_empty_list(self, client: TestClient) -> None:
        """空索引文档列表为空。"""
        resp = client.get("/api/v1/knowledge/documents")
        assert resp.status_code == 200
        assert resp.json()["documents"] == []

    def test_list_after_upload(self, client: TestClient) -> None:
        """上传后列表应包含文档信息。"""
        client.post(
            "/api/v1/knowledge/documents",
            json={"text": "内容", "doc_id": "l1"},
        )
        resp = client.get("/api/v1/knowledge/documents")
        docs = resp.json()["documents"]
        assert len(docs) == 1
        assert docs[0]["doc_id"] == "l1"


class TestSearch:
    """POST /api/v1/knowledge/search：检索测试。"""

    def test_search_empty_index(self, client: TestClient) -> None:
        """空索引检索应返回空结果。"""
        resp = client.post(
            "/api/v1/knowledge/search",
            json={"query": "任意", "top_k": 5},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["results"] == []

    def test_search_returns_results(self, client: TestClient) -> None:
        """有索引时应返回检索结果。"""
        client.post(
            "/api/v1/knowledge/documents",
            json={
                "text": "RAG 检索增强生成通过向量检索与重排提升 LLM 回答质量",
                "doc_id": "search-1",
            },
        )
        resp = client.post(
            "/api/v1/knowledge/search",
            json={"query": "RAG 检索", "top_k": 3},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        for item in data["results"]:
            assert "chunk_id" in item
            assert "doc_id" in item
            assert "text" in item
            assert "score" in item
            assert "source" in item

    def test_search_skip_cache(self, client: TestClient) -> None:
        """skip_cache 应正常工作。"""
        client.post(
            "/api/v1/knowledge/documents",
            json={"text": "缓存测试内容", "doc_id": "c1"},
        )
        resp = client.post(
            "/api/v1/knowledge/search",
            json={"query": "缓存", "top_k": 3, "skip_cache": True},
        )
        assert resp.status_code == 200

    def test_search_skip_rerank(self, client: TestClient) -> None:
        """skip_rerank 应正常工作。"""
        client.post(
            "/api/v1/knowledge/documents",
            json={"text": "重排测试内容", "doc_id": "r1"},
        )
        resp = client.post(
            "/api/v1/knowledge/search",
            json={"query": "重排", "top_k": 3, "skip_rerank": True},
        )
        assert resp.status_code == 200


class TestRebuild:
    """POST /api/v1/knowledge/documents/rebuild：重建索引。"""

    def test_rebuild_clears_index(self, client: TestClient) -> None:
        """重建（清空）后索引应为空。"""
        client.post(
            "/api/v1/knowledge/documents",
            json={"text": "内容", "doc_id": "rb1"},
        )
        resp = client.post("/api/v1/knowledge/documents/rebuild")
        assert resp.status_code == 200
        # 重建后统计应为零
        stats = client.get("/api/v1/knowledge/stats").json()
        assert stats["total_docs"] == 0
