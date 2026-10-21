# 配额结算修复：未完成任务不再白扣预扣额度

> **背景**：2026-10-21 Task 7 验收实测暴露——demo 账号 6 个停在 `waiting_approval`
> 的探针任务占满 100 万日配额，所有建任务请求 429。根因：`quota_refund_for_usage`
> **只在 `task_completed` 调用**，取消 / 失败 / 转人工 / 放弃确认的任务，提交时预扣的
> `100k` 永久占用，用户点错一次就白扣额度。

**Goal:** 让任务无论以何种方式结束（完成 / 取消 / 失败 / 转人工 / 放弃执行），
用户都只为**真实消耗**的 token 付费；预扣与结算之间可审计、可重放、不重复扣退。

**架构决策（已与用户确认，实现方不得擅自更改）**

1. **用量落库**：新增 `task.used_tokens`（Integer, `NOT NULL DEFAULT 0`）。
   各节点执行完成后累加写入。这是配额结算的唯一依据——进程内丢失后无法统计。
2. **结算幂等**：新增 `task.quota_settled`（Boolean, `NOT NULL DEFAULT false`）。
   结算只做一次；重复调用必须是无操作（不是「再退一次」）。
3. **四条路径全部接入**：`CANCELLED` / `FAILED` / `WAITING_HUMAN` /
   `WAITING_APPROVAL`（放弃确认执行）。
4. **`WAITING_APPROVAL` 采用「先结算、执行时再预扣」**：该状态不是终态，用户可能
   确认后继续。不做定时超时释放（项目无定时任务基础设施，本次不引入）。
   进入 `WAITING_APPROVAL` 时按规划阶段已用结算；`approve_plan` 时按
   `ESTIMATED - used_tokens` 重新预扣，配额不足则**拒绝确认**（明确提示，而不是跑到一半没钱了）。

---

## 1. 数据模型与迁移

**Files:**
- Modify: `backend/app/db/models/task.py`
- Create: `backend/alembic/versions/<rev>_add_task_quota_settlement.py`
- Test: `backend/tests/test_task_quota_settlement_model.py`

### 字段定义

```python
#: 任务**实际已消耗**的 token 总量（配额结算依据）。
#:
#: 为什么必须落库：配额预扣 100k 在提交时发生，而结算要按真实用量退差额。
#: 真实用量原本只存在于内存 `ExecutionResult` 里，进程重启 / 任务中途取消后就丢了，
#: 导致「未完成任务的预扣永远退不掉」——用户点错一次就白扣 100k。
used_tokens: Mapped[int] = mapped_column(
    Integer, default=0, nullable=False, comment="实际已消耗 token（配额结算依据）"
)

#: 配额是否已结算（**幂等保护**）。
#:
#: 结算会调 `quota.refund()`，重复调用就等于「越退越多」——额度被刷成负成本。
#: 布尔标记 + 同一事务内更新，保证每条任务只结算一次。
quota_settled: Mapped[bool] = mapped_column(
    Boolean, default=False, nullable=False, comment="配额是否已结算（幂等保护）"
)
```

### 迁移 `add_task_quota_settlement`

- `used_tokens`：`Integer`，`server_default='0'`，`NOT NULL`（存量行自动补 0）；
- `quota_settled`：`Boolean`，`server_default=sa.false()`，`NOT NULL`；
- **存量已完成任务的补偿**：已完成任务的预扣已在完成时按实际用量校正过（`refund` 已调用），
  所以迁移时把 `status='completed'` 的行**置 `quota_settled=true`**，
  避免上线后被 `recover()` 或任何重放路径再结算一次。
- 幂等：列已存在则跳过（Phase 1 的教训）。

---

## 2. 用量累加

**Files:**
- Modify: `backend/app/orchestration/services/task_execution.py`

### 累加点

`_execute` 里拿到 `result` 后（无论 success 与否）：

```python
used = sum_actual_tokens(getattr(result, "node_results", {}) or {})
```

> ⚠️ **`node_results` 只含本次调度跑的节点**。重启续跑 / 重试节点时会重复累计吗？
> `sum_actual_tokens` 是对本次 `result` 求和，而 `used_tokens` 是**增量累加**
> （`task.used_tokens += used`）。续跑场景下 `pre_completed` 的节点不在本次
> `node_results` 里，所以不会重复计。

