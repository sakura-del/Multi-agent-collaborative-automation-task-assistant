# Phase 2 · 计费套餐（实现计划）

> 创建：2026-10-22 ｜ 状态：**待执行**
> 上游：[产品路线图](../../product/2026-10-18-demo-to-product-roadmap.md)（§7 建议档位）
> 前置：PRISMA ✅ + 可观测性 ✅ + 团队工作区 ✅ + 开放 API ✅（**Phase 2 最后一项**）

---

## 0. 现状与要解决的核心矛盾

### 已有什么

| 组件 | 现状 |
| --- | --- |
| 配额 | `QuotaService`：Redis 按 **UTC 日** 扣 token，`daily_token_limit=1_000_000`，Lua 原子扣减 + 退还 |
| 预扣机制 | 建任务时预扣 100k → 任务终态按实际用量退还差额（四类终止路径都结算） |
| 席位 | `workspace.seat_limit` 已存在（机构版计费单位） |
| 用量数据 | `task.used_tokens` 已落库（**成本可核算**） |
| 计量基础 | `api_key` 已可归属调用方 |

### 要解决的核心矛盾（**这是本项最重要的问题**）

现有配额是「**按天 token 上限**」，套餐是「**按月篇数**」。两者并存会**双轨打架**：

- 用户买了「专业版 100 篇/月」，但某天跑了 3 篇就撞了日 token 上限 → 明明有额度却用不了；
- 反过来，日 token 上限宽松时，免费用户一天跑 50 篇 → 篇数额度形同虚设；
- 两个 429 的 `error_code` 都是 `QUOTA_EXCEEDED`，用户不知道是「今天超了」还是「本月超了」；
- 预扣 100k 与「篇数」是不同量纲，结算逻辑无法复用。

**结论：必须有单一事实源（single source of truth）**。

---

## 1. 关键设计决策：篇数为主，token 为辅

### 决策

| 维度 | 角色 | 说明 |
| --- | --- | --- |
| **篇数（月）** | **准入控制（主）** | 决定「能不能建任务」。这是**用户可理解、可销售**的单位 |
| **token（日）** | **成本护栏（辅）** | 防单用户单日烧穿成本。**不面向用户销售**，只在异常时触发 |

### 为什么是篇数为主

1. **销售语言**：竞品（Rayyan 3 reviews / Covidence 1–3 reviews）都按篇数，用户能比价。
   「100 万 token」没人能理解值多少钱。
2. **成本可预测**：一篇综述的 token 用量已经稳定（`used_tokens` 实测中位 10 万上下），
   按篇数卖可以把成本波动风险留在自己这边（用日 token 护栏兜底）。
3. **与既有预扣兼容**：篇数检查发生在**建任务前**（一次原子 `INCR`），
   token 预扣继续按原逻辑跑。两者**不同量纲、不冲突**。

### 为什么保留日 token 护栏

免费用户如果一天跑 50 篇（篇数额度若配置宽松或代码有 bug），成本会失控。
日 token 上限是**兜底**，不对外宣传、不出现在定价页。

**关键**：护栏触发时的错误码必须与「篇数用尽」**区分**，否则用户会误以为买套餐能解决。

---

## 2. 数据模型

### 2.1 `plan`（套餐定义，**配置化而非硬编码**）

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | String(32) PK | `free` / `student` / `pro` / `institution` |
| `name` | String(64) | 展示名 |
| `monthly_reviews` | Integer | 每月篇数（`-1` = 不限） |
| `max_workspaces` | Integer | 可创建工作区数（机构版关键） |
| `max_seats_per_workspace` | Integer | 单工作区席位上上限 |
| `allow_api_access` | Boolean | 是否可用开放 API / MCP |
| `allow_team` | Boolean | 是否可用团队工作区 |
| `price_cents` | Integer | 月费（分）；0 = 免费 |
| `is_active` | Boolean | 下架用（**不删行**：历史订阅要能解析） |

**为什么套餐是表而不是代码常量**：
定价会变、会有促销档、机构版要定制。写死在代码里每次改价都要发版，
且历史订阅无法回溯「当时买的是什么」。

**为什么 `is_active` 而不删行**：用户的历史订阅指向 `plan_id`，
删掉会让「我当初买的什么」无从查证。

