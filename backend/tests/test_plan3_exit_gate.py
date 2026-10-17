"""Plan 3 Exit Gate harness 的纯函数测试（无需网络 / 数据库 / 真实模型）。"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.plan3_exit_gate import (
    extract_markers,
    parse_cli_summary,
    sha256_file,
    unresolved_markers,
)


class TestMarkerHelpers:
    def test_extract_markers_keeps_numbers(self) -> None:
        assert extract_markers("结论 [E1]、[E2] 与重复 [E2]") == [1, 2, 2]
        assert extract_markers("") == []

    def test_unresolved_markers_flags_out_of_range(self) -> None:
        assert unresolved_markers("见 [E1] 与 [E3]", 2) == [3]
        assert unresolved_markers("见 [E0]", 2) == [0]
        assert unresolved_markers("见 [E1] 与 [E2]", 2) == []


class TestCliSummaryParsing:
    def test_parses_summary_after_logs(self) -> None:
        stdout = (
            "2026-10-17 [info] 离线语料: 10 篇\n"
            '{"task_id": "abc", "status": "completed", "artifacts": []}\n'
        )
        summary = parse_cli_summary(stdout)
        assert summary is not None
        assert summary["task_id"] == "abc"

    def test_ignores_log_json_objects(self) -> None:
        stdout = '{"event": "app_start", "level": "info"}\n'
        assert parse_cli_summary(stdout) is None


class TestHashCheck:
    def test_sha256_matches_hashlib(self, tmp_path: Path) -> None:
        import hashlib

        target = tmp_path / "a.md"
        target.write_bytes(b"# title\n")
        assert sha256_file(target) == hashlib.sha256(b"# title\n").hexdigest()


class TestGateReportShape:
    def test_report_is_json_serializable(self) -> None:
        report = {"ok": False, "phases": [{"phase": "corpus", "ok": True}], "failures": []}
        assert json.loads(json.dumps(report))["phases"][0]["phase"] == "corpus"
