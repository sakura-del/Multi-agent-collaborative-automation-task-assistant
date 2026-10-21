"""指标核心（Phase 2 · 可观测性）。

## 设计要点

1. **独立 `CollectorRegistry`，不用默认全局 registry**。
   默认 registry 的后果：测试之间状态串扰，且模块被重复 import 时直接抛
   `Duplicated timeseries in CollectorRegistry`（进程起不来）。

2. **打点全部 fail-open**。指标是旁路能力，任何故障（registry 异常、序列化失败）
   都只记 debug 日志、绝不向上抛——与限流/配额的 fail-open 同一原则。
   反例代价：一次 registry 故障让所有建任务请求 500，比"没有监控"糟糕得多。

3. **指标名与标签名是契约**（`_COUNTERS` / `_HISTOGRAMS` 查表）。
   拼错名字 → 空操作 + warning，**不创建垃圾指标**；标签拼错同理。
   这样"改错名字"表现为"图空了"而不是"静默生成两个指标，一个永远为 0"。

4. **不暴露任何 PII**：`/metrics` 通常无鉴权，标签里只允许低基数枚举值
   （状态、节点类型、源名、路由模板），绝不允许 `user_id` / 任务标题 / 真实路径。

## 为什么不引入 APM

当前是单进程 + 五容器，结构化日志（带 `trace_id`/`task_id`）已能定位单次请求，
Prometheus 指标能回答"整体趋势/成功率/成本"。引入 OpenTelemetry / Jaeger 需要改造
所有 LLM 与 HTTP 调用点，成本远高于收益——等有真实排障瓶颈再单独立项。
"""

from __future__ import annotations

from typing import Final

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)

from app.core.logging import get_logger

logger = get_logger(__name__)

#: 专用 registry（见模块 docstring 第 1 条）。
REGISTRY: Final[CollectorRegistry] = CollectorRegistry()

# ===== HTTP 层 =====

_HTTP_REQUESTS = Counter(
    "maa_http_requests",
    "HTTP 请求总数",
    labelnames=("method", "route", "status"),
    registry=REGISTRY,
)

_HTTP_DURATION = Histogram(
    "maa_http_request_duration_seconds",
    "HTTP 请求耗时（秒）",
    labelnames=("method", "route"),
    # 桶覆盖：快速 API（50ms）→ 常规（<500ms）→ 慢端点（上传/渲染，数秒）→ 超时边缘
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
    registry=REGISTRY,
)

# ===== 任务层 =====

_TASKS = Counter(
    "maa_tasks",
    "进入终态的任务数（completed / failed / cancelled / waiting_human）",
    labelnames=("status",),
    registry=REGISTRY,
)

_TASK_TOKENS = Counter(
    "maa_task_tokens",
    "任务累计消耗 token（与 task.used_tokens 口径一致）",
    registry=REGISTRY,
)

_TASK_DURATION = Histogram(
    "maa_task_duration_seconds",
    "任务从创建到终态的耗时（秒）",
    labelnames=("status",),
    # 文献综述实测：快则 1 分钟，慢则 15 分钟以上（服务器 CPU 慢）
    buckets=(30.0, 60.0, 120.0, 300.0, 600.0, 900.0, 1800.0, 3600.0),
    registry=REGISTRY,
)

_TASK_NODES = Counter(
    "maa_task_nodes",
    "DAG 节点执行结果数",
    labelnames=("node_type", "status"),
    registry=REGISTRY,
)

# ===== 外部依赖与准入控制 =====

_SOURCE_ERRORS = Counter(
    "maa_literature_source_errors",
    "文献检索源失败次数（单源故障已隔离，这里只做可观测）",
    labelnames=("source",),
    registry=REGISTRY,
)

_RATE_LIMIT_REJECTED = Counter(
    "maa_rate_limit_rejected",
    "被限流拒绝（429）的请求数",
    labelnames=("route",),
    registry=REGISTRY,
)

_QUOTA_REJECTED = Counter(
    "maa_quota_rejected",
    "因配额耗尽被拒绝（429）的请求数",
    registry=REGISTRY,
)

