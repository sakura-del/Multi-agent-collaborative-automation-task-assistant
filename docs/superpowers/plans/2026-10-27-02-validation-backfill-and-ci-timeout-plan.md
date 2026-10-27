# 历史校验结论迁移 + CI 作业超时

> 日期：2026-10-27 ｜ 状态：**已实施**
> 前序：[2026-10-27-01-validation-persistence-plan.md](2026-10-27-01-validation-persistence-plan.md)

---

## 1. CI 作业超时（第一部分）

### 1.1 缺陷

`.github/workflows/ci.yml` **没有配置任何 `timeout-minutes`** → 每个作业都按
GitHub 默认的 **6 小时**。

2026-10-27 实测踩到：一次**纯文档**提交（2 个 `.md`）里，`test` 作业的
`Run pytest with coverage gate` 步骤卡了 **42 分钟**（正常 5m35s）。手动取消后
重跑，同一作业 **5m35s** 正常完成 —— runner 侧卡住，不是代码问题。

### 1.2 为什么必须修（不只是"等久一点"）

| 后果 | 说明 |
| --- | --- |
| 白占 6 小时 | 卡死的 run 会一直挂着 |
| **更糟：后续 push 会取消它** | `concurrency.cancel-in-progress: true` → 卡住的 run 被新 push 取消，**看起来一直在跑、实际什么都没验** |
| 无法区分「慢」与「卡死」 | 没有超时就永远等不到失败信号 |

### 1.3 修法

按实测耗时 × 3~5 倍余量，给 6 个作业都加 `timeout-minutes`：

| 作业 | 实测 | 超时 |
| --- | --- | --- |
| `lint` | 1m22s | **15** |
| `test` | 5m35s | **30** |
| `migration` | 1m41s | **20** |
| `frontend` | 1m24s | **20** |
| `build-check` | 28s（缓存命中）~ 4m47s（冷缓存） | **30** |
| `smoke` | 3m31s | **30** |

文件头加了说明，解释**为什么每个作业都必须有超时**（避免后来者删掉）。

---

## 2. 历史校验结论迁移（第二部分）

### 2.1 缺陷

`validation_result` 表直到 `90dfd6a` 才接上写入方。在此之前校验结论只存在
`subtask.output_data` 的 JSON 里 —— 不可查询、不可聚合。

### 2.2 取证（**修正上一轮的错误数字**）

上一轮我在文档里写「193 个节点有校验结论」。本轮实测**精确数字是 188**：

```sql
SELECT count(*) FROM subtask
WHERE output_data::jsonb->'validation' IS NOT NULL
  AND output_data::jsonb->'validation' <> 'null'::jsonb;
-- 188
```

之前那个 193 是**含 `null` 的计数**（把 `validation: null` 也算进去了）。
**这是我在文档里写错的数字，本轮纠正。**

### 2.3 完整形状取证（AGENTS §5 #33：先打印真实载荷）

| 查证项 | 结果 |
| --- | --- |
| 带校验载荷的 subtask | **188** |
| `validation_result` 表行数 | **0** |
| 每 subtask 最多一条校验 | 188 rows = 188 distinct（**无重复**） |
| `passed` 类型 | `boolean` |
| `score` 范围 | **80.0 ~ 100.0**（契约 0–100，需归一化） |
| 字段分布 | `score`/`passed` **188**；`checks`/`feedback`/`retryable` **172**；`semantic_check`/`rule_check`/`issues`/`suggestions` **41** |

**关键发现：188 − 172 = 16 条只有 `score`/`passed`，没有 `checks`。**
迁移脚本必须容忍这种载荷（否则 16 条会崩）。

### 2.4 修法：脚本，不是 Alembic 迁移

与 `backfill_used_tokens.py` 同一理由：

1. **迁移不该做业务计算** —— 在 schema 版本链里解析 JSON、归一化分数，日后看不懂；
2. **可重跑** —— `--dry-run` 先看规模，实际执行幂等；
3. **可回查** —— `--verify`（AGENTS §5 #11）。

### 2.5 四个语义约束

| 约束 | 理由 |
| --- | --- |
| **只补表里还没有的 subtask** | 幂等，重跑不产生重复行 |
| **`score` 除以 100** | 历史 80~100，列语义 0–1。**复用 `normalize_score()`**，不另写一份（否则两处规则漂移） |
| **不改 `output_data`** | 快照/复盘 Agent 还在读它 |
| **缺字段从严** | `passed` 非 true 即 False、`score` 缺失即 0.0、`checks` 缺失即空列表 |

---

## 3. 验收标准

| # | 标准 | 验证 |
| --- | --- | --- |
| A1 | `score` 从 0–100 归一化到 0–1 | `test_score_is_normalized_from_contract_range` |
| A2 | 越界分数夹紧 | `test_score_out_of_range_is_clamped` |
| A3 | 只有 `passed is True` 才算通过 | `test_passed_only_true_counts_as_pass` |
| A4 | 缺 `passed` 从严（False） | `test_missing_passed_defaults_to_failed` |
| A5 | `checks` → `rule_results` | `test_checks_become_rule_results` |
| A6 | **缺 `checks` 不崩**（真实 16 条） | `test_missing_checks_yields_empty_list` |
| A7 | `feedback` → `report` | `test_feedback_becomes_report` |
| A8 | `semantic_check` → `llm_report` | `test_semantic_check_becomes_llm_report` |
| A9 | 空载荷不崩 | `test_empty_payload_does_not_crash` |
| A10 | 找到带校验的 subtask | `test_finds_subtasks_with_validation` |
| A11 | 跳过没有校验的 | `test_skips_subtasks_without_validation` |
| A12 | **跳过已迁移的**（幂等） | `test_skips_already_migrated` |
| A13 | 写入行 | `test_writes_rows` |
| A14 | **连跑两次只写一次** | `test_is_idempotent` |
| A15 | **失败的校验也要迁移** | `test_migrates_failed_validations` |
| A16 | **不改 `output_data`** | `test_does_not_touch_output_data` |

---

## 4. 风险

| 风险 | 缓解 |
| --- | --- |
| 迁移写错分数（80 存成 80.0） | 复用 `normalize_score()` + 专项测试 + 夹紧 |
| 重跑产生重复行 | `collect_candidates` 用 `not_in(migrated)` 排除；`test_is_idempotent` 锁住 |
| 188 条里有畸形载荷 | 缺字段全部从严兜底 + `test_empty_payload_does_not_crash` |
| 迁移期间用户在用 | 只 INSERT，不改既有数据；单事务提交 |

---

## 5. 执行顺序

1. CI 超时（已做）
2. 取证历史载荷形状（已做，修正 193 → 188）
3. 失败测试（17 条，红）
4. 实现脚本
5. 绿 + 诚实性验证（回退归一化 → 3 条变红）
6. 门禁 → 提交 → 部署
7. **服务器真实迁移**（188 条）+ 回查
8. 文档 → push → 查 CI

---

## 6. 实施结果

（见文末）
