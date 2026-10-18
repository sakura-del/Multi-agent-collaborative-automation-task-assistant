"""指标纯函数测试（Plan 5 Task 6）——全部使用固定小样本，断言精确值。"""

from __future__ import annotations

import math

import pytest

from evaluation.metrics import (
    aggregate,
    citation_resolvability,
    duplicate_paragraph_ratio,
    error_citation_rate,
    evidence_coverage,
    mean_duration,
    ndcg_at_k,
    recall_at_k,
    reciprocal_rank,
    recovery_success,
    retry_count,
    success_rate,
    summarize_run,
    total_tokens,
)


class TestRankingMetrics:
    def test_recall_at_5_exact(self) -> None:
        retrieved = ["d1", "d2", "d3", "d4", "d5", "d6"]
        relevant = {"d2", "d5", "d9"}
        # 前 5 命中 d2、d5 → 2/3
        assert recall_at_k(retrieved, relevant, k=5) == pytest.approx(2 / 3)

    def test_recall_at_5_ignores_results_below_k(self) -> None:
        # d6 在第 6 位，不计入 top-5
        assert recall_at_k(["d1", "d2", "d3", "d4", "d5", "d6"], {"d6"}, k=5) == 0.0

    def test_recall_returns_none_without_labels(self) -> None:
        assert recall_at_k(["d1"], [], k=5) is None

    def test_reciprocal_rank_uses_first_relevant(self) -> None:
        assert reciprocal_rank(["x", "y", "d2"], {"d2"}) == pytest.approx(1 / 3)
        assert reciprocal_rank(["d1", "d2"], {"d1", "d2"}) == pytest.approx(1.0)

    def test_reciprocal_rank_zero_when_none_relevant(self) -> None:
        assert reciprocal_rank(["x", "y"], {"d9"}) == 0.0

    def test_reciprocal_rank_none_without_labels(self) -> None:
        assert reciprocal_rank(["d1"], set()) is None

    def test_ndcg_at_5_with_graded_relevance(self) -> None:
        # 位置 1 相关度 3，位置 3 相关度 2
        retrieved = ["a", "b", "c"]
        qrels = {"a": 3.0, "c": 2.0}
        dcg = 3.0 / math.log2(2) + 0 + 2.0 / math.log2(4)
        idcg = 3.0 / math.log2(2) + 2.0 / math.log2(3)
        assert ndcg_at_k(retrieved, qrels, k=5) == pytest.approx(dcg / idcg)

    def test_ndcg_perfect_ranking_is_one(self) -> None:
        assert ndcg_at_k(["a", "b"], {"a": 2.0, "b": 1.0}, k=5) == pytest.approx(1.0)

    def test_ndcg_none_without_qrels(self) -> None:
        assert ndcg_at_k(["a"], {}, k=5) is None

    def test_ndcg_none_when_ideal_dcg_is_zero(self) -> None:
        assert ndcg_at_k(["a"], {"a": 0.0}, k=5) is None


class TestCitationMetrics:
    def test_citation_resolvability_exact(self) -> None:
        markdown = "结论一 [E1]，结论二 [E2]，错误引用 [E7]。"
        # 3 个标记中 2 个可解析
        assert citation_resolvability(markdown, evidence_count=5) == pytest.approx(2 / 3)

    def test_citation_resolvability_none_without_markers(self) -> None:
        assert citation_resolvability("没有任何引用的正文", evidence_count=5) is None

    def test_evidence_coverage_exact(self) -> None:
        # 池 4 条，引用了 E1 与 E3 → 2/4
        assert evidence_coverage("[E1] 与 [E3]", evidence_count=4) == pytest.approx(0.5)

    def test_evidence_coverage_none_for_empty_pool(self) -> None:
        assert evidence_coverage("[E1]", evidence_count=0) is None

    def test_error_citation_rate_exact(self) -> None:
        cited = ["c1", "c2", "c3", "c4"]
        allowed = {"c1", "c2", "c3"}
        assert error_citation_rate(cited, allowed) == pytest.approx(0.25)

    def test_error_citation_rate_none_without_citations(self) -> None:
        assert error_citation_rate([], {"c1"}) is None

    def test_error_citation_rate_all_wrong_when_nothing_allowed(self) -> None:
        assert error_citation_rate(["c1", "c2"], []) == pytest.approx(1.0)

    def test_duplicate_paragraph_ratio_exact(self) -> None:
        markdown = "第一段\n第二段\n第一段\n第三段"
        assert duplicate_paragraph_ratio(markdown) == pytest.approx(0.25)

    def test_duplicate_paragraph_ratio_none_for_single_paragraph(self) -> None:
        assert duplicate_paragraph_ratio("只有一段") is None


