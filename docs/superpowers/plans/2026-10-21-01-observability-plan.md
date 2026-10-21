# 可观测性（指标 / 成本看板）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让运营者能回答三个问题：① 任务现在成功率多少、卡在哪个节点；② 每次任务烧了多少 token / 多少钱；③ 检索源与限流是否在拖后腿。全部指标可从 `/metrics` 抓取（Prometheus 格式），并在前端有一个只看自己数据的成本/健康看板。

**上游：** [Phase 2 总纲](2026-10-20-00-phase2-overview.md) §2.1

---

## 0. 现状与范围校正

### 0.1 已有

| 能力 | 现状 |
| --- | --- |
| 结构化日志 | ✅ `app/core/logging.py`（structlog 风格，带 `trace_id` / `task_id`） |
| 请求 ID | ✅ `RequestIDMiddleware` |
| 健康检查 | ✅ `/api/v1/health`（存活）+ `/api/v1/health/ready`（真实依赖探测） |
| 限流计数 | ✅ Redis（但**只在 Redis 里**，没有暴露成指标） |
| 配额拒绝 | ✅ 429 带 `QUOTA_EXCEEDED`（同样只在 Redis / 响应里） |
| **任务真实用量** | ✅ `task.used_tokens`（本轮刚落地，是成本指标的天然数据源） |

### 0.2 缺的（就是本计划要做的）

1. **没有 `/metrics` 端点**：只在限流中间件的 skip 列表里"预留"了这个名字；
2. **没有指标**：任务成功率、节点耗时、token 成本、源错误率、限流/配额命中数全部不可聚合；
3. **没有看板**：用户只能自己翻任务列表，看不出"我这周烧了多少额度"。

### 0.3 明确不做（写进文档，避免范围蔓延）

- **不引入 APM**（Jaeger / SkyWalking / OpenTelemetry collector）。理由：当前是单进程 +
  五容器，结构化日志 + Prometheus 指标足够定位问题；引入分布式追踪需要改造所有
  LLM/HTTP 调用点，成本远高于收益。
- **不做多租户指标隔离**：`/metrics` 是**运维端点**（默认不含 `user_id` 标签，
  避免高基数与信息泄漏）。用户侧的"我的用量"走单独的看板端点（按 `user_id` 过滤）。
- **不做告警**（Alertmanager）。理由：告警渠道（邮件/webhook）未定，且本项目 SMTP 尚未
  配置齐全；先让指标可用，告警等运营需求明确后单独立项。

---

## 1. 指标设计（先定口径，再写代码）

### 1.1 HTTP 层

| 指标 | 类型 | 标签 | 说明 |
| --- | --- | --- | --- |
| `maa_http_requests_total` | Counter | `method` / `route` / `status` | 请求总数。`route` 用**路由模板**（`/api/v1/tasks/{task_id}`）而非真实路径 —— 否则 path 参数会把基数炸掉 |
| `maa_http_request_duration_seconds` | Histogram | `method` / `route` | 延迟分布（桶：50ms/100ms/250ms/500ms/1s/2.5s/5s/10s/30s） |

> ⚠️ **基数控制是硬约束**：绝不用 `request.url.path`（含 UUID）。用
> `request.scope["route"].path`；未匹配到路由时用 `"__unmatched__"`。

### 1.2 任务层

| 指标 | 类型 | 标签 | 说明 |
| --- | --- | --- | --- |
| `maa_tasks_total` | Counter | `status` | 任务进入终态（completed/failed/cancelled）时 +1 |
| `maa_task_tokens_total` | Counter | 无 | 累计消耗 token（结算时按 `used_tokens` 累加） |
| `maa_task_duration_seconds` | Histogram | `status` | 任务从创建到终态的耗时 |
| `maa_task_nodes_total` | Counter | `node_type` / `status` | 节点执行结果（成功/失败/转人工），用于定位"卡在哪个环节" |

### 1.3 外部依赖与限流

| 指标 | 类型 | 标签 | 说明 |
| --- | --- | --- | --- |
| `maa_literature_source_errors_total` | Counter | `source` | 检索源失败次数（arXiv/OpenAlex/S2/PubMed） |
| `maa_rate_limit_rejected_total` | Counter | `route` | 限流 429 次数 |
| `maa_quota_rejected_total` | Counter | 无 | 配额 429 次数 |