写入位置：`_finish_waiting_human` / `_start_reviewing` / `_finish_reviewing`
各自在事务内累加（都在 `lock_for_update` 之后，串行安全）。

**规划阶段的用量**：`workflow.plan()` / `planner.execute()` 不返回 token 用量，
故规划消耗**无法统计**。这会让「规划后放弃」的任务按 0 用量退款。
**明确取舍**：规划用量通常远小于 100k，且无法获取；宁可少扣用户，也不额外惩罚。
在文档与代码注释里写清这个局限。

---

## 3. 结算函数

```python
async def settle_quota(self, task_id: UUID) -> None:
    """按实际用量结算配额（**幂等**：每任务只结算一次）。

    退款额 = 预扣 − 实际用量（不足则退 0；实际超出预扣**不补扣**，与既有语义一致）。
    """
```

**必须在同一事务内**：先 `lock_for_update` 读 `quota_settled`，若已结算直接返回；
否则 `used_tokens` / `user_id` 取值、置 `quota_settled=True`、commit，
**commit 成功之后**才调 `quota.refund()`（外部副作用不能放在事务内——
Redis 失败回滚不了 DB，而 DB 回滚了 Redis 却已退）。

失败处理：`refund()` 已 fail-open（记 warning），不影响任务终态。

---

## 4. 四条路径接入

| 路径 | 接入点 | 说明 |
| --- | --- | --- |
| `CANCELLED` | `_control_cancel` → `control()` commit 后 | 取消是终态，立即结算 |
| `FAILED` | 目前无显式 FAILED 写入路径；由 `_mark_waiting_human` 覆盖 | 若后续新增，同样接入 |
| `WAITING_HUMAN` | `_finish_waiting_human` + `_mark_waiting_human` | 转人工视为「本次执行结束」，先结算 |
| `WAITING_APPROVAL` | `_plan` 成功转态后 | 先结算规划阶段；`approve_plan` 时重新预扣 |

> ⚠️ `WAITING_HUMAN` 任务可能被 `retry_node` / `supply_input` **继续执行**。
> 继续时同样要重新预扣（与 `approve_plan` 一致）。否则「先结算 → 继续跑」
> 就等于免费额度。

---

## 5. 测试清单（先红后绿）

### 模型/迁移
- [ ] `used_tokens` / `quota_settled` 有正确默认值
- [ ] 迁移覆盖 ORM 全部列（对照既有 `test_migration_schema_consistency`）
- [ ] 存量 `completed` 行迁移后 `quota_settled=true`

### 服务层
- [ ] 任务取消 → `refund` 被调用一次，额度 = `100k − used_tokens`
- [ ] 转人工（调度失败）→ 同样结算
- [ ] 规划失败转人工 → 结算（used_tokens=0 → 退满 100k）
- [ ] **幂等**：`settle_quota` 调两次只退一次
- [ ] 已完成任务：既有行为不变（完成时按实际用量退一次，不重复）
- [ ] 取消后再 `retry_node` 继续 → 重新预扣
- [ ] `approve_plan` 重新预扣；配额不足时**拒绝确认**并给出可读错误
- [ ] `used_tokens` 累加不重复（续跑场景）

### HTTP 层
- [ ] 取消任务后配额减少量 == `used_tokens`（端到端语义）

---

## 6. 验收

- [ ] 本地全量门禁（black/ruff/mypy/pytest ≥80%）
- [ ] **真实服务器验证**：建任务（不执行）→ 取消 → 配额恢复
- [ ] 服务器验证：建任务 → 停在 `waiting_approval` → 配额按规划用量结算（不再占满）
- [ ] 文档：execution-notes + AGENTS.md 待办表更新
- [ ] CI 6/6

---

## 7. 明确不做（写进文档）

- **不做定时超时释放**：需要新的定时任务基础设施，本次不引入。
  `WAITING_APPROVAL` 通过「进入即结算」解决占用问题，不依赖超时。
- **不补扣超支**：实际用量超过 100k 时不额外扣（既有语义，保持一致）。
- **规划阶段用量不统计**：规划 API 不返回 token 用量，无法获取。