### 2.2 `subscription`（用户订阅）

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | UUID PK | |
| `user_id` | UUID FK | 一个用户同时只有**一条 active** |
| `plan_id` | String(32) FK→plan | |
| `status` | String(16) | `active` / `expired` / `cancelled` |
| `period_start` / `period_end` | DateTime | 计费周期（自然月或自定义） |
| `seats_purchased` | Integer | 机构版买的席位数（个人版 = 1） |
| `source` | String(16) | `manual`（手工开通）/ `payment`（支付网关，**本轮不实现**） |
| `note` | Text | 手工开通时记录「谁开的、为什么」 |

**约束**：`UNIQUE(user_id) WHERE status='active'`（部分唯一索引，PostgreSQL 支持；
SQLite 用应用层保证 + 测试覆盖）。

**为什么一个用户只能有一条 active**：多条 active 会让「这个月还能跑几篇」无法判定。
升级 = 旧订阅 `cancelled` + 新订阅 `active`（同一事务）。

### 2.3 `usage_counter`（月度篇数计数）

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | UUID PK | |
| `user_id` | UUID FK | |
| `period_key` | String(8) | `2026-10`（UTC 年月） |
| `reviews_used` | Integer | 已用篇数 |
| UNIQUE(`user_id`, `period_key`) | | |

**为什么用数据库而不是 Redis**：
篇数是**计费依据**，必须持久、可审计、可与 `task` 表对账。
Redis 丢了就是「用户说用了 3 篇、系统说 5 篇」的纠纷。
（token 护栏继续用 Redis —— 它是成本护栏，精度要求低。）

**为什么按 UTC 年月而不是订阅周期**：
自然月边界简单、可预测、与用户认知一致。订阅周期跨月时按自然月切分
（例：10-15 订阅，10 月剩余额度 = 全月额度，不做按比例折算 —— 简化，
写进 runbook 作为已知行为）。

### 2.4 与既有配额的关系（**必须写清，否则实现会打架**）

```
建任务请求
  │
  ├─ ① 套餐篇数检查（数据库，原子 UPDATE ... WHERE reviews_used < limit）
  │     └─ 超限 → 429 PLAN_QUOTA_EXCEEDED（"本月 N 篇已用完，升级套餐或等下月"）
  │
  ├─ ② token 成本护栏（Redis，既有 QuotaService）
  │     └─ 超限 → 429 TOKEN_BUDGET_EXCEEDED（"今日 token 预算已用尽，请明天再试"）
  │
  └─ ③ 预扣 token 100k（既有逻辑，不变）
        └─ 任务终态按实际用量退还差额（既有逻辑，不变）
```

**顺序很重要**：先查篇数（用户可理解的限制），再查 token（内部护栏）。
反过来的话，用户会先看到「token 超限」这种他无法理解的错误。

**篇数扣减时机**：建任务成功即扣（与预扣 token 同时）。
任务失败**不退还篇数** —— 因为已经消耗了真实算力。这是与 token 预扣的关键区别，
必须写进产品文案与 runbook。

---

## 3. 与既有功能的联动

| 功能 | 联动规则 |
| --- | --- |
| **团队工作区** | `plan.allow_team=false` → 建工作区 403；`max_workspaces` 限制创建数；`max_seats_per_workspace` 覆盖 `workspace.seat_limit` 上限 |
| **开放 API / MCP** | `plan.allow_api_access=false` → 用 Key 调 API 返回 403（错误码 `PLAN_FEATURE_UNAVAILABLE`） |
| **配额（token）** | 继续生效，作为护栏；错误码区分 |
| **席位** | `workspace.seat_limit` 的**上限**由套餐约束（创建/扩容时校验） |

**为什么 API 访问要绑套餐**：免费用户用 API 刷任务的成本风险远高于网页
（脚本可以无限快）。这是行业惯例（Rayyan/Covidence 的 API 都是付费档）。

---

## 4. 支付接入（**本轮不接真实支付**）

### 决策：手工开通 + 套餐记录

| 做什么 | 不做什么 |
| --- | --- |
| `subscription.source='manual'` + `note` 记录开通原因 | 接支付宝/微信/Stripe |
| 前端「我的套餐」页展示当前套餐与用量 | 支付回调 / 对账 / 退款流程 |
| 管理员手工开通/变更/取消（用 `role='admin'`） | 自动续费 |

**为什么不接真实支付**：

