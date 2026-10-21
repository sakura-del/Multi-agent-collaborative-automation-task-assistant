# 可观测性：指标与看板

> Phase 2 第 3 项交付。适用对象：运维（Prometheus 抓取、排障）与开发（看指标设计口径）。
> 计划：[2026-10-21-01-observability-plan.md](../superpowers/plans/2026-10-21-01-observability-plan.md)

---

## 1. 两个端点，两套安全语义

| 端点 | 鉴权 | 用途 |
| --- | --- | --- |
| `GET /metrics` | **无** | Prometheus 抓取（运维视角：全实例聚合） |
| `GET /api/v1/observability/overview` | **Bearer** | 用户看板（只看自己的任务与成本） |

### ⚠️ `/metrics` 必须只在内网暴露

`/metrics` **刻意不加鉴权**——Prometheus 抓取器不带业务 token，加鉴权要在抓取配置里
额外塞 header，运维摩擦大且容易忘。代价是**必须靠网络层隔离**：

- 生产部署：把 `/metrics` 限制在受信内网（VPC/安全组），或在外层网关加 IP 白名单；
- 公网入口：**不要**把 `/metrics` 直接暴露出去。

配套的代码侧约束（已用测试钉死，见 `tests/test_observability_api.py::test_metrics_contains_no_pii`）：

- 指标标签**绝不允许** `user_id` / 任务标题 / 真实请求路径；
- 只允许低基数枚举：状态、节点类型、源类名、路由模板。

---

## 2. 指标清单与口径

### HTTP 层（中间件自动采集）

| 指标 | 标签 | 口径 |
| --- | --- | --- |
| `maa_http_requests_total` | `method` / `route` / `status` | 请求总数。`route` 是**路由模板**（`/api/v1/tasks/{task_id}`） |
| `maa_http_request_duration_seconds` | `method` / `route` | 延迟直方图（50ms ~ 30s 共 9 桶） |

**`/metrics`、`/api/v1/health*`、`/api/v1/ws` 不记录**（探针/抓取是高频调用，会淹没业务数据）。

### 任务层（业务路径打点，零轮询）

| 指标 | 标签 | 口径 |
| --- | --- | --- |
| `maa_tasks_total` | `status` | 进入终态的任务数：`completed` / `waiting_human` / `cancelled` |
| `maa_task_tokens_total` | 无 | 累计消耗 token（与 `task.used_tokens` 同口径） |
| `maa_task_duration_seconds` | `status` | 从创建到终态的耗时 |
| `maa_task_nodes_total` | `node_type` / `status` | DAG 节点执行结果（定位「卡在哪个环节」） |

> **`waiting_human` 不是 `failed`**：前者是「等人工处理」，后者是「系统 bug」。
> 两者在值班优先级上完全不同，指标里必须分开。

### 依赖与准入控制

| 指标 | 标签 | 口径 |
| --- | --- | --- |
| `maa_literature_source_errors_total` | `source`（类名） | 检索源失败次数（单源故障已隔离，这里只做可观测） |
| `maa_rate_limit_rejected_total` | `route`（**限流规则名**） | 被限流拒绝（429）的请求数 |
| `maa_quota_rejected_total` | 无 | 因配额耗尽被拒绝（429）的请求数 |

> 两个 429 必须分开：**限流 = 客户端在刷**（查是谁、要不要提额）；
> **配额 = 用户额度用尽**（转化付费 / 调整策略）。混在一起就分不清该做什么。

---

## 3. 常用查询

```promql
# 任务成功率（近 1 小时）
sum(rate(maa_tasks_total{status="completed"}[1h]))
  / sum(rate(maa_tasks_total[1h]))

# 每天烧多少 token
sum(increase(maa_task_tokens_total[24h]))

# 哪个检索源在拖后腿
sum by (source) (increase(maa_literature_source_errors_total[1h]))

# 哪些端点最慢（P95）
histogram_quantile(0.95, sum by (le, route) (rate(maa_http_request_duration_seconds_bucket[5m])))

# 谁在刷（限流命中 Top）
topk(5, sum by (route) (increase(maa_rate_limit_rejected_total[1h])))

# 配额拒绝趋势（判断该不该调额度）
sum(rate(maa_quota_rejected_total[1h]))
```

---

## 4. 排障流程

**「任务老是失败 / 卡住」**

1. `sum by (status) (increase(maa_tasks_total[1h]))` → 看是 `failed` 还是 `waiting_human`；
2. 若是 `waiting_human`，看 `sum by (node_type) (increase(maa_task_nodes_total{status!="completed"}[1h]))`
   → 定位卡在哪类节点（`search` 卡 = 检索源问题；`write` 卡 = 模型问题）；
3. 若 `search` 卡得多，再看 `maa_literature_source_errors_total` 按 `source` 分组。

**「用户说额度不够」**

1. `maa_quota_rejected_total` 是否在涨 → 确认是配额而非限流；
2. 用用户的看板（`/observability`）或 `task.used_tokens` 查实际消耗；
3. 注意：**规划阶段用量不统计**（规划 API 不返回 token 用量），
   所以「规划后放弃」的任务按 0 用量计——这是已知局限，不是 bug。

**「某个端点很慢」**

1. `histogram_quantile(0.95, ...)` 按 `route` 分组；
2. 注意 `route` 是模板，不会因参数不同而分散。

---

## 5. 已知局限（不要误判为 bug）

| 局限 | 原因 | 影响 |
| --- | --- | --- |
| 规划阶段 token 不统计 | 规划 API 不返回 usage | 「规划后放弃」按 0 用量计（宁可少扣用户） |
| 指标重启归零 | 进程内 registry（非持久化） | Prometheus 侧用 `rate`/`increase` 不受影响；绝对值会跳变 |
| 无告警 | 未接入 Alertmanager（告警渠道未定） | 需要人工看板；后续单独立项 |
| 无分布式追踪 | 刻意不引入 APM | 单请求排障靠结构化日志的 `trace_id`/`task_id` |

---

## 6. 抓取配置示例

```yaml
# prometheus.yml
scrape_configs:
  - job_name: maa-backend
    scrape_interval: 15s
    static_configs:
      # 生产环境请用内网地址；不要暴露到公网
      - targets: ["10.0.10.2:48080"]
```

---

## 7. 手工验证

```bash
# 1) 端点可达且是 Prometheus 文本
curl -s http://10.0.10.2:48080/metrics | head -20

# 2) 触发一次请求后看 HTTP 指标
curl -s -o /dev/null http://10.0.10.2:48080/api/v1/health
curl -s http://10.0.10.2:48080/metrics | grep maa_http_requests_total

# 3) 确认没有 PII（应为空输出）
curl -s http://10.0.10.2:48080/metrics | grep -E 'user_id|task_title'
```

前端看板：登录后左侧导航「用量」（`/observability`）。
