"""Runner 的 resume / 原子性 / 失败语义测试（Plan 5 Task 6）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation.models import EvaluationTopic, ExperimentConfig, load_experiment_config
from evaluation.runner import (
    REQUIRED_ARTIFACTS,
    ExperimentRunner,
    FixedAdapterExecutor,
    RunOutcome,
    RunSpec,
    build_matrix,
    run_is_complete,
    write_json_atomic,
)

CONFIG_DIR = Path(__file__).resolve().parents[2] / "evaluation" / "configs"
TOPICS_PATH = Path(__file__).resolve().parents[2] / "evaluation" / "datasets" / "topics.json"


def _profiles() -> list[ExperimentConfig]:
    return [
        load_experiment_config(CONFIG_DIR / "baseline_a.yaml"),
        load_experiment_config(CONFIG_DIR / "full.yaml"),
    ]


def _topics() -> list[EvaluationTopic]:
    from evaluation.models import load_topics

    return load_topics(TOPICS_PATH)[:2]


class _FlakyExecutor:
    """第一次失败、第二次成功的执行器（验证失败不覆盖 + 显式重试）。"""

    def __init__(self) -> None:
        self.attempts = 0

    async def execute(self, spec: RunSpec) -> RunOutcome:
        self.attempts += 1
        if self.attempts == 1:
            raise RuntimeError("模拟执行崩溃")
        return RunOutcome(
            status="completed",
            record={
                "markdown": "## 摘要\n正文 [E1]",
                "evidence_count": 1,
                "events": [{"event_type": "task_completed", "event_id": 1, "status": "completed"}],
            },
        )


class _UnstableExecutor:
    """某个课题永远失败（验证"失败不中断其他 run"）。"""

    async def execute(self, spec: RunSpec) -> RunOutcome:
        if spec.topic.topic_id.endswith("rag-eval"):
            return RunOutcome(status="failed", record={}, error="故意失败")
        return RunOutcome(
            status="completed",
            record={
                "markdown": "## 摘要 [E1]",
                "evidence_count": 1,
                "events": [{"event_type": "task_completed", "event_id": 1, "status": "completed"}],
            },
        )


class TestMatrix:
    def test_matrix_size_and_stability(self) -> None:
        specs = build_matrix(_profiles(), _topics(), repetitions=2)

        # 2 组 × 2 课题 × 2 次
        assert len(specs) == 8
        assert len({spec.run_id for spec in specs}) == 8
        # 顺序稳定：同输入两次构造，相对路径序列一致
        assert [spec.relative_dir for spec in specs] == [
            spec.relative_dir for spec in build_matrix(_profiles(), _topics(), repetitions=2)
        ]

    def test_matrix_matches_frozen_design_when_full(self) -> None:
        from evaluation.models import load_topics

        profiles = [
            load_experiment_config(CONFIG_DIR / name)
            for name in ("baseline_a.yaml", "baseline_b.yaml", "baseline_c.yaml", "full.yaml")
        ]
        topics = load_topics(TOPICS_PATH)

        specs = build_matrix(profiles, topics, repetitions=2)

        assert len(specs) == 120, "4 组 × 15 课题 × 2 次"

    def test_matrix_rejects_non_positive_repetitions(self) -> None:
        with pytest.raises(ValueError, match="repetitions"):
            build_matrix(_profiles(), _topics(), repetitions=0)


class TestCompletionDetection:
    def test_incomplete_when_any_artifact_missing(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        write_json_atomic(run_dir / "config.json", {"a": 1})
        assert run_is_complete(run_dir) is False

    def test_incomplete_when_metrics_unparsable(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        for name in REQUIRED_ARTIFACTS:
            (run_dir / name).write_text("{}", encoding="utf-8")
        (run_dir / "metrics.json").write_text("{not json", encoding="utf-8")

        assert run_is_complete(run_dir) is False

    def test_complete_when_all_artifacts_present(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "result.md").write_text("# x", encoding="utf-8")
        (run_dir / "events.jsonl").write_text("{}\n", encoding="utf-8")
        write_json_atomic(run_dir / "config.json", {"a": 1})
        write_json_atomic(run_dir / "task.json", {"b": 2})
        write_json_atomic(run_dir / "review.json", {})
        write_json_atomic(run_dir / "metrics.json", {"success": 1.0})

        assert run_is_complete(run_dir) is True


class TestAtomicWrite:
    def test_no_tmp_file_left_behind(self, tmp_path: Path) -> None:
        target = tmp_path / "nested" / "metrics.json"
        write_json_atomic(target, {"x": 1})

        assert target.is_file()
        assert not list(target.parent.glob("*.tmp"))

    def test_existing_file_is_replaced(self, tmp_path: Path) -> None:
        target = tmp_path / "metrics.json"
        write_json_atomic(target, {"version": 1})
        write_json_atomic(target, {"version": 2})

        assert json.loads(target.read_text(encoding="utf-8"))["version"] == 2


class TestRunnerExecution:
    async def test_runs_write_all_artifacts(self, tmp_path: Path) -> None:
        runner = ExperimentRunner(
            FixedAdapterExecutor(), output_root=tmp_path, run_set_id="rs-test"
        )
        specs = build_matrix(_profiles(), _topics(), repetitions=1)

        report = await runner.run(specs)

        assert report.ok is True
        assert report.completed == len(specs)
        for spec in specs:
            run_dir = runner.run_dir(spec)
            for name in REQUIRED_ARTIFACTS:
                assert (run_dir / name).is_file(), f"{spec.run_id} 缺 {name}"
            metrics = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
            assert metrics["status"] == "completed"
            assert metrics["citation_resolvability"] == pytest.approx(1.0)
        # run_set.json 摘要
        summary = json.loads((runner.root / "run_set.json").read_text(encoding="utf-8"))
        assert summary["ok"] is True
        assert summary["total"] == len(specs)

    async def test_second_pass_skips_completed_runs(self, tmp_path: Path) -> None:
        executor = FixedAdapterExecutor()
        runner = ExperimentRunner(executor, output_root=tmp_path, run_set_id="rs-resume")
        specs = build_matrix(_profiles(), _topics(), repetitions=1)

        await runner.run(specs)
        first_call_count = len(executor.calls)

        report = await runner.run(specs)

        assert report.skipped == len(specs)
        assert report.completed == 0
        assert len(executor.calls) == first_call_count, "resume 不应重复执行"

    async def test_executor_exception_marks_run_failed_and_keeps_logs(self, tmp_path: Path) -> None:
        runner = ExperimentRunner(_FlakyExecutor(), output_root=tmp_path, run_set_id="rs-fail")
        specs = build_matrix(_profiles()[:1], _topics()[:1], repetitions=1)

        report = await runner.run(specs)

        assert report.failed == 1
        run_dir = runner.run_dir(specs[0])
        assert (run_dir / "error.txt").is_file(), "失败运行必须保留现场"
        metrics = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
        assert metrics["status"] == "failed"
        # 失败目录不算完成 → 下次仍会被执行
        assert run_is_complete(run_dir) is False

    async def test_retry_failed_archives_previous_attempt(self, tmp_path: Path) -> None:
        executor = _FlakyExecutor()
        runner = ExperimentRunner(executor, output_root=tmp_path, run_set_id="rs-retry")
        specs = build_matrix(_profiles()[:1], _topics()[:1], repetitions=1)
        await runner.run(specs)

        report = await runner.run(specs, retry_failed=True)

        assert report.completed == 1
        run_dir = runner.run_dir(specs[0])
        archives = list((run_dir / "attempts").glob("retry-*"))
        assert archives, "重试必须保留旧现场"
        assert (archives[0] / "error.txt").is_file()
        assert run_is_complete(run_dir) is True

    async def test_one_failed_run_does_not_block_others(self, tmp_path: Path) -> None:
        runner = ExperimentRunner(
            _UnstableExecutor(), output_root=tmp_path, run_set_id="rs-partial"
        )
        specs = build_matrix(_profiles()[:1], _topics(), repetitions=1)

        report = await runner.run(specs)

        assert report.failed == 1
        assert report.completed == len(specs) - 1
        assert report.ok is False
        assert len(report.failed_runs) == 1

    async def test_progress_callback_reports_each_run(self, tmp_path: Path) -> None:
        runner = ExperimentRunner(
            FixedAdapterExecutor(), output_root=tmp_path, run_set_id="rs-progress"
        )
        specs = build_matrix(_profiles()[:1], _topics()[:1], repetitions=1)
        seen: list[tuple[int, int, str]] = []

        await runner.run(
            specs, on_progress=lambda i, total, result: seen.append((i, total, result))
        )

        assert seen == [(1, 1, "completed")]

    async def test_fixed_adapter_distinguishes_retrieval_profiles(self) -> None:
        """无 RAG 画像不应有检索命中（保证消融在数据上可区分）。"""
        executor = FixedAdapterExecutor()
        specs = build_matrix(_profiles(), _topics()[:1], repetitions=1)
        by_profile = {spec.profile.name: spec for spec in specs}

        no_rag = await executor.execute(by_profile["single_agent_no_rag"])
        full = await executor.execute(by_profile["multi_agent_hybrid_rerank"])

        assert no_rag.record["retrieved_ids"] == []
        assert full.record["retrieved_ids"], "有 RAG 的画像应有检索结果"


class TestPayloadTrimming:
    def test_metrics_file_does_not_duplicate_markdown(self, tmp_path: Path) -> None:
        runner = ExperimentRunner(
            FixedAdapterExecutor(), output_root=tmp_path, run_set_id="rs-trim"
        )
        specs = build_matrix(_profiles()[:1], _topics()[:1], repetitions=1)

        import asyncio

        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(runner.run(specs))

        metrics_path = runner.run_dir(specs[0]) / "metrics.json"
        payload = json.loads(metrics_path.read_text(encoding="utf-8"))
        assert "markdown" not in payload
        assert "events" not in payload, "事件只存 events.jsonl，不重复进 metrics.json"
        # 正文在 result.md 里
        assert (runner.run_dir(specs[0]) / "result.md").read_text(encoding="utf-8")
