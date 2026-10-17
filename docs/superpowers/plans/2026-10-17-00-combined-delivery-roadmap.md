# 双仓库合并开发路线（2026-10-17）

> **定位**：本文件定义 `multi-agent-research-thesis`（GPT Researcher 基线仓库，下称 B）与本仓库
> `Multi-agent collaborative automation task assistant`（自研文献综述主系统，下称 A）的合并关系、
> 统一排期与执行门禁。
>
> 架构、接口、任务步骤仍以 `docs/superpowers/specs/2026-08-09-literature-review-design.md`（下称
> 设计规格）与五份子计划为唯一事实来源。本文件不修改任何任务步骤，只补充跨仓库约束与时间表。
>
> 目标截止：**2027 年 3 月**（2026-09-30 对话中确认的"明年 3 月"，最终以你确认为准）。

---

## 1. 两个仓库的现状

### 1.1 A：主系统（本仓库）

| 项 | 状态 |
|---|---|
| 分支 / 提交 | `codex/literature-review` @ `57cd5d3`，工作树干净 |
| M1 安全基线 | 已通过（2026-08-21 @ `4df8189`）：573 tests 全绿、Compose 五容器 ready、双用户隔离实测通过 |
| 2026-10-17 基线复验 | `573 passed`，覆盖率 91%（host 侧需 `--basetemp`，原因见 §5.2） |
| Plan 1 | 已完成（6 个任务，见 execution-notes 执行日志） |
| Plan 2-5 | **尚未开始**：任务运行时 7 task / 证据级文献工作流 6 task / 前端工作台 8 task / 加固与实验 8 task |
| 前端 | 仍是阶段 0 登录联调页（Plan 4 才会重建） |
| 已冻结契约 | 设计规格 §3 范围、§5 接口、§12 实验设计、§13 十周路线 |

### 1.2 B：基线仓库（GPT Researcher fork）

位置：`C:\Users\Lenovo\Documents\Codex\2026-09-30\new-chat\outputs\multi-agent-research-thesis`
GitHub：`sakura-del/multi-agent-research-thesis`

| 项 | 状态 |
|---|---|
| 上游 / 工作提交 | `assafelovic/gpt-researcher` @ `0957c301`（固定）；工作提交 `380230e4` |
| 模型 | `gpt-6-luna`（rhzy.ai OpenAI-compatible Responses 接口） |
| 语料 | 5 篇固定版本文本型 PDF（RAG / Self-RAG / CRAG / RARR / RAGAs），`thesis/corpus_manifest.json` 记录 sha256、页数、文本字符数 |
| 问题 | `thesis/questions.json`：3 个不可变问题，含必引论文与预期要点 |
| 运行记录 | `thesis/runs/<run-id>/`：固定输入、输入哈希、环境、console、graph 事件、LLM 请求/响应、usage、报告、观测 |
| 基线进度 | Q1/Q2 于 2026-09-30 完成；**Q3 于 2026-10-17 补跑完成**（269.5s / 128,417 tokens）；三次运行均为 `completed` |
| 仓库归档 | 运行记录已提交（`7a0cd9b6`），tag `baseline-v1` 已推送；`THESIS.md` 标记 frozen，运行清单见 `thesis/runs/README.md` |

### 1.3 基线观测（论文"问题与动机"章的直接证据）

| 指标 | Q1 机制对比 | Q2 证据修订 | Q3 评价设计 |
|---|---|---|---|
| 状态 | completed，`quality_status=not_accepted` | 同 | completed（2026-10-17 补跑） |
| 耗时 | 401.5s | 246.5s | 269.5s |
| 调用 / Token | 13 次 / 134,104 | 13 次 / 137,691 | 13 次 / 128,417 |
| 引用链接 | 57 条，其中 **34 条无法在固定输入集中定位** | 42 条，其中 **22 条无法定位** | 34 条，0 条无法定位 |
| 必引未引 | `rag`（原始 RAG 论文）未引用 | `self_rag` 未引用 | 无 |
| Fact Check 修订 | 0 次（"Draft accepted by Fact Checker"） | 0 次 | 0 次 |
| 报告字符 | 38,690 | 36,238 | 35,183 |
| 观测状态 | `needs_evidence_review`，`semantic_support=not_checked` | 同 | 同 |

