# review 节点长度上限缺陷：作者级规则被施加到机器汇总

> 日期：2026-10-27 ｜ 状态：**已实施**
> 前序：[2026-10-26-01-reference-library-followup-plan.md](2026-10-26-01-reference-library-followup-plan.md)

---

## 1. 缺陷

真实任务 `bdeaa228`（引用库证据闭环验证）的 `n7 review` 节点：

```
validation.passed = False  score = 96.0
✗ rules: 长度超限：10230 > 10000
```

任务从 `running` 直接转 `waiting_human`，`retry_count = 2`（重试两次仍失败）。

### 1.1 为什么这不是「用户写太长」

| 证据 | 说明 |
| --- | --- |
| 超限节点是 `n7 review`，**不是** `n5 write` | `write` 只有 4081 字符 |
| `review` 的 content = 上游所有节点输出拼接 | `_summarize_dependencies()` 机械汇总 |
| 上游节点数固定为 7 | 汇总长度随上游内容线性增长 |
| 已完成任务成稿长度最高 **18413** 字符（5 个任务 > 10000） | 说明 10000 不是成稿上限，只卡中间节点 |
| `retry_count = 2` 后仍失败 | **重试只会重新汇总同样内容** → 无法修复 |

### 1.1.1 影响面：只有 1 次确证，不夸大

**必须如实说明取证边界**：

| 查证项 | 结果 |
| --- | --- |
| 直接证据 | ✅ 真实任务 `bdeaa228` 的 API 快照：`n7 review` 10230 > 10000，`retry_count=2` |
| 库里 `subtask.output_data` 含「长度超限」 | **0 条** |
| 库里 `validation_result` 总数 | **0 条**（校验结论从未落库，所以查不到） |
| 近 7 天后端日志含「长度超限」 | **0 条**（校验结论不进日志） |
| 当前 `waiting_human` 任务 | 15 个，但 `output_data` 里 `node_type` 均为空，无法归因 |

**结论**：缺陷**真实存在且可复现**（有完整 API 快照），但**历史发生频次无法量化** ——
校验结论既不落库也不进日志，事后无法统计。

> 这条边界要写清楚：**「有 1 次确证」不等于「只发生 1 次」，也不等于「经常发生」。**
> 顺带暴露另一个问题：`validation_result` 表是空的 —— 校验结论从未持久化，
> 排查校验类缺陷时**没有任何历史数据可用**。这值得单列任务（见 §7）。

### 1.2 根因：`content_role` 只修了一半

Issue #6 引入 `content_role`（`authored`/`aggregated`）解决「机器汇总被施加作者级检查」，
但只修了 `citation_validator` 的 `duplicate_paragraph_ratio`：

```python
# validator.py validate()
content_role = self._content_role(input_data)      # ← 读了

rule_report = self._check_rules(result)            # ← 没传！max_length 在这里
citation_report = self._run_citation_checks(input_data)  # ← 内部传了 content_role
```

`_check_rules` 的签名根本没有 `content_role` 参数 —— **规则校验完全不知道内容角色**。

**这是同一类错误的第二次出现**：Issue #6 修了 A 处，B 处（`max_length`）留着。

---

## 2. 修法

### 2.1 `max_length` 只对作者正文生效

```python
def check_rules(self, result, *, content_role="authored") -> dict:
    if length < min_len:                                   # 仍对所有人生效
        issues.append(...)
    if content_role != "aggregated" and length > max_len:  # ← 只有作者正文
        issues.append(...)
    for kw in required_keywords:                           # 仍对所有人生效
        ...
```

### 2.2 为什么不是「调大 max_length」

调大只是把阈值推到更远处 —— 上游再多两个节点照样超限。
根因是**对错误的对象施加了这条规则**：

> `max_length` 是「别写太长」的**写作约束**。
> 机器汇总该有多长就有多长，不归它管。

### 2.3 为什么 `min_length` 与 `required_keywords` 仍生效

| 规则 | 对汇总是否适用 | 理由 |
| --- | --- | --- |
| `min_length` | ✅ 适用 | 空汇总/汇总缺内容确实是问题，与谁写的无关 |
| `required_keywords` | ✅ 适用 | 缺必需章节是内容缺陷，与谁写的无关 |
| `max_length` | ❌ 不适用 | 「别写太长」是写作约束；汇总长度由上游决定 |

### 2.4 保留 `_check_rules` 作为内部别名

既有调用点（测试、其他 Agent）用 `_check_rules`，保留它转发到公开的
`check_rules`，避免无谓的破坏性改动。

---

## 3. 验收标准

| # | 标准 | 验证 |
| --- | --- | --- |
| A1 | `aggregated` 内容不受 `max_length` 约束 | `test_aggregated_content_not_limited` |
| A2 | `authored` 内容仍受约束（不回归） | `test_authored_content_still_limited` |
| A3 | `min_length` 对 `aggregated` 仍生效 | `test_aggregated_still_checks_min_length` |
| A4 | `required_keywords` 对 `aggregated` 仍生效 | `test_aggregated_still_checks_keywords` |
| A5 | 不传 `content_role` 时按 `authored` 从严 | `test_default_role_is_authored` |
| A6 | 非法角色值按 `authored` 从严 | `test_unknown_role_falls_back_to_authored` |
| A7 | **工作流 → 校验器**真的把角色传下去 | `test_validator_receives_aggregated_for_review` |
| A8 | 真实长度（10230 字符）的 review 汇总不再失败 | `test_review_node_regression_real_length` |

