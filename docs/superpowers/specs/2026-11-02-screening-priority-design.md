# ASReview 式主动筛选排序设计（PRISMA 内置）

> 日期：2026-11-02 ｜ 状态：用户已确认三段设计，待 spec 审核
>
> 背景：[竞品扫描与下一轮迭代建议](../../product/2026-10-29-competitive-scan-and-next-iteration.md)
> 参考：[ASReview PyPI](https://pypi.org/project/asreview/)（Apache-2.0）·
> [scikit-learn](https://scikit-learn.org/stable/)

## 1. 目标与范围

### 1.1 目标

在现有 PRISMA 筛选工作台内增加**主动学习优先级排序**，让已出现纳入/排除决策后，未筛记录按模型认为的相关性优先展示，减少无效阅读。保留全部人工判断和既有决策/PRISMA 语义。

### 1.2 本轮只做

- 仅标题/摘要阶段排序；
- 任务全员决策作为训练标签；
- 现有筛选页增加一个默认关闭的“智能排序”开关；
- 后端只读接口 + 前端置顶展示；
- 不持久化模型、分数或新表。

### 1.3 明确不做

- 不做全文阶段排序；
- 不做自动纳入/排除，不改变任何决策或 PRISMA 计数；
- 不做 ASReview 的模拟评估、WSS@95、学习曲线或独立项目工作区；
- 不引入 ASReview 的 Flask 应用栈；
- 不做模型持久化、后台重算、跨任务/跨用户模型；
- 不展示模型概率或把模型分数写成“质量分”；
- 不做自动停止筛选；
- 不新增数据库迁移。

## 2. 关键设计决定

| 决定 | 选择 | 原因 |
| --- | --- | --- |
| 训练标签来源 | **任务全员决策** | 双人筛选共享同一文献库；全员标签能更早形成训练信号 |
| 标签聚合 | 任一 `include` → 正；无 `include` 且有 `exclude` → 负；只有 `maybe` → 忽略 | 优先保护召回，避免把有争议的候选推向排除 |
| 候选范围 | 当前阶段为 `title_abstract`、**任务内尚无任何该阶段决策**的记录 | 防止已标注记录又成为待排序候选，造成数据泄漏 |
| 冷启动 | 正、负样本各至少 1 条 | 没有两类标签无法训练二分类器；不足时如实退回原序 |
| 排序时机 | 请求时现算 | 记录上限 1000，无需新建模型表/后台任务；避免状态失效 |
| 模型 | TF-IDF 字符 n-gram + 逻辑回归 | 轻量、确定性、可单测；字符 n-gram 适配中英文 |
| 展示 | 只显示推荐序号，不显示未校准概率 | 小样本概率不可解释，避免虚假精确 |
| 默认状态 | 开关关闭 | 保持现有行为、既有测试和用户预期不变 |

### 2.1 双人筛选的已知边界

排序候选要求“任务内无任何决策”，因此某条记录被第一位筛选者处理后，不会进入第二位筛选者的智能优先队列；它仍出现在普通列表，第二位筛选者可按正常流程处理。

这是有意取舍：本切片优先保证**任务级去重工作量**与无数据泄漏；按筛选者各自的候选队列留待后续。

## 3. 数据语义

### 3.1 标签

对任务内每条记录，取其在 `title_abstract` 阶段的全部历史决策：

- 任意一条 `include` → 正样本；
- 没有 `include`，任意一条 `exclude` → 负样本；
- 只有 `maybe` → 忽略（计入 `ignored`）；
- 无决策 → 不参与训练；若仍处于 `title_abstract`，则是候选。

**不按“每人的最新决策”覆盖历史**：本切片的标签是任务级召回信号，历史 `include` 不应因为后来有人点 `maybe` 而消失。真正的逐人分歧仍由现有 `find_conflicts`/双人流程处理，不在此接口重定义。

### 3.2 候选

一条记录同时满足以下条件才进入排序候选：

1. `current_stage == title_abstract`；
2. 任务内该阶段没有任何决策。

已纳入（进入 `fulltext`/`included`）的记录、已排除记录、仅有 `maybe` 的记录都不进入候选；它们仍按现有逻辑显示在普通列表。

### 3.3 文本

训练和候选文本统一为：

```text
title + "\n" + abstract
```

空摘要不影响；若所有训练/候选文本都为空，返回 `no_candidates` 并在 `message` 说明“缺少可用文本”，不伪造结果。

## 4. 后端架构

### 4.1 模块边界

新增纯排序模块：

```text
backend/app/knowledge/services/screening_priority.py
```

职责：

- 定义 `LabeledRecord`、`PriorityCandidate`、`PriorityRanking` 等纯数据对象；
- 实现 `rank_candidates(labeled, candidates)`；
- 不导入 SQLAlchemy、FastAPI、数据库模型；
- 固定算法版本字符串 `tfidf-char-logreg-v1`。

新增/修改：

| 文件 | 职责 |
| --- | --- |
| `backend/app/db/repositories/screening.py` | 增加一次查询任务内某阶段全部决策的方法，避免 N+1 |
| `backend/app/knowledge/services/screening_service.py` | 加载记录/决策、聚合标签、选候选、调用纯排序器 |
| `backend/app/schemas/screening.py` | 新增响应 DTO |
| `backend/app/api/v1/endpoints/screening.py` | 新增只读端点、阶段校验、异常映射 |
| `backend/pyproject.toml` | 增加 `scikit-learn>=1.5,<2` 运行时依赖 |

不新增数据库表、不修改迁移 head。

### 4.2 查询与事务

`ScreeningService.priority(task_id)`：

1. 读取任务记录（沿用现有 1000 条上限与稳定排序）；
2. 一次查询该任务 `title_abstract` 阶段的全部决策；
3. 按记录聚合标签；
4. 选候选；
5. 调用纯排序器；
6. 返回结果。

只读接口不 commit、不写库。归属校验复用 `ensure_task_owned`（失败 404）。

### 4.3 算法

```text
TfidfVectorizer(
    analyzer="char_wb",
    ngram_range=(2, 4),
    max_features=30000,
    min_df=1,
    sublinear_tf=True,
    lowercase=True,
)
→ LogisticRegression(
    C=1.0,
    class_weight="balanced",
    solver="liblinear",
    random_state=42,
    max_iter=1000,
)
```

- 使用 `decision_function` 分数排序，不转概率；
- 按分数降序、同分保持输入列表顺序（Python 稳定排序）；
- 同一数据、同一算法版本必须产生相同顺序；
- 不调用外部模型/网络。

## 5. API 契约

```http
GET /api/v1/screening/priority?task_id=<uuid>&stage=title_abstract
```

- 需要 Bearer access token；
- `task_id` 不属于当前用户 → 404；
- `stage` 只允许 `title_abstract`；其他值 → 400；
- 不传 `stage` 时默认 `title_abstract`。

成功响应：

```json
{
  "status": "ready",
  "algorithm": "tfidf-char-logreg-v1",
  "stage": "title_abstract",
  "items": [
    {"record_id": "2c3f...", "rank": 1},
    {"record_id": "8a1b...", "rank": 2}
  ],
  "labels": {"positive": 3, "negative": 8, "ignored": 1},
  "candidates": 42,
  "message": "基于任务内 3 条纳入 / 8 条排除决策排序"
}
```

状态：

| `status` | 条件 | `items` |
| --- | --- | --- |
| `ready` | 正、负各至少 1，且有候选 | 完整排序 |
| `insufficient_labels` | 正样本或负样本为 0 | 空数组 |
| `no_candidates` | 无候选，或所有候选/训练文本为空 | 空数组 |

训练/推理异常不伪装成上述状态，映射为 **503** + 可读 `detail`；前端捕获后保持原顺序并显示错误。

## 6. 前端设计

文件：

- `frontend/src/api/screening.ts`：新增类型与 `getPriority()`；
- `frontend/src/views/ScreeningView.vue`：开关、加载态、提示、排序展示；
- `frontend/src/views/ScreeningView.test.ts`：覆盖默认关闭、ready、不足、错误、决策后刷新。

行为：

1. 工具栏增加“智能排序”开关（`Button` + `aria-pressed`，项目无 Switch 组件），默认关闭；
2. 关闭时不请求 priority 接口，列表与现有行为完全一致；
3. 开启后请求接口：
   - `ready`：候选按 rank 置顶；未在 items 中的记录按原顺序追加；已筛记录不隐藏；
   - `insufficient_labels`：保持原顺序，显示 `message` 和正/负样本数；
   - `no_candidates`：保持原顺序，显示“没有待排序记录”；
   - 请求失败：保持原顺序，显示错误，不阻塞筛选；
4. 候选行显示 `推荐优先 #n` 徽标；不显示分数；
5. 提交决策成功后，若开关开启，同时刷新列表、PRISMA 和 priority；
6. 所有新元素用 `data-test` 定位，至少包含：
   - `screening-priority-toggle`
   - `screening-priority-status`
   - `screening-priority-rank-<record_id>`

## 7. 错误、性能与安全

- 任务归属：统一 404，不泄漏存在性；
- 阶段非法：400，列出允许值；
- 训练异常：503，服务端记录日志；前端原序降级；
- 文本缺失：不报 500，返回 `no_candidates` + 明确 message；
- 不做外部调用，摘要不离开本服务；
- 1000 条记录内请求时现算；实现后需实测训练+排序耗时并写入验收记录，不预设未经验证的 SLA；
- 现有“默认关闭”保证不增加普通用户请求成本。

## 8. 测试计划

### 8.1 纯排序器

- 正/负样本可分时，正类候选排在负类候选前；
- 同一输入重复调用顺序完全一致；
- 中文标题/摘要可训练并排序；
- 同分候选保持输入顺序；
- 正样本或负样本为空时返回 `insufficient_labels`；
- 候选为空时返回 `no_candidates`；
- 文本全空时返回 `no_candidates`，不抛异常。

### 8.2 Service / Repository

- 标签聚合：任一 include → 正；无 include 有 exclude → 负；仅 maybe → ignored；
- 候选排除：已有决策（含另一位筛选者）的记录不进入候选；
- 任务隔离：A 任务的决策/记录不影响 B；
- 已进入 fulltext 的正样本仍参与训练；
- 不修改任何决策/PRISMA 数据。

### 8.3 API

- 未鉴权 → 401；
- 他人任务 → 404；
- 非法 stage → 400；
- ready 响应 rank 连续、items 无分数；
- insufficient/no_candidates 空 items + 明确 message；
- 训练异常 → 503（用 fake ranker 注入异常）；
- 响应包含固定 `algorithm` 版本。

### 8.4 前端

- 默认关闭，不调用 priority API；
- 开启 ready：候选置顶、其他记录保留、徽标正确；
- insufficient/no_candidates：原顺序 + 提示；
- API 失败：原顺序 + 错误提示；
- 决策成功后开关开启会重新请求；
- 现有 ScreeningView 测试与 `vue-tsc -b` 全绿。

## 9. 验收标准

| # | 验收项 | 证据 |
| --- | --- | --- |
| A1 | 排序确定、可复现 | 重复调用顺序断言 + 固定算法版本 |
| A2 | 标签语义正确 | Service 聚合测试（include/exclude/maybe/无决策） |
| A3 | 无数据泄漏 | 有决策记录绝不出现在候选/排序 items |
| A4 | 冷启动诚实 | 不足时原序 + 明确提示，不返回假排序 |
| A5 | 不改变既有流程 | 决策/PRISMA 相关测试全绿；默认关闭零额外请求 |
| A6 | 中英文可用 | 中英文文本排序测试 |
| A7 | 安全与边界 | 401/404/400/503 测试；无新迁移、无外部调用 |
| A8 | 前端可解释 | 只显示 rank，不显示概率；提示训练样本数 |

## 10. 依赖与部署影响

- 新增运行时依赖 `scikit-learn>=1.5,<2`（BSD-3-Clause）；
- 后端镜像需重新构建并验证导入；
- 不引入 ASReview 包本身，因此不会带入 Flask/Flask-SQLAlchemy 等整套 Web 栈；
- 本地 venv 需要安装该依赖后运行单测/实现；
- 无 Alembic 变更、无新增后台服务。

## 11. 后续扩展（不在本切片）

- 全文阶段主动学习；
- ASReview 式模拟评估/WSS@95/学习曲线；
- 按筛选者独立队列；
- 模型/分数持久化与跨会话复现；
- 主动学习停止准则；
- 纳入/排除冲突感知标签。
