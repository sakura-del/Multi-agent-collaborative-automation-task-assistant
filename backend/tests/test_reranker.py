"""Reranker 测试：Mock 关键词重排与工厂函数。

覆盖 MockReranker 重排行为、空结果处理、top_k 截断、metadata 标记、工厂降级。
"""

from __future__ import annotations

from app.knowledge.models.document import RetrievalResult
from app.knowledge.services.reranker import (
    BGEReranker,
    MockReranker,
    create_reranker,
)


def _make_results(n: int = 4) -> list[RetrievalResult]:
    """构造测试用检索结果，文本各异以便区分重排顺序。"""
    return [
        RetrievalResult(
            chunk_id=f"c{i}",
            doc_id="d1",
            text=f"RAG 检索增强生成 第 {i} 条结果内容",
            score=0.5 - i * 0.05,
            metadata={"doc_source": "test.txt"},
            source="hybrid",
        )
        for i in range(n)
    ]


class TestMockReranker:
    """Mock 关键词重排器。"""

    def test_empty_results_returns_empty(self) -> None:
        """空结果重排应返回空列表。"""
        reranker = MockReranker()
        assert reranker.rerank("查询", [], top_k=5) == []

    def test_returns_at_most_top_k(self) -> None:
        """重排结果数不超过 top_k。"""
        reranker = MockReranker()
        results = _make_results(5)
        reranked = reranker.rerank("RAG 检索", results, top_k=2)
        assert len(reranked) <= 2

    def test_marks_reranked_metadata(self) -> None:
        """重排结果应标记 metadata['reranked']=True。"""
        reranker = MockReranker()
        reranked = reranker.rerank("RAG", _make_results(2), top_k=2)
        for r in reranked:
            assert r.metadata.get("reranked") is True

    def test_higher_overlap_ranks_first(self) -> None:
        """与 query 重叠度高的结果应排在前面。"""
        reranker = MockReranker()
        results = [
            RetrievalResult(
                chunk_id="c-low",
                doc_id="d1",
                text="无关内容 xyz abc",
                score=0.9,
                source="hybrid",
            ),
            RetrievalResult(
                chunk_id="c-high",
                doc_id="d1",
                text="RAG 检索增强生成系统",
                score=0.5,
                source="hybrid",
            ),
        ]
        reranked = reranker.rerank("RAG 检索", results, top_k=2)
        # 重叠度高的应排前
        assert reranked[0].chunk_id == "c-high"

    def test_empty_query_returns_input_top_k(self) -> None:
        """空查询（无有效词项）应直接返回前 top_k 条。"""
        reranker = MockReranker()
        results = _make_results(3)
        reranked = reranker.rerank(" ， 。 ", results, top_k=2)
        assert len(reranked) == 2
        # 顺序与输入一致
        assert reranked[0].chunk_id == results[0].chunk_id

    def test_score_in_range(self) -> None:
        """重排后得分应在 [0, 1] 区间。"""
        reranker = MockReranker()
        reranked = reranker.rerank("RAG 检索", _make_results(3), top_k=3)
        for r in reranked:
            assert 0.0 <= r.score <= 1.0

    def test_preserves_chunk_id_and_text(self) -> None:
        """重排不应改变 chunk_id 与 text 内容。"""
        reranker = MockReranker()
        original = _make_results(3)
        reranked = reranker.rerank("RAG", original, top_k=3)
        original_map = {r.chunk_id: r for r in original}
        for r in reranked:
            assert r.chunk_id in original_map
            assert r.text == original_map[r.chunk_id].text


class TestCreateReranker:
    """重排器工厂函数。"""

    def test_force_mock_returns_mock(self) -> None:
        """force_mock=True 返回 Mock 重排器。"""
        reranker = create_reranker(force_mock=True)
        assert isinstance(reranker, MockReranker)

    def test_env_var_triggers_mock(self, monkeypatch) -> None:
        """环境变量 RAG_RERANKER_MOCK=1 返回 Mock 重排器。"""
        monkeypatch.setenv("RAG_RERANKER_MOCK", "1")
        reranker = create_reranker()
        assert isinstance(reranker, MockReranker)

    def test_env_var_0_returns_real_or_fallback(self, monkeypatch) -> None:
        """环境变量 RAG_RERANKER_MOCK=0 尝试真实模型，失败降级 Mock。"""
        monkeypatch.setenv("RAG_RERANKER_MOCK", "0")
        # CI 环境无模型时降级为 Mock，不抛异常
        reranker = create_reranker()
        # 可能是 BGEReranker 或 MockReranker（取决于是否已下载模型）
        assert reranker is not None