可从 B 的运行记录直接引用的事实：

1. **流程跑通 ≠ 结论有据。** Q1 的 57 条引用中有 34 条无法在固定输入集中定位，且原始 RAG 论文本身未被引用；Q2 同类问题（22/42 无法定位，缺 Self-RAG）；Q3 引用虽全部可定位，但同样没有页码/片段级证据。
2. **上游 Fact Checker 是模型自审。** 三问均为 0 次修订、无原文页码/片段级证据，观测层只能标 `not_checked`。
3. **"本地资料"模式仍会联网。** Q1 console 出现 `Active retrievers: ['Duckduckgo']` → `Starting web search`，随后对 wikipedia/brave/yahoo/startpage/mojeek 等发起请求，最终 "0 results"；离线可复现性不完整，也是隐私/合规风险。
4. **长任务无恢复。** Q3 开始约 2 分钟后中止，无任何可续跑状态；说明 A 的"PostgreSQL 为事实来源 + 断点恢复"是刚需而不是过度设计。

---

## 2. 合并结论：A 主 B 辅，不合并代码

**A = 主系统与答辩交付物；B = 冻结基线（对照实验 + 问题证据 + 实验协议模板）。**

不合并代码的四个理由：

1. **架构/工具链差异大**：B 是 Python 3.12 + uv + LangGraph 的上游大库；A 是自研五层架构、573 tests、已有安全/部署基线。把 B 并进 A 等于重写 A。
2. **论文贡献**：A 是"设计与实现"的自研系统；B 的二开更接近集成改进。A 的核心创新（原文级证据绑定、确定性引用校验、定向重生成）在 Plan 3 已有明确位置。
3. **维护成本**：10 周主线内不能同时演进两套运行时。B 冻结后只读，不修上游、不加功能。
4. **实验价值**：B 的失败数据正好构成 A 实验的动机与外部对照——"多 Agent 无原文级核验"的现状。

### 2.1 结合点只有三层

| 层 | B 提供 | 落到 A 的位置 |
|---|---|---|
| 需求层 | 4 条失败事实（§1.3） | Plan 3 验收增补（§6.2），Plan 2 的恢复能力设计依据 |
| 资产层 | 5 PDF + 3 问题 + manifest + 运行记录 schema | Plan 5 的 pilot 数据集与实验基建（§6.3） |
| 方法层 | 4 篇方法（下表） | Plan 3 / Plan 5 的实现参考 |

### 2.2 方法映射（B 调研的 5 篇论文怎么用）

| 方法 | 对 A 的用处 | 落点 | 优先级 |
|---|---|---|---|
| RARR：生成后证据归因与修订 | 引用校验失败后的"定向重写"策略参考 | Plan 3 Task 4（确定性引用校验 + 定向重生成） | P0（本就在计划内） |
| RAGAs：faithfulness / answer relevance / context relevance | 实验评价指标补充；LLM Judge 只作补充指标 | Plan 5 Task 6/7 | P1 |
| CRAG：检索质量评估与纠错 | Validator 前置"检索质量门槛"的增强 | Plan 3 Task 2 增强 | P1（时间富余再做） |
| Self-RAG：按需检索与反思 | Planner/Executor 检索触发策略的参考 | Plan 3 可选增强 | P2 |

边界：**不训练模型、不做 LoRA**（设计规格 §3.2 明确不实现）；方法只作为实现参考，不把首版范围撑大。

---

## 3. 正式实验设计（A 的 Plan 5，已冻结）