1. **没有商户资质**（个人主体无法开通支付宝/微信商户）；
2. 支付网关涉及回调验签、幂等、对账、退款 —— 是**独立子系统**，成本高于套餐本身；
3. 当前阶段要验证的是「套餐模型与配额联动是否正确」，不是「能否收钱」。

**接口预留**：`subscription.source` 枚举已含 `payment`，
未来接网关只需写一个「支付成功 → 创建 subscription」的适配器，
不改套餐/配额逻辑。

---

## 5. 实现任务分解

### Task 1：数据模型 + 迁移 + 套餐种子

- `app/db/models/billing.py`：`Plan` / `Subscription` / `UsageCounter`
- 迁移 `add_billing`（幂等）+ **种子数据**（4 个套餐）
- 测试：模型约束、迁移 upgrade/downgrade/幂等、种子幂等

### Task 2：套餐服务（订阅与用量）

- `app/billing/services/plan_service.py`
  - `get_active_plan(user_id)` → 无订阅则返回 `free`（**默认档，不建订阅行**）
  - `subscribe(user_id, plan_id, period, note)` / `cancel(user_id)` / `change_plan(...)`
  - `check_and_consume_review(user_id)` → 原子扣篇数；超限抛 `PlanQuotaExceeded`
  - `get_usage(user_id)` → 当前周期用量与剩余
- 测试：默认免费档、订阅/升级/降级、篇数原子扣减、跨月重置、并发不超发

### Task 3：接入建任务流程 + 错误码区分

- `tasks.py::create_task`：**先查篇数，再查 token**
- 新增 `PlanQuotaExceeded`（429，`PLAN_QUOTA_EXCEEDED`）与
  `TokenBudgetExceeded`（429，`TOKEN_BUDGET_EXCEEDED`）
- 测试：两条限制的**顺序**、错误码区分、免费档默认限制、升级后可继续

### Task 4：功能门禁（团队 / API / 席位）

- `allow_team` / `max_workspaces` / `max_seats_per_workspace` 接入工作区服务
- `allow_api_access` 接入 `get_current_user_or_api_key`
- 测试：免费档不能用团队/API、学生档可用、机构版席位上限

### Task 5：管理端点 + 前端「我的套餐」页

- `GET /api/v1/billing/plan`（我的套餐 + 用量）
- `GET /api/v1/billing/plans`（套餐列表，公开）
- `POST /api/v1/billing/subscribe`（**admin only**，手工开通）
- 前端 `/settings/plan`：当前套餐、本月用量进度条、可升级套餐列表、功能对照表
- 测试：端点契约、权限（普通用户不能开通）、前端组件

### Task 6：端到端验收

- 服务器：免费档建任务（第 2 篇 429）→ 手工升级 → 可继续 → 团队/API 门禁生效
- 清理探针（订阅/用量/任务 + quota key）
- 浏览器走查

---

## 6. 风险与对策

| 风险 | 对策 |
| --- | --- |
| **双轨配额打架** | 明确主辅：篇数准入、token 护栏；**错误码区分**；顺序固定（篇数先） |
| 篇数超发（并发建任务） | 数据库原子 `UPDATE ... WHERE reviews_used < limit`，不用「先查后写」 |
| 升级/降级状态不一致 | 同一事务：旧订阅 `cancelled` + 新订阅 `active`；应用层保证单 active |
| 免费档被滥用 | 免费档 `monthly_reviews=1` + `allow_api_access=false` + token 护栏 |
| 自然月与订阅周期不一致 | 明确按 UTC 自然月计；跨月订阅不折算（写进 runbook） |
| 历史任务无用量记录 | 存量 `task.used_tokens` 已有；`usage_counter` 只从上线日起算（不回填） |

---

## 7. Exit Gate

- [ ] 迁移可 upgrade / downgrade / 重复 upgrade；种子幂等
- [ ] 后端全量门禁绿（覆盖率不下降）
- [ ] **两条限制的错误码明确区分**（有测试）
- [ ] 篇数并发扣减不超发（有测试 + 回退验证）
- [ ] 功能门禁（团队/API/席位）全部有测试
- [ ] 服务器端到端：免费档撞限 → 升级 → 继续 → 门禁生效
- [ ] 清理回查（订阅/用量/任务/quota key + demo 数据未变）
- [ ] CI 6/6 绿
- [ ] 文档：execution-notes + 路线图勾选 + runbook（套餐口径、手工开通流程）