class TestExecutionMetrics:
    def test_success_rate_exact(self) -> None:
        assert success_rate(["completed", "completed", "failed", "waiting_human"]) == pytest.approx(
            0.5
        )

    def test_success_rate_none_without_records(self) -> None:
        assert success_rate([]) is None

    def test_mean_duration_exact(self) -> None:
        assert mean_duration([100.0, 200.0, 300.0]) == pytest.approx(200.0)

    def test_mean_duration_none_without_records(self) -> None:
        assert mean_duration([]) is None

    def test_total_tokens_sums_both_sides(self) -> None:
        assert total_tokens([10, 20], [5]) == 35

    def test_total_tokens_none_when_empty(self) -> None:
        assert total_tokens([], []) is None

    def test_retry_count_sums_extra_attempts(self) -> None:
        # attempts 含首次：2→1 次重试，4→3 次
        assert retry_count([2, 4, 1]) == 4

    def test_retry_count_none_without_attempts(self) -> None:
        assert retry_count([]) is None

    def test_recovery_success_exact(self) -> None:
        assert recovery_success([True, True, False, True]) == pytest.approx(0.75)

    def test_recovery_success_none_without_attempts(self) -> None:
        assert recovery_success([]) is None


class TestAggregation:
    def test_aggregate_ignores_none(self) -> None:
        assert aggregate([1.0, None, 3.0]) == pytest.approx(2.0)

    def test_aggregate_all_none_returns_none(self) -> None:
        assert aggregate([None, None]) is None


class TestSummarizeRun:
    def test_full_record_produces_all_metrics(self) -> None:
        record = {
            "markdown": "## 摘要\n结论 [E1]\n[E2]\n## 参考文献\n- [1] 文献",
            "evidence_count": 2,
            "cited_chunk_ids": ["c1"],
            "allowed_chunk_ids": ["c1", "c2"],
            "retrieved_ids": ["d1", "d2"],
            "relevant_ids": ["d1", "d3"],
            "qrels": {"d1": 2.0},
            "status": "completed",
            "duration_ms": 1200.0,
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "attempts": 2,
        }

        metrics = summarize_run(record)

        assert metrics["recall_at_5"] == pytest.approx(0.5)
        assert metrics["mrr"] == pytest.approx(1.0)
        assert metrics["citation_resolvability"] == pytest.approx(1.0)
        assert metrics["evidence_coverage"] == pytest.approx(1.0)
        assert metrics["error_citation_rate"] == pytest.approx(0.0)
        assert metrics["success"] == pytest.approx(1.0)
        assert metrics["duration_ms"] == pytest.approx(1200.0)
        assert metrics["total_tokens"] == 150
        assert metrics["retries"] == 1

    def test_sparse_record_returns_none_not_zero(self) -> None:
        """关键契约：没有数据时必须是 None，不能是 0。"""
        metrics = summarize_run({"status": "failed"})

        assert metrics["recall_at_5"] is None
        assert metrics["ndcg_at_5"] is None
        assert metrics["citation_resolvability"] is None
        assert metrics["evidence_coverage"] is None
        assert metrics["error_citation_rate"] is None
        assert metrics["total_tokens"] is None
        assert metrics["retries"] is None
        # 只有"成功率"有定义（1 条失败记录 → 0.0，这是真实的 0 而不是缺失）
        assert metrics["success"] == pytest.approx(0.0)