- 4 组：`baseline_a` 单 Agent 无 RAG / `baseline_b` 多 Agent 无原文级核验 / `baseline_c` 多 Agent 向量 RAG / `full` 多 Agent 混合检索+重排。
- 规模：15 课题 × 3 领域 × 4 组 × 2 次 = **120 runs**，resume-safe。
- 固定：模型 `gpt-6-luna`、温度 0.2、Prompt 版本、语料版本、随机种子；失败重跑限次并保留日志。
- **B 在论文中的角色**：pilot 对照（5 PDF × 3 问题，Q1–Q3 数据已全部完成）+ 动机证据。**不进入 120-run 矩阵**，控制成本与时间。

> 论文叙事建议：第 1 章写背景；第 2 章用 B 的现状说明"多 Agent 文献调研的引用可靠性问题"；第 5 章用 A 的四组消融 + B 的 pilot 对照证明证据核验机制有效。

---

## 4. 统一排期（2026-10-19 → 2027-03）

起点：2026-10-19（周一），对应设计规格 §13 十周路线的第 1 周（安全基线已在 Plan 1 完成，实际从第 2 周内容起跑）。

| 周次 | 日期 | 内容 | 退出条件（Gate） |
|---|---|---|---|
| W1 | 10/19–10/25 | B 收口归档（§6.1）+ A 基线复验 + Plan 2 worktree 与 Task 1 启动 | B 有 tag、runs 已提交；A `573 passed` 记录在案 |
| W2–W3 | 10/26–11/08 | **Plan 2**：Persistent Task Runtime（7 task） | **M2**：HTTP 创建→确认→执行→事件回放→重启恢复，固定 adapter E2E 通过 |
| W4–W5 | 11/09–11/22 | **Plan 3**：Evidence-Backed Literature Workflow（6 task） | **M3**：离线 10 篇语料生成带可回溯引用的综述，Markdown/DOCX 哈希记录 |
| W6 | 11/23–11/29 | **Plan 4**：前端工作台（8 task） | **M4**：不依赖 Swagger/终端完成全流程；刷新/重连不丢状态 |
| W7 | 11/30–12/06 | **Plan 5 Task 1–4**：进程级 E2E、恢复/隔离/降级、Compose smoke、CI 门禁 | **M5**：答辩候选版本，停止新增产品功能 |
| W8–W9 | 12/07–12/20 | **Plan 5 Task 5–7**：实验输入、runner/metrics、报告；跑 120-run 矩阵 + B pilot 对照 | **M6**：实验数据与图表齐全 |
| W10 | 12/21–12/27 | **Plan 5 Task 8**：部署/恢复/答辩 runbook，两次彩排 | **M7**：答辩冻结版本 |
| 12/28–03 | 2027-01–03 | 论文撰写与实验补充、论文素材整理、答辩准备；春节（2027-02-06）缓冲 | 论文定稿、答辩演练完成 |

说明：

- 设计规格的十周路线是"最短可行"排期，**W2–W4 是关键路径**（Plan 2/3）。Plan 4 若延期，按 §13.2 裁剪：多模型降级 → 在线 arXiv → 复杂质量评分 → 非必要动画；**用户隔离、检查点恢复、离线演示、引用追溯不得裁剪**。
- 12/27 之后仍有约 2 个月缓冲，用于真实实验补跑（成本/稳定性原因）与论文写作，不必把实验压缩在 W8–W9 内完成。
- 若 12 月中旬前 M3 仍未通过，启用 §7 的备用方案（不晚于 12/01 决策）。

---

## 5. 每周怎么跑（执行协议）

### 5.1 工作树与分支

- 主分支：`codex/literature-review`（当前工作树，唯一活跃开发树）。
- 每份计划开独立 worktree + 分支（`codex/plan2-task-runtime` 等），从 `codex/literature-review` 拉出；该计划 Exit Gate 通过后 `merge --no-ff` 回主分支，再开下一份。
- `C:\Users\Lenovo\.config\superpowers\worktrees\...\plan1-foundation-security` 与主工作树同为 `57cd5d3`，属 Plan 1 遗留；确认无未提交改动后删除（`git worktree remove`），避免两处同时跑测试/Compose 相互干扰。
- 不在 `main` 工作树实现（主计划全局规则）。