#: 计数器名 → (指标对象, 合法标签名)。**名字与标签都是对外契约**。
_COUNTERS: Final[dict[str, tuple[Counter, frozenset[str]]]] = {
    "maa_http_requests": (_HTTP_REQUESTS, frozenset({"method", "route", "status"})),
    "maa_tasks": (_TASKS, frozenset({"status"})),
    "maa_task_tokens": (_TASK_TOKENS, frozenset()),
    "maa_task_nodes": (_TASK_NODES, frozenset({"node_type", "status"})),
    "maa_literature_source_errors": (_SOURCE_ERRORS, frozenset({"source"})),
    "maa_rate_limit_rejected": (_RATE_LIMIT_REJECTED, frozenset({"route"})),
    "maa_quota_rejected": (_QUOTA_REJECTED, frozenset()),
}

#: 直方图名 → (指标对象, 合法标签名)。
_HISTOGRAMS: Final[dict[str, tuple[Histogram, frozenset[str]]]] = {
    "maa_http_request_duration_seconds": (
        _HTTP_DURATION,
        frozenset({"method", "route"}),
    ),
    "maa_task_duration_seconds": (_TASK_DURATION, frozenset({"status"})),
}


def _inc_raw(metric: Counter, *, amount: float, labels: dict[str, str]) -> None:
    """真正的打点（测试里会被 monkeypatch 成抛异常，以验证 fail-open）。"""
    target = metric.labels(**labels) if labels else metric
    target.inc(amount)


def _observe_raw(metric: Histogram, *, value: float, labels: dict[str, str]) -> None:
    target = metric.labels(**labels) if labels else metric
    target.observe(value)


def _render_raw() -> str:
    return generate_latest(REGISTRY).decode("utf-8")


def inc_counter(name: str, *, amount: float = 1.0, **labels: str) -> None:
    """计数器 +`amount`（默认 1）；**任何异常都吞掉**（fail-open）。

    未知指标名 / 未知标签名 → 空操作 + warning，避免拼错名字悄悄产生垃圾指标。
    """
    entry = _COUNTERS.get(name)
    if entry is None:
        logger.warning("metric_unknown_counter", metric=name)
        return
    metric, allowed = entry
    if not set(labels) <= allowed:
        logger.warning(
            "metric_unknown_labels",
            metric=name,
            unexpected=sorted(set(labels) - allowed),
        )
        return
    try:
        _inc_raw(metric, amount=float(amount), labels={k: str(v) for k, v in labels.items()})
    except Exception as exc:  # pragma: no cover - 防御性兜底
        logger.debug("metric_inc_failed", metric=name, error=str(exc))


def observe_histogram(name: str, value: float, **labels: str) -> None:
    """观察一个数值；**任何异常都吞掉**（fail-open）。"""
    entry = _HISTOGRAMS.get(name)
    if entry is None:
        logger.warning("metric_unknown_histogram", metric=name)
        return
    metric, allowed = entry
    if not set(labels) <= allowed:
        logger.warning(
            "metric_unknown_labels",
            metric=name,
            unexpected=sorted(set(labels) - allowed),
        )
        return
    try:
        _observe_raw(metric, value=float(value), labels={k: str(v) for k, v in labels.items()})
    except Exception as exc:  # pragma: no cover - 防御性兜底
        logger.debug("metric_observe_failed", metric=name, error=str(exc))


def render() -> str:
    """渲染为 Prometheus 文本格式；失败返回空串（`/metrics` 不能把运维挡在门外）。"""
    try:
        return _render_raw()
    except Exception as exc:  # pragma: no cover - 防御性兜底
        logger.warning("metric_render_failed", error=str(exc))
        return ""


def reset_for_tests() -> None:
    """清空全部计数与直方图（**仅供测试**，不对外暴露成端点）。

    为什么不是"重建 registry"：`Counter`/`Histogram` 对象被模块级引用，
    重建 registry 后旧对象仍指向旧 registry，打点会写到已经没人读的地方。

    为什么分两种处理：**无标签**的计数器/直方图没有 `_metrics` 字典
    （数据直接在 `_value` / `_sum` 上），有标签的才有 `_metrics`。
    直接统一访问 `_metrics` 会 `AttributeError`（本文件首版就踩了这个）。
    """
    for counter, allowed in _COUNTERS.values():
        if allowed:
            counter._metrics.clear()
        else:
            counter._value.set(0)
    for histogram, allowed in _HISTOGRAMS.values():
        if allowed:
            histogram._metrics.clear()
        else:
            # 无标签直方图：清空累积桶与和。
            # 本项目的两个直方图都有标签，这个分支实际不可达；保留是为了
            # 「以后加无标签直方图时不炸」，用 getattr 避免绑死在私有结构上。
            histogram._sum.set(0)  # type: ignore[attr-defined]
            for bucket in getattr(histogram, "_buckets", ()):
                bucket.set(0)
