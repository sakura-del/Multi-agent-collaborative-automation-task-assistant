# 计费套餐

> Phase 2 最后一项交付。适用对象：运营（开通套餐）、开发（理解双配额）、支持（解释 429）。
> 计划：[2026-10-22-01-billing-plans-plan.md](../superpowers/plans/2026-10-22-01-billing-plans-plan.md)

---

## 1. 两个 429 不是一回事（**最重要的一节**）

用户最容易被这里搞混，支持同学请先看这张表：

| 错误码 | 限制维度 | 存储 | 用户该怎么办 |
| --- | --- | --- | --- |
| `PLAN_QUOTA_EXCEEDED` | **篇数（月）** | 数据库 `usage_counter` | **升级套餐** 或等下一自然月 |
| `TOKEN_BUDGET_EXCEEDED` | **token（日）** | Redis | **明天再试**（买套餐也没用） |

**为什么必须分开**：两者混用会让用户以为「买套餐能解除 token 限制」，
买完发现还是不行 —— 这是真实的客诉来源。

**检查顺序**：建任务时**先查篇数，再查 token**。反过来的话用户会先看到
「token 预算用尽」这种他无法理解的错误，而实际撞的是篇数限制。

**为什么不给用户看 token 数字**：token 是**内部成本护栏**，不是销售单位。
定价页只出现「篇数」。

---

## 2. 四个套餐

| 套餐 | 篇数/月 | 工作区 | 席位/工作区 | 团队 | 开放 API | 价格 |
| --- | --- | --- | --- | --- | --- | --- |
| `free` | 1 | 0 | 0 | ❌ | ❌ | ¥0 |
| `student` | 10 | 1 | 3 | ✅ | ❌ | ¥19 |
| `pro` | 50 | 5 | 10 | ✅ | ✅ | ¥69 |
| `institution` | 不限 | 100 | 200 | ✅ | ✅ | ¥1999 |

**免费档必须严格**：宽松会让付费转化与成本控制同时失效。
**API 只给专业档及以上**：脚本刷任务的成本风险远高于网页（行业惯例）。

套餐是**数据库表**（`plan`）而不是代码常量：改价不发版、历史订阅可回溯。
下架用 `is_active=false`，**不删行**（删了历史订阅的 `plan_id` 就指向不存在）。

---

## 3. 手工开通套餐（本轮无支付网关）

### 为什么没有支付

1. 个人主体无商户资质（支付宝/微信商户号需要企业资质）；
2. 支付涉及回调验签、幂等、对账、退款 —— 是**独立子系统**，成本高于套餐本身；
3. 当前阶段要验证的是「套餐模型与配额联动是否正确」，不是「能否收钱」。

`subscription.source` 已含 `payment` 枚举，将来接网关只需写一个
「支付成功 → 创建 subscription」的适配器，**不改**套餐/配额逻辑。

### 开通流程

```bash
# 1) 找到用户 ID
docker compose exec -T postgres psql -U maa -d multi_agent_assistant \
  -c "SELECT id, username, email FROM \"user\" WHERE username = 'someone';"

# 2) 用管理员账号开通（需要 role=admin）
curl -s -X POST http://10.0.10.2:48080/api/v1/billing/subscribe \
  -H "Authorization: Bearer $ADMIN_JWT" -H 'Content-Type: application/json' \
  -d '{"user_id":"<uuid>","plan_id":"pro","note":"微信转账 ¥69，2026-10-22","days":30}'

# 3) 取消（回落免费档）
curl -s -X POST http://10.0.10.2:48080/api/v1/billing/cancel/<user_id> \
  -H "Authorization: Bearer $ADMIN_JWT"
```

**`note` 必须写**：这是唯一的对账依据（谁开的、为什么、收了多少钱）。

### 给管理员权限

```sql
UPDATE "user" SET role = 'admin' WHERE username = 'someone';
```

---

## 4. 用量口径（**已知的简化**）

### 按 UTC 自然月

`period_key` = `YYYY-MM`（UTC）。选自然月而非订阅周期：边界简单、可预测、
与用户认知一致。

**跨月订阅不折算**：10-15 订阅专业版，10 月仍享**全月** 50 篇额度。
这是有意的简化（按比例折算会让「剩余几篇」出现小数，用户更难理解）。

### 篇数**不退还**

任务失败也消耗了真实算力，所以篇数不退。这与 token 预扣不同
（token 在任务终态按实际用量退还差额）。

**产品文案必须说清**，否则用户会觉得「任务失败了还扣我额度」。

### 存量数据不回填

`usage_counter` 只从上线日（2026-10-22）起算。存量任务的 `used_tokens` 仍在
`task` 表，可用于成本分析，但**不计入**篇数额度（否则老用户上线即超限）。

---

## 5. 运维查询

```sql
-- 某用户当前套餐与用量
SELECT s.plan_id, s.status, s.period_start, s.period_end, s.note
FROM subscription s WHERE s.user_id = '<uuid>' AND s.status = 'active';

SELECT period_key, reviews_used
FROM usage_counter WHERE user_id = '<uuid>' ORDER BY period_key DESC LIMIT 6;

-- 本月用量排行（容量规划用）
SELECT u.username, c.reviews_used
FROM usage_counter c JOIN "user" u ON u.id = c.user_id
WHERE c.period_key = to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM')
ORDER BY c.reviews_used DESC LIMIT 20;

-- 付费用户数（免费档不建订阅行，所以这就是付费数）
SELECT plan_id, count(1) FROM subscription
WHERE status = 'active' GROUP BY plan_id;

-- 即将到期（7 天内）
SELECT user_id, plan_id, period_end FROM subscription
WHERE status = 'active' AND period_end < now() + interval '7 days';
```

### 常见问题

| 现象 | 排查 |
| --- | --- |
| 用户说「刚升级还是不能建任务」 | 查 `subscription` 是否 `active` 且 `period_end > now()`；再看 `usage_counter` 本月 `reviews_used` 是否已满 |
| 升级后额度没增加 | 篇数按月计，升级**不重置**当月已用（与「换套餐送额度」是不同产品设计，当前不送） |
| 免费用户调 API 返回 403 | 正常（`allow_api_access=false`）。错误码 `PLAN_FEATURE_UNAVAILABLE` |
| 建工作区 403 | 三种可能：无团队功能 / 工作区数量满 / 席位超上限。看 `error_code` 区分 |

---

## 6. 已知局限

| 局限 | 说明 |
| --- | --- |
| 无支付网关 | 手工开通（见 §3） |
| 无自动续费 | 到期即回落免费档，需手工续 |
| 升级不送当月额度 | 已用篇数不重置（避免「月初升级白拿」的套利） |
| 无按 Key 计费 | 配额按 `user_id`，不按 Key |
| 跨月订阅不折算 | 见 §4 |
| 篇数不退还 | 见 §4（失败任务也算消耗） |
