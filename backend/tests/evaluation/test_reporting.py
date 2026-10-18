"""报告生成测试（Plan 5 Task 7）：全部基于固定 fixture，不依赖真实实验。"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from evaluation.reporting import (
    ALL_METRICS,
    PROFILE_ORDER,
    bootstrap_ci,
    build_aggregate_table,
    build_significance_table,
    build_summary_rows,
    collect_run_records,
    generate_report,
)

# 聚合/显著性表的指标列由实现常量决定（确定性 + 执行类指标）
METRIC_KEYS = list(ALL_METRICS)


def _write_run(
    root: Path,
    profile: str,
    topic: str,
    repetition: int,
    *,
    metrics: dict[str, object] | None = None,
    status: str = "completed",
) -> Path:
    run_dir = root / profile / topic / f"rep-{repetition}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "config.json").write_text(json.dumps({"name": profile}), encoding="utf-8")
    (run_dir / "task.json").write_text(
        json.dumps({"profile": profile, "topic_id": topic, "repetition": repetition}),
        encoding="utf-8",
    )
    (run_dir / "events.jsonl").write_text('{"event_type":"task_completed"}\n', encoding="utf-8")
    (run_dir / "result.md").write_text("## 摘要\n正文 [E1]", encoding="utf-8")
    (run_dir / "review.json").write_text(json.dumps({"final_markdown": "## 摘要 [E1]"}), "utf-8")
    payload = {"status": status, **(metrics or {})}
    (run_dir / "metrics.json").write_text(json.dumps(payload), encoding="utf-8")
    return run_dir


@pytest.fixture
def run_set(tmp_path: Path) -> Path:
    """4 组 × 1 课题 × 2 次，覆盖全部指标且刻意做出组间差异。"""
    root = tmp_path / "rs-fixture"
    root.mkdir()
    (root / "run_set.json").write_text(json.dumps({"run_set_id": "rs-fixture"}), encoding="utf-8")
    for profile, offset in zip(PROFILE_ORDER, [0.0, 0.05, 0.1, 0.15], strict=True):
        for repetition in (1, 2):
            _write_run(
                root,
                profile,
                "ai-rag-eval",
                repetition,
                metrics={
                    "citation_resolvability": 0.8 + offset,
                    "evidence_coverage": 0.6 + offset,
                    "error_citation_rate": 0.2 - offset,
                    "duplicate_paragraph_ratio": 0.1 - offset / 2,
                    "recall_at_5": 0.5 + offset,
                    "ndcg_at_5": 0.4 + offset,
                    "success": 1.0,
                    "duration_ms": 1000.0 + 100 * repetition + offset * 1000,
                    "total_tokens": 200 + int(offset * 1000),
                    "retries": 0.0,
                    "recovery_success": 1.0,
                },
            )
    return root


class TestCollection:
    def test_collects_one_record_per_run(self, run_set: Path) -> None:
        records = collect_run_records(run_set)

        assert len(records) == 8  # 4 组 × 1 课题 × 2 次
        assert {record["profile"] for record in records} == set(PROFILE_ORDER)
        assert all(record["run_id"] for record in records)

    def test_records_missing_and_failed_runs(self, run_set: Path) -> None:
        """失败/缺失必须被记录，不能悄悄丢掉。"""
        _write_run(run_set, PROFILE_ORDER[0], "ai-other-topic", 1, status="failed")
        # 缺 metrics.json 的目录 → 视为不完整
        broken = run_set / PROFILE_ORDER[0] / "ai-broken-topic" / "rep-1"
        broken.mkdir(parents=True)
        (broken / "config.json").write_text("{}", encoding="utf-8")

        records = collect_run_records(run_set)
        statuses = {record["status"] for record in records}

        assert "failed" in statuses
        assert "incomplete" in statuses
        assert len(records) == 10

    def test_raises_for_missing_run_set(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            collect_run_records(tmp_path / "nope")


class TestBootstrap:
    def test_ci_brackets_the_mean(self) -> None:
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        low, high = bootstrap_ci(values, iterations=500, seed=7)

        assert low < 3.0 < high
        assert low >= 1.0 and high <= 5.0

    def test_ci_is_deterministic_for_same_seed(self) -> None:
        values = [0.5, 0.7, 0.9, 1.0]
        assert bootstrap_ci(values, iterations=300, seed=11) == bootstrap_ci(
            values, iterations=300, seed=11
        )

    def test_ci_none_for_empty_or_tiny_samples(self) -> None:
        assert bootstrap_ci([]) == (None, None)
        assert bootstrap_ci([1.0]) == (None, None)


class TestAggregateTable:
    def test_aggregate_has_mean_median_ci_per_profile_metric(self, run_set: Path) -> None:
        records = collect_run_records(run_set)
        rows = build_aggregate_table(records)

        # 4 组 × 全部指标（每组每指标一行）
        assert len(rows) == 4 * len(METRIC_KEYS)
        keys = {(row["profile"], row["metric"]) for row in rows}
        assert (PROFILE_ORDER[0], "citation_resolvability") in keys
        for row in rows:
            assert set(row) >= {"profile", "metric", "n", "mean", "median", "ci_low", "ci_high"}
            assert row["n"] == 2, f"{row['profile']}/{row['metric']} 应有 2 次运行"
            assert row["mean"] is not None

    def test_aggregate_uses_only_completed_runs(self, run_set: Path) -> None:
        _write_run(
            run_set,
            PROFILE_ORDER[0],
            "ai-rag-eval",
            3,
            metrics={"citation_resolvability": 0.0},
            status="failed",
        )
        records = collect_run_records(run_set)
        rows = build_aggregate_table(records)

        target = next(
            row
            for row in rows
            if row["profile"] == PROFILE_ORDER[0] and row["metric"] == "citation_resolvability"
        )
        # 失败运行不参与聚合，n 仍为 2
        assert target["n"] == 2
        assert target["mean"] > 0.5

    def test_metric_missing_everywhere_yields_none(self, run_set: Path) -> None:
        for path in run_set.glob("*/*/rep-*"):
            metrics_path = path / "metrics.json"
            payload = json.loads(metrics_path.read_text(encoding="utf-8"))
            payload.pop("total_tokens", None)
            metrics_path.write_text(json.dumps(payload), encoding="utf-8")

        rows = build_aggregate_table(collect_run_records(run_set))
        target = next(row for row in rows if row["metric"] == "total_tokens")

        assert target["n"] == 0
        assert target["mean"] is None, "缺数据必须是 None，不能是 0"


class TestSignificance:
    def test_paired_wilcoxon_against_baseline_a(self, run_set: Path) -> None:
        records = collect_run_records(run_set)
        rows = build_significance_table(records, baseline=PROFILE_ORDER[0])

        # 3 个非基线组 × 6 指标
        assert len(rows) == 3 * len(METRIC_KEYS)
        assert {row["baseline"] for row in rows} == {PROFILE_ORDER[0]}
        target = next(
            row
            for row in rows
            if row["metric"] == "citation_resolvability" and row["profile"] == PROFILE_ORDER[3]
        )
        assert set(target) >= {"profile", "baseline", "metric", "n_pairs", "statistic", "p_value"}
        assert target["n_pairs"] == 2

    def test_significance_none_with_insufficient_pairs(self, tmp_path: Path) -> None:
        root = tmp_path / "rs-one"
        root.mkdir()
        (root / "run_set.json").write_text("{}", encoding="utf-8")
        for profile in PROFILE_ORDER[:2]:
            _write_run(root, profile, "ai-rag-eval", 1, metrics={"citation_resolvability": 0.5})

        rows = build_significance_table(collect_run_records(root), baseline=PROFILE_ORDER[0])
        target = next(row for row in rows if row["metric"] == "citation_resolvability")

        assert target["p_value"] is None, "少于 2 对样本时不得编造 p 值"

    def test_significance_skips_baseline_itself(self, run_set: Path) -> None:
        rows = build_significance_table(collect_run_records(run_set), baseline=PROFILE_ORDER[0])
        assert all(row["profile"] != PROFILE_ORDER[0] for row in rows)


class TestReportGeneration:
    def test_generates_all_artifacts(self, run_set: Path, tmp_path: Path) -> None:
        output = tmp_path / "report"
        artifacts = generate_report(run_set, output)

        expected = {
            "summary.csv",
            "aggregate.csv",
            "significance.csv",
            "report.md",
            "figures/retrieval.png",
            "figures/content_quality.png",
            "figures/citations.png",
            "figures/latency_tokens.png",
            "figures/recovery.png",
        }
        produced = {path.relative_to(output).as_posix() for path in artifacts}
        assert expected <= produced
        for relative in expected:
            assert (output / relative).is_file(), f"缺少 {relative}"
            assert (output / relative).stat().st_size > 0, f"{relative} 为空"

    def test_summary_csv_has_one_row_per_run(self, run_set: Path, tmp_path: Path) -> None:
        output = tmp_path / "report"
        generate_report(run_set, output)

        with (output / "summary.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))

        assert len(rows) == 8
        assert {"run_id", "profile", "topic_id", "repetition", "status"} <= set(rows[0])

    def test_aggregate_csv_columns(self, run_set: Path, tmp_path: Path) -> None:
        output = tmp_path / "report"
        generate_report(run_set, output)

        with (output / "aggregate.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))

        assert len(rows) == 4 * len(METRIC_KEYS)
        assert {"profile", "metric", "n", "mean", "median", "ci_low", "ci_high"} <= set(rows[0])

    def test_report_markdown_records_run_set_and_missing_runs(
        self, run_set: Path, tmp_path: Path
    ) -> None:
        _write_run(run_set, PROFILE_ORDER[0], "ai-failed-topic", 1, status="failed")
        output = tmp_path / "report"
        generate_report(run_set, output)

        text = (output / "report.md").read_text(encoding="utf-8")

        assert "rs-fixture" in text, "报告必须标注 run-set ID"
        assert "ai-failed-topic" in text, "缺失/失败运行必须被记录下来"
        assert "figures/citations.png" in text, "图表必须被 markdown 引用"
        assert "aggregate.csv" in text

    def test_report_keeps_llm_judge_separate(self, run_set: Path, tmp_path: Path) -> None:
        """LLM Judge 只能出现在单独章节，不得并入主质量分。"""
        output = tmp_path / "report"
        generate_report(run_set, output)
        text = (output / "report.md").read_text(encoding="utf-8")

        assert "LLM Judge" in text
        judge_section = text.split("LLM Judge", 1)[1]
        assert "补充" in judge_section or "supplementary" in judge_section.lower()

    def test_report_is_deterministic(self, run_set: Path, tmp_path: Path) -> None:
        first = tmp_path / "a"
        second = tmp_path / "b"
        generate_report(run_set, first, seed=42)
        generate_report(run_set, second, seed=42)

        for name in ("summary.csv", "aggregate.csv", "significance.csv"):
            assert (first / name).read_text(encoding="utf-8") == (second / name).read_text(
                encoding="utf-8"
            ), f"{name} 两次生成不一致"


class TestSummaryRows:
    def test_summary_row_fields(self, run_set: Path) -> None:
        records = collect_run_records(run_set)
        rows = build_summary_rows(records)

        assert len(rows) == len(records)
        assert {"run_id", "profile", "topic_id", "repetition", "status"} <= set(rows[0])
        # 指标列都出现在表头
        assert any(key in rows[0] for key in ("citation_resolvability", "success"))