### 1.4 采集方式（关键设计决策）

**指标必须在业务路径上"零成本失败"**：

- 所有 `inc()` / `observe()` 包在 `try/except` 里，**指标故障绝不影响业务**
  （与限流/配额的 fail-open 同一原则）；
- 用**模块级单例 registry**，不在每个请求里构造；
- `prometheus-client` 是纯 Python、无额外系统依赖，镜像体积增加约 200KB。

**任务指标从哪来**：

- 不需要轮询数据库。`_finish_reviewing`（完成）、`_mark_waiting_human`（失败）、
  `_control_cancel`（取消）三个点已经知道终态 → 直接在那里打点；
- token 成本在 `settle_quota` 里已经算出 `refunded = ESTIMATED - used` → 等价于
  知道 `used`，直接打点（**不要**再查一次库）。

---

## 2. 文件结构

| 文件 | 职责 |
| --- | --- |
| `backend/app/core/metrics.py` | 指标定义 + 安全打点封装（`inc_counter` / `observe_histogram` / `render()`） |
| `backend/app/api/metrics_middleware.py` | HTTP 指标中间件（路由模板标签、延迟直方图） |
| `backend/app/api/v1/endpoints/health.py` | 新增 `GET /metrics`（Prometheus 文本格式） |
| `backend/app/api/v1/endpoints/observability.py` | 新增 `GET /observability/overview`（**用户维度**用量/成功率，需鉴权） |
| `backend/app/orchestration/services/task_execution.py` | 三个终态点打点 |
| `backend/app/tools/literature/source_registry.py` | 单源失败打点 |
| `backend/app/api/rate_limit_middleware.py` | 429 打点 |
| `frontend/src/views/ObservabilityView.vue` + `.test.ts` | 成本/健康看板页 |
| `frontend/src/api/observability.ts` + `.test.ts` | 前端 API 层 |
| `backend/tests/test_metrics.py` | 指标定义与安全打点 |
| `backend/tests/test_metrics_middleware.py` | 中间件（含**基数控制**断言） |
| `backend/tests/test_observability_api.py` | 两个端点 |

**Lead 独占**：`backend/pyproject.toml`（加依赖）、`app/main.py`（挂中间件）、
`app/api/v1/router.py`（挂路由）、`frontend/src/router/index.ts`。

---

## Task 1: 指标核心与安全打点

**Files:**
- Create: `backend/app/core/metrics.py`
- Modify: `backend/pyproject.toml`（加 `prometheus-client`）
- Test: `backend/tests/test_metrics.py`

### Step 1: 写失败测试

```python
"""指标核心测试：定义正确 + 打点绝不抛异常 + 渲染是 Prometheus 文本格式。"""

from __future__ import annotations

import pytest

from app.core import metrics


def test_counters_and_histograms_are_defined() -> None:
    """计划 §1 的指标必须全部存在（名字/类型即契约）。"""
    names = {metric.name for metric in metrics.REGISTRY.collect()}
    # 计数器在 collect() 里带 _total 后缀
    expected = {
        "maa_http_requests",
        "maa_tasks",
        "maa_task_tokens",
        "maa_task_nodes",
        "maa_literature_source_errors",
        "maa_rate_limit_rejected",
        "maa_quota_rejected",
    }
    missing = expected - names
    assert not missing, f"缺少指标：{sorted(missing)}"

    histograms = {metric.name for metric in metrics.REGISTRY.collect()
                  if metric.type == "histogram"}
    assert {"maa_http_request_duration_seconds", "maa_task_duration_seconds"} <= histograms


def test_render_returns_prometheus_text() -> None:
    metrics.inc_counter("maa_quota_rejected")
    text = metrics.render()
    assert "maa_quota_rejected_total 1.0" in text
    assert "# TYPE maa_quota_rejected counter" in text


def test_inc_counter_swallows_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """指标故障绝不影响业务（与限流/配额的 fail-open 同一原则）。"""
    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("registry 炸了")

    monkeypatch.setattr(metrics, "_inc_raw", boom)
    metrics.inc_counter("maa_quota_rejected")  # 不应抛异常


def test_unknown_metric_name_is_noop() -> None:
    """拼错指标名不能炸（也不能创建垃圾指标）。"""
    before = metrics.render()
    metrics.inc_counter("maa_does_not_exist")
    assert metrics.render() == before
```