**A7 是关键**：单测 `check_rules(role="aggregated")` 全绿但工作流没传角色的话，
真实任务照样失败 —— 这是 AGENTS §5 #33 的「假绿」防线。

---

## 4. 风险

| 风险 | 缓解 |
| --- | --- |
| 汇总内容失控增长（不设上限） | 上游节点数由计划决定（7 个左右），且 `write` 本身受 `max_length` 约束；汇总长度天然有界 |
| `_node_type()` 返回空串导致角色推断失败 → 退化为 `authored` → 缺陷复发 | 测试 `test_empty_node_type_falls_back_to_authored` 锁住当前行为并标注为**已知隐患**；`_node_type` 已有 `prepared` → `plan` 双路径兜底 |
| 有人以为「取消上限 = 不检查质量」 | docstring 明确写清 `min_length`/`required_keywords` 仍生效 |

---

## 5. 执行顺序

1. 取证固化（已做）：真实任务 `n7 review` 10230 字符、`retry_count=2`、成稿最长 18413
2. 失败测试（17 条，红）
3. 实现 `check_rules` 带 `content_role` + `validate` 传参
4. 绿 + 无回归（`-k "validator or validate"` 67 条）
5. 诚实性验证：回退 → **5 条变红**
6. 门禁（逐个跑）→ 提交 → 部署
7. 真实任务验证 → 文档 → push → **查 CI**

---

## 6. 实施结果

### 6.1 交付

| 文件 | 改动 |
| --- | --- |
| `app/orchestration/agents/validator.py` | 新增公开 `check_rules(result, *, content_role)`；`_check_rules` 转别名；`validate()` 把 `content_role` 传给规则校验 |
| `tests/test_validator_content_role.py` | **新增 17 条**测试 |

### 6.2 门禁

```
black：1 file reformatted
ruff：All checks passed
mypy --no-incremental：0 错误（206 文件）
pytest 全量：见 §6.3
```

### 6.3 诚实性验证

回退 `content_role != "aggregated" and` 这一处判断 → **5 条变红**：

```
FAILED test_aggregated_content_not_limited
FAILED test_validate_aggregated_over_limit_passes_rule_check
FAILED test_review_node_regression_real_length
（+ 2 条）
```

其中 `test_review_node_regression_real_length` 是端到端复现真实场景
（10230 字符 review 汇总），它变红证明测试确实覆盖了真实链路。

---

## 7. 顺带发现：校验结论从未落库

查影响面时发现 `validation_result` 表**一条记录都没有**：

```sql
SELECT count(*) FROM validation_result;  -- 0
```

但任务确实在跑校验（`validator_done` 日志有记录，`waiting_human` 任务有 `validation` 字段）。
说明校验结论只存在 `subtask.output_data` 的 JSON 里，**没有写进专门的表**。

后果：

| 场景 | 影响 |
| --- | --- |
| 排查「为什么转人工」 | 只能逐个任务拉 API 快照，无法按失败原因聚合 |
| 统计校验通过率 | 无法做（`passed` 列形同虚设） |
| 本次缺陷的影响面 | **无法量化**（见 §1.1.1） |

**不列入本轮范围**：本轮目标是修 `max_length` 缺陷本身。
但这是排查校验类缺陷的基础设施缺口，建议单列任务。

---

## 8. 验证限制

- 模型不支持图片输入，**未肉眼看过截图**；
- 影响面**无法量化**（见 §1.1.1），不声称「修了 N 个任务的失败」；
- `_node_type()` 返回空串时角色退化为 `authored` 的隐患**未修复**，只加了测试锁住当前行为。

---

## 9. 真实环境验证（两段，缺一不可）

### 9.1 真实任务：`completed`（但未复现触发条件）

```
① 引用库: created=3
② 任务 edd89520 → 批准
⑤ 最终状态 = completed
⑥ review 节点: n7 status=completed content_len=7497 rules_passed=True
⑦ 内容超过 10000 的 review 节点: []
⚠️ 本轮 review 内容未超过 10000，**未复现触发条件**
```

**任务完成了**（上一轮同样配置卡在 `waiting_human`），但 review 只有 7497 字符，
**没有触发超限**。所以这一段**不能证明修复在超限场景生效** ——
只能说「没有回归」。

> 这一点必须如实说：如果只拿这一段就宣布修好了，那是把「没触发」当成「已修好」。

### 9.2 容器内直接构造超限场景（绕过 LLM，长度可控）

review 内容长度由 LLM 决定，不可控。所以直接构造**与真实缺陷完全同长度**
（10230 字符）的载荷，送进**生产同一份代码**的 `ValidatorAgent`：

| 场景 | `rule_check.passed` | issues |
| --- | --- | --- |
| `aggregated`（review，10230 字符） | **True** | `[]` ← **修复生效** |
| `authored`（write，10230 字符） | **False** | `长度超限：10230 > 10000` ← **仍被拦** |
| 不传角色 | **False** | 按 `authored` 从严 |

**三行对照才是完整证据**：
- 第 1 行证明修复生效；
- 第 2 行证明**没有把规则整体关掉**（只对汇总放宽）；
- 第 3 行证明兜底仍是「从严」。

### 9.3 清理（回查确认）

```
probe_tasks = 0        （清理前 2）
task_total = 53        （回到基线）
demo_tasks = 27        （回到基线）
reference_library_item = 0
quota key 已删除，回查 KEYS 为空
```