### 5.2 每个任务的固定动作

1. 按计划里的 red → green → refactor 步骤执行，一个任务一个 Conventional Commit（≤72 字符）。
2. 统一质量命令（Windows host）：

```powershell
Set-Location backend
.\.venv\Scripts\python.exe -m pytest tests -q --basetemp="$env:TEMP\pytest-dsh"
.\.venv\Scripts\python.exe -m black --check app tests
.\.venv\Scripts\python.exe -m ruff check app tests
.\.venv\Scripts\python.exe -m mypy app

Set-Location ..\frontend
npm run type-check
npm run build
```

> `--basetemp` 说明：`%TEMP%\pytest-of-Lenovo` 被历史进程写坏了权限（当前用户不可列/不可建），导致 26 个用到 tmp_path 的用例在 setup 阶段 `PermissionError`。加 `--basetemp` 全部通过（2026-10-17 实测 573 passed / 91%）。彻底修复需要管理员删除该目录；CI 与容器内不受影响。

3. 计划 Exit Gate 的验收命令必须逐条执行并记录真实输出；不满足不得开下一计划。
4. 进度只看两份计划复选框与 Exit Gate 记录；`checklist.md` 的 23 周旧口径在 Plan 5 Task 8 统一重写，在那之前不作为进度依据。

### 5.3 模型与密钥

- 正式实验模型固定 `gpt-6-luna`（provider=`openai` 兼容模式，`base_url=https://model.rhzy.ai/v1`）。
- 密钥只放各自仓库的 `.env`（gitignore），禁止写入代码、文档、聊天记录或提交历史。
- Plan 3 的真实模型验收前，先在 A 的 LLM 层做一次连通性验证（普通 chat completion 与流式各一次），失败则退回固定 adapter 并记录。

---

## 6. 立即行动（W1，按顺序）

### 6.1 B 收口归档（2026-10-17 已完成）

1. [x] Q3 补跑完成（269.5s / 128,417 tokens）；两次环境失败尝试也保留在 `thesis/runs/` 并在 README 标注无效。
2. [x] 证据入库：`thesis/runs/`、`thesis/environment.json`、server 日志已提交（`7a0cd9b6`）；`.env` 未跟踪（已校验）。
3. [x] tag `baseline-v1` 已创建并推送（记录 upstream `0957c301`、working `380230e4`、gpt-6-luna、5 PDF、3 问题）。
4. [x] `THESIS.md` 顶部已加 "Frozen baseline" 说明，并补三问结果表与引用可靠性发现。

### 6.2 A 的基线验收增补（Plan 3 开工前，0.5 天）

把 §1.3 的 4 条事实写进 Plan 3 的执行注释（可追加到 `2026-08-09-00-execution-notes.md` 第 5 节），并在实现时逐条验证：

- [ ] 本地资料模式**不发起任何联网请求**（B 的缺陷 3）；
- [ ] 每个正式引用可回溯到 `document + chunk + 物理页码`，无法回溯的标 `unresolved` 而不是输出外部链接（B 的缺陷 1）；
- [ ] 必引文献覆盖：计划要求的文献未覆盖时不得通过 Validator（B 的必引未引）；
- [ ] 无证据结论不得作为文献事实；Fact Check 不得"无修订直接接受"（B 的缺陷 2）；
- [ ] 任务中止/重启后可从检查点恢复，事件序号连续（B 的缺陷 4，对应 Plan 2 验收）。

### 6.3 pilot 数据集落盘（Plan 5 Task 5 开工前）

- 从 B 复制到 `backend/evaluation/datasets/pilot/`：5 篇 PDF、`questions.json`、`corpus_manifest.json`、`upstream.json`（记录上游 commit 与许可）、`observations.json` 模板。
- `upstream.json` 与 `THESIS.md` 作为"设计来源与许可说明"留在仓库，论文中明确：语料来自公开论文、系统 B 来自 GPT Researcher（Apache 2.0），A 的改进独立实现。

