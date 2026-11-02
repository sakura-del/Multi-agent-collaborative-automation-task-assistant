# PRISMA 主动筛选排序使用手册

> 设计：[主动筛选排序设计](../superpowers/specs/2026-11-02-screening-priority-design.md) ·
> 计划：[实施计划](../superpowers/plans/2026-11-02-screening-priority-plan.md)

## 1. 它做什么

在 PRISMA 筛选页的标题/摘要阶段，用**任务内已有的纳入/排除决策**训练一个轻量文本分类器，把尚未筛选的候选按“可能相关”优先展示。

它只改变**展示顺序**：

- 不自动纳入或排除；
- 不修改任何决策、PRISMA 计数或阶段；
- 不需要数据库迁移；
- 默认关闭，关闭时请求和列表行为与旧版完全一致。

## 2. 使用方式

1. 打开任务的 PRISMA 筛选页；
2. 至少完成 **1 条纳入 + 1 条排除** 决策；
3. 点工具栏“智能排序：关”，切换为“开”；
4. 页面顶部显示排序依据（正/负样本数），候选行出现“推荐优先 #n”；
5. 继续人工逐条判断；提交决策后，若开关仍开启，会自动重算顺序；
6. 再次点击开关可关闭并恢复原顺序。

阶段过滤选择“全文”或“已纳入”时，开关会**自动关闭并禁用**；本切片只排序标题/摘要阶段。

## 3. 标签与候选规则

### 训练标签（任务全员）

对任务内每条记录在标题/摘要阶段的全部历史决策：

- 任意一条 `include` → 正样本；
- 没有 `include`、任意一条 `exclude` → 负样本；
- 只有 `maybe` → 忽略（计入 `ignored`）；
- 无决策 → 不参与训练；若仍处于标题/摘要阶段，则进入候选。

规则优先保护召回：同一记录如果一位筛选者纳入、另一位排除，仍按正样本处理。逐人分歧仍由既有双人筛选/冲突流程负责，本功能不重新定义。

### 候选

只有同时满足以下条件的记录才排序：

1. `current_stage == title_abstract`；
2. 任务内该阶段**没有任何决策**。

因此：

- 已纳入/已排除/仅有“待定”的记录不会出现在推荐排序里；
- 已被第一位筛选者处理过的记录不会进入第二位筛选者的推荐队列，但仍出现在普通列表（已知边界）。

## 4. 冷启动与提示

| 状态 | 含义 | 页面行为 |
| --- | --- | --- |
| `ready` | 正负样本足够且有候选 | 按推荐名次置顶，其余记录原顺序追加 |
| `insufficient_labels` | 正样本或负样本为 0 | 保持原顺序，提示还缺哪类标签 |
| `no_candidates` | 没有未筛记录/可用文本 | 保持原顺序，提示无待排序记录 |
| HTTP 503 | 模型/依赖异常 | 保持原顺序，显示错误；不阻塞筛选 |

冷启动至少需要 **1 条纳入 + 1 条排除**，不要为了启用排序而伪造或批量制造决策。

## 5. 模型与可复现性

算法版本：`tfidf-char-logreg-v1`

```text
TF-IDF（char_wb, 2–4 字符 n-gram, max_features=30000, sublinear_tf）
→ LogisticRegression（class_weight=balanced, liblinear, random_state=42）
```

- 文本 = 题名 + 摘要；
- 不调用外部模型，摘要不离开本服务；
- 同一数据 + 同一算法版本产生相同顺序；同分保持原列表顺序；
- 只展示名次，不展示未校准概率；
- 请求时现算，不持久化模型/分数。

它复用 ASReview 的“相关/不相关 + 主动学习排序”方法思路，但不引入 ASReview 的 Flask 应用栈；依赖是 `scikit-learn>=1.5,<2`（BSD-3-Clause）。

## 6. 明确不做

- 全文阶段排序；
- 模拟评估/WSS@95/学习曲线；
- 自动停止筛选；
- 模型持久化或按筛选者独立队列；
- 自动纳入/排除或修改 PRISMA 数字。

这些需要独立设计与验收，不要在本功能上隐式扩展。

## 7. 相关测试

```powershell
cd backend
& 'E:\Multi-agent collaborative automation task assistant\backend\.venv\Scripts\python.exe' `
  -m pytest tests/test_screening_priority.py tests/test_screening_priority_service.py `
  tests/test_screening_api.py -q --no-cov

cd frontend
node node_modules/vitest/vitest.mjs run src/views/ScreeningView.test.ts src/api/screening.test.ts
node node_modules/vue-tsc/bin/vue-tsc.js -b
```
