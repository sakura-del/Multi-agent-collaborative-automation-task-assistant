"""锁定监控告警的逻辑（Issue #11 C 组）。

告警系统最贵的两个失败模式都不是"不报警"，而是：

1. **一直报警**：同一条故障每 2 分钟推一次，人会把群免打扰，然后真的故障也被忽略；
2. **恢复了没人知道**：只发故障不发恢复，运维得靠猜。

所以这里重点锁 `decide_notifications` 的三种转移（新故障 / 持续 / 恢复），
以及"没配 webhook 时不能崩、也不能静默吞掉"。

`ops/` 下的脚本是**故意**不 import 项目代码的（后端挂了它还得能跑），
所以测试用 importlib 按路径加载，而不是走 `app.*` 的包导入。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parents[2] / "ops"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, OPS / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def wd():
    return _load("ops_watchdog_under_test", "watchdog.py")


class TestConfigParsing:
    def test_env_file_ignores_comments_and_strips_quotes(self, tmp_path: Path, wd) -> None:
        path = tmp_path / "notify.env"
        path.write_text(
            '# 注释\n\nWECOM_WEBHOOK_URL="https://qyapi.example/x?a=1"\nEMPTY=\nBAD_LINE\n',
            encoding="utf-8",
        )
        values = wd.load_env_file(path)
        assert values["WECOM_WEBHOOK_URL"] == "https://qyapi.example/x?a=1"
        assert values["EMPTY"] == ""
        assert "BAD_LINE" not in values

    def test_missing_file_returns_empty(self, wd) -> None:
        assert wd.load_env_file("/nonexistent/notify.env") == {}

    def test_invalid_numbers_fall_back_to_defaults(self, wd) -> None:
        cfg = wd.build_config({"WATCHDOG_DISK_WARN_PCT": "abc", "WATCHDOG_REPEAT_MINUTES": "x"})
        assert cfg.disk_warn_pct == 85.0
        assert cfg.repeat_minutes == 180

    def test_thresholds_read_from_env(self, wd) -> None:
        cfg = wd.build_config({"WATCHDOG_DISK_CRIT_PCT": "95", "WATCHDOG_ERROR_MIN_REQUESTS": "50"})
        assert cfg.disk_crit_pct == 95.0
        assert cfg.error_min_requests == 50


class TestMetrics:
    def test_parses_status_classes(self, wd) -> None:
        text = (
            'maa_http_requests_total{method="GET",route="/tasks",status="200"} 90.0\n'
            'maa_http_requests_total{method="GET",route="/tasks",status="500"} 10.0\n'
            'maa_http_requests_total{method="GET",route="/tasks",status="404"} 5.0\n'
            "maa_task_tokens_total 123.0\n"
        )
        totals = wd.parse_prometheus_counters(text)
        assert totals["all"] == 105.0
        assert totals["5xx"] == 10.0
        assert totals["4xx"] == 5.0

    def test_no_previous_snapshot_yields_no_ratio(self, wd) -> None:
        ratio, _, _ = wd.compute_error_rate(
            None, {"all": 100, "5xx": 50}, min_requests=10, threshold_pct=20
        )
        assert ratio is None

    def test_below_min_requests_is_ignored(self, wd) -> None:
        """3 次请求里 3 次失败 = 100%，但那不叫故障率，叫样本太小。"""
        ratio, five, total = wd.compute_error_rate(
            {"all": 100, "5xx": 0}, {"all": 103, "5xx": 3}, min_requests=20, threshold_pct=20
        )
        assert ratio is None
        assert (five, total) == (3, 3)

    def test_ratio_over_threshold(self, wd) -> None:
        ratio, five, total = wd.compute_error_rate(
            {"all": 100, "5xx": 0}, {"all": 140, "5xx": 8}, min_requests=20, threshold_pct=20
        )
        assert ratio == 20.0
        assert (five, total) == (8, 40)

    def test_counter_reset_after_restart_is_not_treated_as_negative(self, wd) -> None:
        ratio, five, total = wd.compute_error_rate(
            {"all": 5000, "5xx": 100}, {"all": 12, "5xx": 2}, min_requests=20, threshold_pct=20
        )
        assert ratio is None  # 12 次不够下限，但绝不能算出负数或抛异常
        assert five >= 0 and total >= 0


class TestHostChecks:
    def test_disk_warn_and_crit(self, wd, monkeypatch) -> None:
        class Usage:
            total = 100 * 1024**3
            used = 88 * 1024**3
            free = 12 * 1024**3

        monkeypatch.setattr(wd.shutil, "disk_usage", lambda _p: Usage())
        issue = wd.check_disk(wd.Config())
        assert issue is not None and issue.severity == "orange"

        Usage.used = 95 * 1024**3
        Usage.free = 5 * 1024**3
        issue = wd.check_disk(wd.Config())
        assert issue is not None and issue.severity == "red"

        Usage.used = 40 * 1024**3
        Usage.free = 60 * 1024**3
        assert wd.check_disk(wd.Config()) is None

    def test_container_status_parsing(self, wd) -> None:
        output = (
            "vp-backend\tUp 6 minutes (healthy)\n"
            "vp-postgres\tUp 45 hours (healthy)\n"
            "vp-gateway\tUp 3 hours (unhealthy)\n"
            "vp-redis\tExited (1) 2 hours ago\n"
        )
        bad = wd.parse_container_status(output)
        assert "vp-gateway" in bad[0] and "unhealthy" in bad[0]
        assert any("vp-redis" in item for item in bad)
        assert not any("vp-backend" in item for item in bad)

    def test_backup_timestamp_picked_from_logs(self, wd) -> None:
        text = (
            "[db-backup] 2026-11-10T11:38:28Z 已上传 s3://x/1.sql.gz\n"
            "[db-backup] 2026-11-11T11:47:52Z 已上传 s3://x/2.sql.gz\n"
            "[db-backup] 2026-11-12T09:00:00Z dump 开始\n"
        )
        last = wd.parse_last_backup_ts(text)
        assert last == datetime(2026, 11, 11, 11, 47, 52, tzinfo=UTC)

    def test_backup_stale_triggers_issue(self, wd) -> None:
        class Result:
            returncode = 0
            stdout = ""
            stderr = "[db-backup] 2026-11-10T11:38:28Z 已上传 s3://x/1.sql.gz\n"

        issue = wd.check_backup(
            wd.Config(backup_max_age_hours=30),
            runner=lambda *a, **k: Result(),
            now=datetime(2026, 11, 12, 6, 0, tzinfo=UTC),
        )
        assert issue is not None and issue.key == "backup"

    def test_backup_fresh_no_issue(self, wd) -> None:
        class Result:
            returncode = 0
            stdout = ""
            stderr = "[db-backup] 2026-11-12T02:00:00Z 已上传 s3://x/1.sql.gz\n"

        assert (
            wd.check_backup(
                wd.Config(backup_max_age_hours=30),
                runner=lambda *a, **k: Result(),
                now=datetime(2026, 11, 12, 6, 0, tzinfo=UTC),
            )
            is None
        )


class TestNotificationStateMachine:
    NOW = datetime(2026, 11, 12, 6, 0, tzinfo=UTC)

    def _issue(self, wd, key="disk", severity="orange"):
        return wd.Issue(key, severity, "磁盘 88%", "剩 4 GB")

    def test_first_failure_notifies(self, wd) -> None:
        state, new, recovered = wd.decide_notifications(
            {}, [self._issue(wd)], now=self.NOW, repeat_minutes=180
        )
        assert len(new) == 1 and recovered == []
        assert "disk" in state["firing"]

    def test_repeat_suppressed_within_interval(self, wd) -> None:
        state, _, _ = wd.decide_notifications(
            {}, [self._issue(wd)], now=self.NOW, repeat_minutes=180
        )
        later = self.NOW + timedelta(minutes=10)
        state, new, recovered = wd.decide_notifications(
            state, [self._issue(wd)], now=later, repeat_minutes=180
        )
        assert new == [] and recovered == []

    def test_repeat_after_interval(self, wd) -> None:
        state, _, _ = wd.decide_notifications(
            {}, [self._issue(wd)], now=self.NOW, repeat_minutes=180
        )
        later = self.NOW + timedelta(minutes=181)
        _, new, _ = wd.decide_notifications(state, [self._issue(wd)], now=later, repeat_minutes=180)
        assert len(new) == 1

    def test_recovery_reported(self, wd) -> None:
        state, _, _ = wd.decide_notifications(
            {}, [self._issue(wd)], now=self.NOW, repeat_minutes=180
        )
        state, new, recovered = wd.decide_notifications(
            state, [], now=self.NOW + timedelta(minutes=5), repeat_minutes=180
        )
        assert new == [] and recovered == ["disk"]
        assert state["firing"] == {}

    def test_two_issues_tracked_independently(self, wd) -> None:
        issues = [self._issue(wd, "disk"), self._issue(wd, "backup")]
        state, new, _ = wd.decide_notifications({}, issues, now=self.NOW, repeat_minutes=180)
        assert len(new) == 2
        state, new, recovered = wd.decide_notifications(
            state,
            [self._issue(wd, "backup")],
            now=self.NOW + timedelta(minutes=5),
            repeat_minutes=180,
        )
        assert new == [] and recovered == ["disk"]


class TestMessageRendering:
    def test_alert_message_contains_severity_and_detail(self, wd) -> None:
        cfg = wd.Config(host_label="47.253.81.6")
        content = wd.format_message(
            issues=[wd.Issue("disk", "red", "磁盘 95%（严重）", "剩 2 GB")],
            recovered=[],
            cfg=cfg,
            now=datetime(2026, 11, 12, 6, 0, tzinfo=UTC),
        )
        assert "🚨" in content and "磁盘 95%" in content and "剩 2 GB" in content
        assert "47.253.81.6" in content

    def test_recovery_message(self, wd) -> None:
        content = wd.format_message(
            issues=[],
            recovered=["disk"],
            cfg=wd.Config(),
            now=datetime(2026, 11, 12, 6, 0, tzinfo=UTC),
        )
        assert "已恢复" in content and "disk" in content

    def test_nothing_to_say_renders_empty(self, wd) -> None:
        assert (
            wd.format_message(issues=[], recovered=[], cfg=wd.Config(), now=datetime.now(UTC)) == ""
        )

    def test_message_truncated_below_wecom_limit(self, wd) -> None:
        huge = wd.Issue("noise", "red", "x" * 5000, "y" * 5000)
        content = wd.format_message(
            issues=[huge], recovered=[], cfg=wd.Config(), now=datetime.now(UTC)
        )
        assert len(content.encode()) <= wd.MAX_MESSAGE_BYTES


class TestDelivery:
    def test_send_wecom_success(self, wd, monkeypatch) -> None:
        class Response:
            status = 200

            def read(self) -> bytes:
                return b'{"errcode":0,"errmsg":"ok"}'

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(wd.urllib.request, "urlopen", lambda *a, **k: Response())
        ok, detail = wd.send_wecom("https://qyapi.example/hook", "hello")
        assert ok is True and detail == "ok"

    def test_send_wecom_detects_errcode(self, wd, monkeypatch) -> None:
        """HTTP 200 但 errcode != 0（key 失效）必须判为失败，否则告警静默丢失。"""

        class Response:
            status = 200

            def read(self) -> bytes:
                return b'{"errcode":93000,"errmsg":"invalid webhook url"}'

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr(wd.urllib.request, "urlopen", lambda *a, **k: Response())
        ok, detail = wd.send_wecom("https://qyapi.example/bad", "hello")
        assert ok is False and "93000" in detail

    def test_no_webhook_does_not_raise_and_logs(self, wd, capsys) -> None:
        code = wd._deliver(wd.Config(webhook_url=""), "内容", dry_run=False)
        assert code == 0
        assert "未配置" in capsys.readouterr().err

    def test_dry_run_prints_without_network(self, wd, monkeypatch, capsys) -> None:
        def boom(*a, **k):
            raise AssertionError("dry-run 不应该发网络请求")

        monkeypatch.setattr(wd.urllib.request, "urlopen", boom)
        assert wd._deliver(wd.Config(webhook_url="https://x"), "内容", dry_run=True) == 0
        assert "内容" in capsys.readouterr().out


@pytest.fixture(scope="module")
def probe():
    """外部探测模块：判定逻辑必须区分「应用在答」与「Cloudflare 在答」。"""
    return _load("ops_probe_under_test", "probe_public.py")


class TestPublicProbe:

    def test_api_ok(self, probe, monkeypatch) -> None:
        monkeypatch.setattr(
            probe,
            "fetch",
            lambda url: (200, json.dumps({"status": "ready", "checks": {"database": "ok"}}), {}),
        )
        ok, _detail = probe.check_api()
        assert ok is True

    def test_api_unhealthy_dependency(self, probe, monkeypatch) -> None:
        monkeypatch.setattr(
            probe,
            "fetch",
            lambda url: (200, json.dumps({"status": "degraded", "checks": {"redis": "error"}}), {}),
        )
        ok, detail = probe.check_api()
        assert ok is False and "redis=error" in detail

    def test_api_html_error_page_rejected(self, probe, monkeypatch) -> None:
        """CF 的错误页是 200 + HTML 的情况也见过，不能只看状态码。"""
        monkeypatch.setattr(probe, "fetch", lambda url: (200, "<html>error 1020</html>", {}))
        ok, _ = probe.check_api()
        assert ok is False

    def test_site_marker(self, probe, monkeypatch) -> None:
        monkeypatch.setattr(probe, "fetch", lambda url: (200, '<div id="app"></div>', {}))
        assert probe.check_site()[0] is True
        monkeypatch.setattr(probe, "fetch", lambda url: (200, "<html>blocked</html>", {}))
        assert probe.check_site()[0] is False
