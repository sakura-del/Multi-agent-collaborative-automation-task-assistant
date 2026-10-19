# Issue #3 遗留项实施计划：前端引用核验结果展示

> 上游：[Issue #3](https://github.com/sakura-del/Multi-agent-collaborative-automation-task-assistant/issues/3)（P0，后端已交付 `42b5e54`）
> 遗留项原文：「**前端展示核验结果**」
> 节奏：AGENTS.md §3（先写失败测试 → 实现 → 全量门禁 → 提交 → 同步服务器 → 文档 → 推送备份）

---

## 0. 关键约束（先摸清，否则做出来是空壳）

### 0.1 数据其实**已经**在后端返回里

`GET /api/v1/tasks/{id}` 的 `review_report` 已含两份核验数据（无需新增 API）：

| 位置 | 内容 |
| --- | --- |
| `review_report.stats.reference_verification` | `{total, verified, metadata_mismatch, not_found, unverified, cached_hits, dropped, intercepted}` |
| `review_report.bibliography[].verification_status` | `verified / metadata_mismatch / not_found / unverified`（未核验时**该键不存在**） |
| `review_report.final_markdown` | 含 `## 引用核验` 章节（纯文本） |

### 0.2 ⚠️ 但演示用的离线任务**默认不做核验**

`task_execution._offline_without_verification()`（R6 修复）：`offline_dataset=true` 且
`reference_verification.verify_offline_tasks=false`（默认）→ **核验器被摘掉**，零外呼。

实测确认（3 个 completed 离线任务）：`stats` 里**没有** `reference_verification` 键、
`bibliography` 条目**没有** `verification_status` 键。

**因此本任务必须同时解决两件事**：
1. 前端能展示核验结果（UI）；
2. **让核验结果在演示路径上真实存在**——否则 UI 永远是空态。

### 0.3 决策（本计划采用）

- **不改默认配置**（`verify_offline_tasks` 仍为 `false`）：离线零外呼是刻意的可复现性保证，
  贸然打开会让演示重新依赖 CrossRef/OpenAlex 可用性（曾因 429 导致超时）。
- 改为**按任务可选**：`LiteratureReviewRequest.verify_references: bool | None`
  （`None` = 沿用全局配置；`True` = 本任务强制核验，即使离线）。
  离线演示时可显式打开，用**缓存**兜住重复核验（TTL 7 天）。
- 前端在核验数据存在时展示统计与逐条徽标；**数据不存在时明确显示「本次未做外部核验」**
  并给出原因说明（而不是假装 0 条）。

---

## 1. 冻结接口

### 1.1 后端：任务级核验开关

```python
# app/orchestration/contracts.py —— LiteratureReviewRequest
verify_references: bool | None = None
"""本任务是否做外部引用核验：None=沿用全局配置；True=强制核验（含离线任务）；False=强制跳过。"""

# app/schemas/task.py —— TaskCreate（HTTP 入参，同名同语义）
verify_references: bool | None = None
```

判定优先级（`task_execution`）：
```
request.verify_references is True   → 核验（即使离线）
request.verify_references is False  → 不核验
None                                → 沿用 _offline_without_verification() 既有逻辑
```
`task.config` 已由 `request.model_dump()` 落库，故重试/恢复路径自动一致。

### 1.2 前端：类型与展示契约

```ts
// src/api/types.ts
export type VerificationStatus = "verified" | "metadata_mismatch" | "not_found" | "unverified";

export interface ReferenceVerificationSummary {
  total: number; verified: number; metadata_mismatch: number;
  not_found: number; unverified: number;
  cached_hits: number; dropped: number; intercepted: boolean;
}

export interface BibliographyEntry {
  index: number; title: string; url: string | null;
  verification_status: VerificationStatus | null;   // null = 该任务未做核验
}
```

新增组件 `src/components/tasks/ReferenceVerificationPanel.vue`：
- props：`summary: ReferenceVerificationSummary | null`、`entries: BibliographyEntry[]`
- 数据存在 → 四态计数 + 拦截开关 + 逐条徽标（在 `TaskResult` 参考文献列表内联）
- `summary === null` → 明确空态：「本次任务未做外部引用核验」+ 一句原因
- 徽标颜色/文案与后端 `_VERDICT_LABELS` 一致：已验证 / 元数据不符 / 未找到 / 未核验
- **必须**有 `data-test` 钩子：`verification-panel`、`verification-summary`、
  `verification-count-<verdict>`、`verification-empty`、`bibliography-verdict-<index>`

### 1.3 `TaskCreateForm` 增加「核验引用」开关

- 默认 **关闭**（与全局默认一致，避免用户无感知地引入外呼延迟）
- 文案说明：开启后会与 CrossRef/OpenAlex 比对，可能增加耗时
- 提交时带 `verify_references: boolean`

---

## 2. 任务拆分与写作用域

| 任务 | 内容 | 写作用域 |
| --- | --- | --- |
| **T1** 后端开关 | `contracts.py` / `schemas/task.py` / `task_execution.py` 判定 + `tests/test_reference_verify_switch.py` | 这 4 个文件（Lead 独占，避免与前端冲突） |
| **T2** 前端组件 | `ReferenceVerificationPanel.vue` + `.test.ts` | 这 2 个文件 |
| **T3** 前端集成 | `TaskResult.vue`(+test)、`TaskCreateForm.vue`(+test)、`types.ts`、`api/tasks.ts`（如需要） | 这些文件 |

Lead 负责：计划文档、T1、门禁、提交、服务器验证、文档。

---

## 3. 验收标准

- [ ] 有核验数据的任务：结果页展示四态计数、拦截开关、逐条徽标（颜色+文案正确）
- [ ] 无核验数据的任务：**明确空态**（不得显示「已验证 0 条」误导用户）
- [ ] 创建任务时可显式开启核验；开启后任务产物**真的**带核验数据（服务器实测）
- [ ] 未开启时行为与现在完全一致（离线零外呼，不回归 R6）
- [ ] `pnpm test` / `pnpm build` / 后端全量门禁全绿

---

## 4. 门禁

```powershell
cd backend
.\.venv\Scripts\python.exe -m black app tests evaluation
.\.venv\Scripts\python.exe -m ruff check --fix app tests evaluation
.\.venv\Scripts\python.exe -m mypy app evaluation
.\.venv\Scripts\python.exe -m pytest tests -q --cov=app --cov-fail-under=80 --basetemp="$env:TEMP\pytest-dsh"
cd ..\frontend
pnpm test
pnpm build
```