### 6.4 Plan 2 启动（W1 内）

- 创建 worktree/分支 `codex/plan2-task-runtime`，从 Plan 2 Task 1 开始（Runtime ORM 字段 + 类型化契约）。
- 第一周目标：Task 1–2 完成（持久化契约 + owner-scoped repository），不赶进度、不跳过红灯测试。

### 6.5 服务器开发环境（2026-10-17 已完成）

- 位置：`ylin@10.0.10.2:~/projects/multi-agent-assistant`（分支 `codex/literature-review`）
- Python：3.11.14 venv，`backend/.venv/bin/python -m pytest tests -q` → **573 passed / 90.62%**
- Compose：五容器全部 healthy；nginx 48080 返回 200（Vue 构建）与
  `{"status":"ready","checks":{"database":"ok","redis":"ok","chroma":"ok"}}`
- 详细命令、端口表与 Windows ↔ 服务器同步流程见
  [development-environment.md](../runbooks/development-environment.md)

---

## 7. 风险与裁剪顺序

| 风险 | 概率 | 影响 | 应对 |
|---|---|---|---|
| Plan 2/3 关键路径延期 | 中 | 高 | 每周检查 Gate；12/01 前未过 M3 则启用备用方案；优先砍 Plan 4 高级交互 |
| 120-run 实验成本 | 中 | 中 | 按 B 基线 ~0.13M tokens/run 粗估总量约 1.6e7；设每日额度告警、失败重跑限次、先跑 1 组试点确认耗时 |
| 真实模型（Luna 代理）不稳定/接口差异 | 中 | 中 | Plan 3 前先做连通性验证；CI 用固定 adapter；真实模型只在验收与实验使用 |
| 两仓库/两套密钥管理混乱 | 低 | 中 | B 打 tag 后冻结只读；A 只从 B 取数据不取代码；密钥只进 `.env` |
| Windows host 测试环境异常（temp 权限等） | 中 | 低 | 统一 `--basetemp`；Plan 5 的 CI/Compose 在干净环境验证 |
| 论文写作启动过晚 | 中 | 高 | 12 月起同步写"系统实现"章；实验图表模板在 Plan 5 Task 7 提前产出 |

**备用方案（12/01 决策）**：若 M3 明显不可达，B（可运行原型）+ 最小证据核验补丁可作为"可演示系统"兜底；但论文主线仍是 A，B 只作为对照系统出现。是否启用由你决定。

---

## 8. 已确认决策（2026-10-17）

1. **路线**：A 主 B 辅。B 的优点（基线数据、实验协议、5 篇方法调研）集成进 A，不合并代码。
2. **B 的 Q3**：补跑后归档并打 tag。
3. **正式实验模型**：`gpt-6-luna`（rhzy.ai 代理，OpenAI 兼容接口）。
4. **题面边界**：文献综述单场景（设计规格 §3.1/3.2）；旧 `dev-plan-v2.md` 的代码生成/电商场景只作论文扩展说明。
5. **开发环境**：主开发环境在服务器 `ylin@10.0.10.2:~/projects/multi-agent-assistant`（见 §6.5）。

## 9. 执行状态

- [x] 4 项决策确认（2026-10-17）
- [x] 服务器仓库 + Python 3.11 venv + 573 tests 全绿（2026-10-17）
- [x] 服务器 Compose build / up / readiness 全通过（五容器 healthy，2026-10-17）
- [x] B 的 Q3 补跑、归档、tag `baseline-v1` 并推送（2026-10-17）
- [x] 备份：`codex/literature-review` 分支已推送 GitHub（HTTPS `backup` remote）
- [x] Plan 2 Task 1：运行时 ORM 字段 + 共享契约 + 可回滚迁移
      （582 passed；服务器 PostgreSQL 上 `upgrade → downgrade -1 → upgrade` round-trip 通过）
- [ ] Plan 2 Task 2：owner-scoped 任务/事件仓库（下一步）