class TestBGERerankerInit:
    """BGEReranker 初始化与 rerank（使用 mock 避免下载模型）。"""

    def test_init_does_not_load_model(self) -> None:
        """构造 BGEReranker 不应立即加载模型（延迟加载）。"""
        reranker = BGEReranker(model_name="dummy-model")
        # 模型未加载
        assert reranker._model is None

    def test_rerank_with_mocked_model(self) -> None:
        """使用 mock 模型测试 rerank 完整流程。"""
        from unittest.mock import MagicMock

        reranker = BGEReranker(model_name="dummy-model")
        # 预设 mock 模型：compute_score 返回 3 个得分
        mock_model = MagicMock()
        mock_model.compute_score.return_value = [0.9, 0.3, 0.7]
        reranker._model = mock_model

        results = _make_results(3)
        reranked = reranker.rerank("RAG", results, top_k=2)

        # 应返回 top_k=2 条结果
        assert len(reranked) == 2
        # 得分最高的（0.9）应排第一
        assert reranked[0].chunk_id == "c0"
        assert reranked[0].score == 0.9
        # 第二名是得分 0.7 的 c2
        assert reranked[1].chunk_id == "c2"
        # 所有结果标记 reranked
        for r in reranked:
            assert r.metadata.get("reranked") is True

    def test_rerank_empty_results_with_model(self) -> None:
        """空结果重排应返回空列表。"""
        from unittest.mock import MagicMock

        reranker = BGEReranker(model_name="dummy-model")
        reranker._model = MagicMock()
        assert reranker.rerank("查询", [], top_k=5) == []

    def test_rerank_single_result_scalar_score(self) -> None:
        """单条结果时 compute_score 返回标量，应正确处理。"""
        from unittest.mock import MagicMock

        reranker = BGEReranker(model_name="dummy-model")
        mock_model = MagicMock()
        mock_model.compute_score.return_value = 0.85  # 标量而非列表
        reranker._model = mock_model

        results = _make_results(1)
        reranked = reranker.rerank("RAG", results, top_k=1)
        assert len(reranked) == 1
        assert reranked[0].score == 0.85

    def test_rerank_lazy_loads_model(self) -> None:
        """rerank 调用时应触发模型延迟加载。"""
        import sys
        from unittest.mock import MagicMock

        reranker = BGEReranker(model_name="dummy-model")
        mock_model = MagicMock()
        mock_model.compute_score.return_value = [0.5]

        # 构造 mock FlagEmbedding 模块
        mock_flag = MagicMock()
        mock_flag.FlagReranker.return_value = mock_model

        original = sys.modules.get("FlagEmbedding")
        sys.modules["FlagEmbedding"] = mock_flag
        try:
            results = _make_results(1)
            reranker.rerank("RAG", results, top_k=1)
            # 应调用 FlagReranker 构造函数加载模型
            mock_flag.FlagReranker.assert_called_once()
        finally:
            if original is not None:
                sys.modules["FlagEmbedding"] = original
            else:
                sys.modules.pop("FlagEmbedding", None)

    def test_rerank_marks_metadata_reranked(self) -> None:
        """重排结果应标记 metadata['reranked']=True。"""
        from unittest.mock import MagicMock

        reranker = BGEReranker(model_name="dummy-model")
        mock_model = MagicMock()
        mock_model.compute_score.return_value = [0.9, 0.8]
        reranker._model = mock_model

        results = _make_results(2)
        reranked = reranker.rerank("RAG", results, top_k=2)
        for r in reranked:
            assert r.metadata.get("reranked") is True
