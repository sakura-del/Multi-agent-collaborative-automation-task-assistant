"""实验 CLI 测试（Plan 5 Task 6）：参数解析与 fixed 适配器矩阵执行。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation import cli as cli_module


class TestArgumentParsing:
    def test_defaults(self) -> None:
        args = cli_module.build_parser().parse_args(["run"])

        assert args.profiles == "all"
        assert args.repetitions == 2
        assert args.adapter == "fixed"
        assert args.retry_failed is False

    def test_subset_selection(self) -> None:
        args = cli_module.build_parser().parse_args(
            ["run", "--profiles", "full,baseline_a", "--repetitions", "1"]
        )

        assert args.profiles == "full,baseline_a"
        assert args.repetitions == 1

    def test_unknown_profile_exits(self) -> None:
        with pytest.raises(SystemExit):
            cli_module._load_selected_profiles("baseline_z")

    def test_unknown_topic_exits(self) -> None:
        with pytest.raises(SystemExit):
            cli_module._load_selected_topics("no-such-topic")

    def test_unknown_adapter_exits(self) -> None:
        args = cli_module.build_parser().parse_args(["run", "--adapter", "fixed"])
        args.adapter = "mystery"
        with pytest.raises(SystemExit):
            cli_module._build_executor("mystery", args)


class TestFixedDryRun:
    def test_four_profiles_produce_four_complete_runs(self, tmp_path: Path) -> None:
        code = cli_module.main(
            [
                "run",
                "--adapter",
                "fixed",
                "--profiles",
                "all",
                "--topic",
                "ai-rag-eval",
                "--repetitions",
                "1",
                "--output-root",
                str(tmp_path),
                "--run-set-id",
                "rs-cli-test",
            ]
        )

        assert code == 0
        run_set = tmp_path / "rs-cli-test"
        profile_dirs = sorted(path.name for path in run_set.iterdir() if path.is_dir())
        assert profile_dirs == [
            "multi_agent_hybrid_rerank",
            "multi_agent_no_rag",
            "multi_agent_vector_rag",
            "single_agent_no_rag",
        ]
        for profile in profile_dirs:
            run_dir = run_set / profile / "ai-rag-eval" / "rep-1"
            assert (run_dir / "metrics.json").is_file()
            metrics = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
            assert metrics["status"] == "completed"

    def test_resume_skips_all_runs(self, tmp_path: Path) -> None:
        base = [
            "run",
            "--adapter",
            "fixed",
            "--profiles",
            "full",
            "--topic",
            "ai-rag-eval",
            "--repetitions",
            "1",
            "--output-root",
            str(tmp_path),
            "--run-set-id",
            "rs-resume-cli",
        ]
        assert cli_module.main(base) == 0

        summary = json.loads((tmp_path / "rs-resume-cli" / "run_set.json").read_text("utf-8"))
        assert summary["completed"] == 1

        # 二次执行：应全部跳过
        assert cli_module.main(base) == 0
        summary = json.loads((tmp_path / "rs-resume-cli" / "run_set.json").read_text("utf-8"))
        assert summary["skipped"] == 1
        assert summary["completed"] == 0
