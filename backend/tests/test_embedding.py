"""向量嵌入服务单元测试：Mock 模式与工厂函数。"""

from app.knowledge.services.embedding import (
    MockEmbeddingService,
    create_embedding_service,
)


class TestMockEmbeddingService:
    """Mock 嵌入服务测试。"""

    def test_dimension_default(self) -> None:
        """默认维度为 1024。"""
        svc = MockEmbeddingService()
        assert svc.dimension == 1024

    def test_dimension_custom(self) -> None:
        """可自定义维度。"""
        svc = MockEmbeddingService(dimension=768)
        assert svc.dimension == 768

    def test_embed_returns_correct_length(self) -> None:
        """嵌入向量维度与 dimension 一致。"""
        svc = MockEmbeddingService(dimension=128)
        vectors = svc.embed(["文本1", "文本2"])
        assert len(vectors) == 2
        for vec in vectors:
            assert len(vec) == 128

    def test_embed_deterministic(self) -> None:
        """相同文本生成相同向量（确定性）。"""
        svc = MockEmbeddingService()
        v1 = svc.embed_one("测试文本")
        v2 = svc.embed_one("测试文本")
        assert v1 == v2

    def test_embed_different_text_different_vector(self) -> None:
        """不同文本生成不同向量。"""
        svc = MockEmbeddingService()
        v1 = svc.embed_one("文本A")
        v2 = svc.embed_one("文本B")
        assert v1 != v2

    def test_embed_values_in_range(self) -> None:
        """向量值在 [-1, 1] 区间。"""
        svc = MockEmbeddingService()
        vec = svc.embed_one("任意文本")
        for val in vec:
            assert -1.0 <= val <= 1.0

    def test_embed_empty_list(self) -> None:
        """空列表返回空列表。"""
        svc = MockEmbeddingService()
        assert svc.embed([]) == []

    def test_embed_one_convenience(self) -> None:
        """embed_one 返回单条向量。"""
        svc = MockEmbeddingService(dimension=64)
        vec = svc.embed_one("单条")
        assert isinstance(vec, list)
        assert len(vec) == 64


class TestCreateEmbeddingService:
    """工厂函数测试。"""

    def test_force_mock_returns_mock(self) -> None:
        """force_mock=True 返回 Mock 服务。"""
        svc = create_embedding_service(force_mock=True)
        assert isinstance(svc, MockEmbeddingService)

    def test_env_var_triggers_mock(self, monkeypatch) -> None:
        """环境变量 RAG_EMBEDDING_MOCK=1 返回 Mock 服务。"""
        monkeypatch.setenv("RAG_EMBEDDING_MOCK", "1")
        svc = create_embedding_service()
        assert isinstance(svc, MockEmbeddingService)

    def test_env_var_0_returns_real_or_fallback(self, monkeypatch) -> None:
        """环境变量 RAG_EMBEDDING_MOCK=0 尝试真实模型，失败降级 Mock。"""
        monkeypatch.setenv("RAG_EMBEDDING_MOCK", "0")
        # 真实模型在 CI 不可用时降级为 Mock，不抛异常
        svc = create_embedding_service()
        # 可能是 SentenceTransformerEmbeddingService 或 MockEmbeddingService
        assert svc.dimension > 0