### Step 2: 跑测试确认失败

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_metrics.py -q --no-cov
```

Expected: `ModuleNotFoundError: No module named 'app.core.metrics'`

### Step 3: 写实现

要点（不贴完整代码，实现时按此写）：

1. `pyproject.toml` 加 `"prometheus-client>=0.20"` 到 `dependencies`；
2. `metrics.py` 定义 `REGISTRY = CollectorRegistry()`（**独立 registry，不污染默认全局**——
   否则测试之间状态串扰，且默认 registry 会重复注册报错）；
3. 每个指标一个模块级变量，名字用常量表映射（`inc_counter("maa_quota_rejected")` 查表）；
4. `inc_counter` / `observe_histogram` / `set_gauge` 内部 `try/except Exception: logger.debug(...)`；
5. `render()` 返回 `generate_latest(REGISTRY).decode()`；
6. **加 `reset_for_tests()`**：清空计数（测试隔离用，不做生产入口）。

### Step 4: 跑测试确认通过

### Step 5: 提交

```bash
git commit -m "feat(observability): add metrics registry with fail-open recording"
```

---

## Task 2: HTTP 指标中间件（含基数控制）

**Files:**
- Create: `backend/app/api/metrics_middleware.py`
- Modify: `backend/app/main.py`（挂中间件）
- Test: `backend/tests/test_metrics_middleware.py`

### Step 1: 写失败测试（核心是**基数**断言）

```python
"""HTTP 指标中间件：路由模板标签 + 未匹配兜底 + 不记录真实路径。"""

