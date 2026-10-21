"""指标核心测试（Phase 2 · 可观测性 Task 1）。

## 本文件锁定

1. **指标定义即契约**：计划 §1 列出的名字/类型必须全部存在，且不许悄悄改名
   （改了监控侧就断，而监控断掉是**静默**的——没有报错，只是图空了）；
2. **打点绝不抛异常**：指标组件故障绝不能拖垮业务请求/任务
   （与限流 `fail-open`、配额 `fail-open` 同一原则）；
3. **拼错指标名是空操作**：不能创建垃圾指标，也不能炸；
4. **渲染是 Prometheus 文本格式**：`# TYPE` / `_total` 后缀等约定必须对。
"""

from __future__ import annotations

import pytest

from app.core import metrics


@pytest.fixture(autouse=True)
def _clean_registry() -> None:
    """每个用例前清空，避免用例间互相污染计数。"""
    metrics.reset_for_tests()


class TestMetricDefinitions:
    def test_all_planned_counters_exist(self) -> None:
        """计划 §1 的计数器必须全部存在（名字就是契约）。"""
        collected = {metric.name: metric.type for metric in metrics.REGISTRY.collect()}
        expected = {
            "maa_http_requests",
            "maa_tasks",
            "maa_task_tokens",
            "maa_task_nodes",
            "maa_literature_source_errors",
            "maa_rate_limit_rejected",
            "maa_quota_rejected",
        }
        missing = expected - set(collected)
        assert not missing, f"缺少计数器：{sorted(missing)}"

        wrong_type = {name: collected[name] for name in expected if collected[name] != "counter"}
        assert not wrong_type, f"这些指标不是 counter：{wrong_type}"

    def test_all_planned_histograms_exist(self) -> None:
        collected = {metric.name: metric.type for metric in metrics.REGISTRY.collect()}
        expected = {"maa_http_request_duration_seconds", "maa_task_duration_seconds"}
        missing = expected - set(collected)
        assert not missing, f"缺少直方图：{sorted(missing)}"

        wrong_type = {name: collected[name] for name in expected if collected[name] != "histogram"}
        assert not wrong_type, f"这些指标不是 histogram：{wrong_type}"

    def test_uses_dedicated_registry_not_default(self) -> None:
        """必须用**独立 registry**。

        用默认全局 registry 的后果：测试间状态串扰，且重复 import 会
        `Duplicated timeseries in CollectorRegistry` 直接炸。
        """
        from prometheus_client import REGISTRY as default_registry

        assert metrics.REGISTRY is not default_registry


class TestRecording:
    def test_inc_counter_increments(self) -> None:
        metrics.inc_counter("maa_quota_rejected")
        metrics.inc_counter("maa_quota_rejected")

        text = metrics.render()
        assert "maa_quota_rejected_total 2.0" in text

    def test_inc_counter_supports_labels(self) -> None:
        metrics.inc_counter("maa_tasks", status="completed")
        metrics.inc_counter("maa_tasks", status="completed")
        metrics.inc_counter("maa_tasks", status="failed")

        text = metrics.render()
        assert 'maa_tasks_total{status="completed"} 2.0' in text
        assert 'maa_tasks_total{status="failed"} 1.0' in text

    def test_inc_counter_supports_amount(self) -> None:
        """token 是累加量，不是次数。"""
        metrics.inc_counter("maa_task_tokens", amount=1500)
        metrics.inc_counter("maa_task_tokens", amount=500)

        assert "maa_task_tokens_total 2000.0" in metrics.render()

    def test_observe_histogram_records_value(self) -> None:
        metrics.observe_histogram("maa_task_duration_seconds", 1.5, status="completed")

        text = metrics.render()
        assert "maa_task_duration_seconds_count" in text
        assert "maa_task_duration_seconds_sum" in text

    def test_render_is_prometheus_text_format(self) -> None:
        metrics.inc_counter("maa_quota_rejected")

        text = metrics.render()
        # 注意：Prometheus 文本格式里 counter 的 TYPE 行也带 `_total` 后缀
        # （`# TYPE maa_quota_rejected_total counter`），这是规范要求，不是笔误
        assert "# TYPE maa_quota_rejected_total counter" in text
        assert "# HELP maa_quota_rejected_total" in text

    def test_render_is_parseable_text(self) -> None:
        """能被 prometheus_client 自己的解析器读回来（格式真的合法）。"""
        from prometheus_client.parser import text_string_to_metric_families

        metrics.inc_counter("maa_tasks", status="completed", amount=3)
        families = list(text_string_to_metric_families(metrics.render()))

        names = {family.name for family in families}
        assert "maa_tasks" in names


class TestFailOpen:
    def test_inc_counter_swallows_errors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """指标故障绝不影响业务（fail-open）。"""

        def boom(*args: object, **kwargs: object) -> None:
            raise RuntimeError("registry 炸了")

        monkeypatch.setattr(metrics, "_inc_raw", boom)

        # 关键断言：不抛异常
        metrics.inc_counter("maa_quota_rejected")

    def test_observe_histogram_swallows_errors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*args: object, **kwargs: object) -> None:
            raise RuntimeError("registry 炸了")

        monkeypatch.setattr(metrics, "_observe_raw", boom)

        metrics.observe_histogram("maa_task_duration_seconds", 1.0, status="completed")

    def test_render_swallows_errors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """连渲染都不能炸：`/metrics` 挂了不该把运维挡在门外。"""

        def boom(*args: object, **kwargs: object) -> str:
            raise RuntimeError("generate_latest 炸了")

        monkeypatch.setattr(metrics, "_render_raw", boom)

        assert metrics.render() == ""


class TestUnknownNames:
    def test_unknown_counter_name_is_noop(self) -> None:
        """拼错名字不能创建垃圾指标（也不能炸）。"""
        before = metrics.render()
        metrics.inc_counter("maa_does_not_exist")

        assert metrics.render() == before

    def test_unknown_histogram_name_is_noop(self) -> None:
        before = metrics.render()
        metrics.observe_histogram("maa_nope_seconds", 1.0)

        assert metrics.render() == before

    def test_unknown_label_name_is_noop(self) -> None:
        """标签名拼错也是空操作（指标标签是契约）。"""
        before = metrics.render()
        metrics.inc_counter("maa_tasks", wrong_label="x")

        assert metrics.render() == before