# 关键断言（4 条）：
# 1. 请求 /api/v1/tasks/<uuid> 后，标签里出现的是 `/api/v1/tasks/{task_id}`，**不含那个 uuid**
# 2. 404 请求（无匹配路由）标签是 `__unmatched__`
# 3. /metrics 自身不被记录（否则抓取请求淹没指标）
# 4. 中间件抛异常也不影响响应返回（fail-open）
```

### Step 2-5: 同 Task 1 节奏

**实现要点**：

- 用 `BaseHTTPMiddleware`，`try/finally` 保证异常路径也记录；
- 路由模板取 `request.scope.get("route")`，其 `.path` 即模板；`None` 时用 `__unmatched__`；
- **`/metrics` 与 `/api/v1/health` 不记录**（探针/抓取会污染数据）；
- 注册顺序：在 `RequestIDMiddleware` **之后**（洋葱模型里更内层），确保能拿到 request_id。

---

## Task 3: 任务/节点/成本指标接入

**Files:**
- Modify: `backend/app/orchestration/services/task_execution.py`
- Test: `backend/tests/test_task_metrics.py`

### Step 1: 写失败测试

```python
# 断言：
# 1. 任务完成 → maa_tasks_total{status="completed"} == 1，且 maa_task_tokens_total 增加 used_tokens
# 2. 调度失败转人工 → maa_tasks_total{status="waiting_human"} == 1（**注意**：不是 failed）
# 3. 取消 → maa_tasks_total{status="cancelled"} == 1
# 4. 节点结果 → maa_task_nodes_total{node_type=..., status=...} 有值
# 5. **指标故障时任务照常完成**（fail-open）
```

### Step 2-5

**实现要点**：在 `_finish_reviewing`（completed）、`_finish_waiting_human` +
`_mark_waiting_human`（waiting_human）、`_control_cancel`（cancelled）三处打点；
token 在 `settle_quota` 里已知 `used`，直接打点。

> ⚠️ 三个终态点都可能在**并发/重试**下重复进入。任务状态转换有 `lock_for_update` +
> 状态断言兜底，所以打点放在**状态转换成功之后**，不会重复计数。

---

## Task 4: 检索源失败 + 限流/配额拒绝打点

**Files:**
- Modify: `backend/app/tools/literature/source_registry.py`、`backend/app/api/rate_limit_middleware.py`
- Test: `backend/tests/test_source_and_limit_metrics.py`

**要点**：`source_registry` 里单源失败已有 `try/except` 隔离 → 在那里 `inc`；
限流中间件返回 429 的分支 → `inc`；配额 429 在 `handle_app_exception` 里按
`error_code == "QUOTA_EXCEEDED"` 区分（**不要**把两个计数混在一起）。

---

## Task 5: `/metrics` 端点 + 用户维度看板 API

**Files:**
- Modify: `backend/app/api/v1/endpoints/health.py`（加 `/metrics`）
- Create: `backend/app/api/v1/endpoints/observability.py`
- Modify: `backend/app/api/v1/router.py`
- Test: `backend/tests/test_observability_api.py`

### 端点设计

| 端点 | 鉴权 | 返回 |
| --- | --- | --- |
| `GET /metrics` | **无**（运维抓取） | Prometheus 文本 |
| `GET /api/v1/observability/overview` | **需要**（Bearer） | 当前用户的任务统计 |

`/observability/overview` 响应契约：

```json
{
  "tasks": {"total": 12, "completed": 9, "failed": 1, "cancelled": 1, "waiting_human": 1},
  "success_rate": 0.75,
  "tokens": {"total_used": 342000, "avg_per_task": 28500},
  "recent": [{"task_id": "...", "status": "completed", "used_tokens": 31000, "duration_seconds": 92}]
}
```

> **必须 owner-scoped**：只能看自己的数据（与既有 `user_id` 隔离口径一致；
> 越权返回空集而不是 403 —— 不泄漏"别人有多少任务"）。

### 安全约束（硬要求）

`/metrics` **无鉴权**是刻意选择（Prometheus 抓取需要），因此：

1. **绝不含 `user_id` / 任务标题 / 任何 PII 标签**；
2. 只暴露聚合计数与直方图；
3. 在 `docs/runbooks/` 记录「生产部署应只在内网暴露 `/metrics`」。

---

## Task 6: 前端可观测性看板

**Files:**
- Create: `frontend/src/api/observability.ts` + `.test.ts`
- Create: `frontend/src/views/ObservabilityView.vue` + `.test.ts`
- Modify: `frontend/src/router/index.ts`、AppLayout 导航

### 页面内容（按 AGENTS §6.5 规范）

| 区块 | 内容 |
| --- | --- |
| 概览卡片 | 任务总数 / 成功率 / 累计 token / 平均每任务 token（`Card` + `text-2xl`） |
| 状态分布 | 5 个 `Badge`（completed/failed/cancelled/waiting_human 等） |
| 最近任务表 | 任务 ID（截断）/ 状态 / token / 耗时 / 时间（窄屏转卡片式） |
| 空态 | `EmptyState`：「还没有任务数据。创建一个任务后这里会显示用量统计」 |

**必须遵守**：
- 只用 `Card` / `Badge` / `Table` 风格的既有组件，不写 scoped CSS；
- 定位用 `data-test`（`observability-total` / `observability-success-rate` /
  `observability-token-total` / `observability-empty` / `observability-row-<id>`）；
- 移动端 `max-md:` 转卡片式，且无横向溢出（复用既有 E2E 的溢出断言思路）。

---

## Task 7: 端到端验收

- [ ] 本地全量门禁
- [ ] 服务器部署 + `curl /metrics` 看到真实数据（建一个任务后 counter 增长）
- [ ] 浏览器走查看板（桌面 + 移动双视口）
- [ ] `docs/runbooks/` 补「指标与看板」小节（含 `/metrics` 只在内网暴露的警告）
- [ ] CI 6/6

---

## 验收清单

- [ ] `/metrics` 返回 Prometheus 文本格式，含计划 §1 全部指标
- [ ] **标签无高基数**（路由用模板、无 UUID、无 user_id）
- [ ] 任务完成/失败/取消/转人工都能在指标里反映
- [ ] token 成本可从指标聚合（与 `task.used_tokens` 一致）
- [ ] 检索源失败、限流 429、配额 429 分别可观测
- [ ] 用户看板只显示自己的数据
- [ ] 全量门禁：black/ruff/mypy/pytest（≥80% cov）+ 前端 Vitest + `vue-tsc` + build
- [ ] CI 6 作业全绿
