# AGENTS.md — 项目上下文与开发约定

> 本文件是**给 AI 编码助手看的交接文档**。新会话开始时请先读完本文件再动手。
> 最后更新：2026-10-19

---

## 1. 这是什么项目

**多智能体协作自动化任务助手** —— FastAPI + Vue3 的五层架构系统，当前唯一正式业务场景是**文献综述**（证据约束 + 引用可追溯 + Markdown/DOCX 产出）。

- **双重目标**：① 毕业设计交付物（已达成）；② **正在产品化**，目标是"生成即合规、引用可核验的学术综述工作台"
- **仓库位置（本地）**：`E:\Multi-agent collaborative automation task assistant`
- **仓库位置（服务器）**：`/home/ylin/projects/multi-agent-assistant`
- **产品化路线图**：[docs/product/2026-10-18-demo-to-product-roadmap.md](docs/product/2026-10-18-demo-to-product-roadmap.md)（含竞品分析、功能蓝图、优先级）

---

## 2. 当前状态快照（2026-10-19）

| 项 | 值 |
| --- | --- |
| 分支 | `main`（工作分支 `codex/plan3-literature-workflow` 与 main 同点） |
| HEAD | … → P1-D 缺口补齐 `63b1d95` → Phase 2 规划 `8f1b60f` → **PRISMA Task 1 `67c6c30`** |
| 冻结 tag | `defense-v1` |
| Plan 状态 | Plan 2 / 3 / 4 / 5 **全部 Exit Gate PASSED**；**Phase 1 五项全部端到端可用且遗留清零**；**Phase 2 进行中**（PRISMA 子计划 Task 1/7 已完成） |
| 产品化进度 | **Issue #2 / #3 / #4 / #5 / #6 / #7 全部关闭且遗留项清零**；配置优先级缺陷已修复；Phase 1 五项端到端可用（后端 + HTTP 端点 + 前端页面 + 限流全覆盖 + 配额真扣减）；**Phase 2 PRISMA 数据层已就位**（search_log / screening_record / screening_decision，迁移 `b8e5f1c3d9a2`） |
| 后端测试 | **2075 passed**, 8 skipped；覆盖率 **90.00%**；`mypy` 0 错误（164 文件） |
| 前端测试 | Vitest **156 passed**；Playwright E2E **20 passed**（Phase 1 三页 × 桌面 + 移动双视口） |
| CI | **6/6 作业全绿**（2026-10-19 修复；此前自 08-03 起连续失败） |
| 服务器 | 已同步，五容器 healthy，`curl http://127.0.0.1:48080/api/v1/health/ready` → `{"status":"ready"}` |

**演示账号**：`demo` / `demo12345`（知识库已有 10 篇冻结语料 / 2834 分块）

---

## 3. 开发节奏（每次"继续" = 一个任务，严格按此循环）

1. **读计划**：`docs/superpowers/plans/` 下的对应计划文件（Task 级）
2. **先写失败测试**（红）—— 这是硬要求，不要先写实现
3. **实现**（绿）
4. **本地全量门禁**（缺一不可）：
   ```powershell
   cd backend
   .\.venv\Scripts\python.exe -m black app tests evaluation
   .\.venv\Scripts\python.exe -m ruff check --fix app tests evaluation
   .\.venv\Scripts\python.exe -m mypy app evaluation
   .\.venv\Scripts\python.exe -m pytest tests -q --cov=app --cov-fail-under=80 --basetemp="$env:TEMP\pytest-dsh"
   ```
5. **一次 Conventional Commit**（`feat:` / `fix:` / `docs:` / `test:` / `chore:`）
6. **同步到服务器**（见 §4.3）
7. **服务器验证**（真实依赖、真实模型）
8. **更新文档**：`docs/superpowers/plans/2026-08-09-00-execution-notes.md`（记录实测证据与偏差）+ 路线图勾选
9. **推送备份**：`git push backup main`
10. **中文简报**，结尾写"下一任务"

> 报告与文档用中文；代码注释/文档字符串用中文；提交信息用 Conventional Commits。

---

## 4. 环境与常用命令

### 4.1 本地（Windows）

```powershell
cd 'E:\Multi-agent collaborative automation task assistant\backend'
# 全量测试（必须指定 basetemp，否则会撞权限问题）
.\.venv\Scripts\python.exe -m pytest tests -q --basetemp="$env:TEMP\pytest-dsh"
```

- 前端：`cd frontend && pnpm test` / `pnpm build`
- 本机**无法访问** `model.rhzy.ai`（直连 reset / 代理 TLS 失败）→ **真实模型实验只能在服务器跑**

### 4.2 服务器（ylin@10.0.10.2，Ubuntu 24.04）

```bash
cd ~/projects/multi-agent-assistant
docker compose ps
curl -s http://127.0.0.1:48080/api/v1/health/ready
```

- venv 由 **uv** 创建，**没有 pip** → 装包用 `uv pip install --python .venv/bin/python <pkg>`
- 测试环境变量：
  ```bash
  export APP_ENV=test
  export DATABASE__URL="postgresql+asyncpg://maa:maa_secret_pwd@localhost:35433/multi_agent_assistant"
  export JWT_SECRET_KEY="ci-test-secret-not-for-production"
  ```
- 宿主机端口（由未入库的 `docker-compose.override.yml` 指定）：postgres `35433` / redis `36379` / chroma `38002` / nginx `48080`
- ⚠️ **服务器 CPU 单核吞吐约为本机 1/28**（2026-10-19 实测：同一非文献子集本地 14.89s vs 服务器 418.53s；主机 192 核、load ~44，多项目共存）。**不要在服务器跑全量 `pytest tests` 等结果**（约需 3 小时），服务器验证用「定向用例 + 容器内脚本」；全量门禁以本地为准

### 4.3 本地 → 服务器同步（标准流程）

```powershell
# 本地
$b = Join-Path $env:TEMP 'maa.bundle'
git bundle create $b --all
scp $b ylin@10.0.10.2:~/maa.bundle
```
```bash
# 服务器
cd ~/projects/multi-agent-assistant
git fetch ~/maa.bundle "+refs/heads/*:refs/remotes/win/*"
git merge --ff-only win/main
docker compose build backend && docker compose up -d backend
```

### 4.4 冒烟测试（隔离栈，不碰生产数据）

```bash
./scripts/smoke.sh     # 期望结尾 SMOKE OK；失败时 KEEP=1 ./scripts/smoke.sh 保留现场
```

---

## 5. 铁律与踩过的坑（**必须遵守**）

| # | 规则 | 原因 |
| --- | --- | --- |
| 1 | **绝不用 PowerShell 做 UTF-8 文本往返**（`Get-Content`/`Set-Content`/`-replace`） | 会破坏中文（mojibake + 换行塌缩），本会话踩过 4 次。改文件用 `edit`/`write` 工具，或写 Python 脚本执行 |
| 2 | 本机访问服务器服务要用 **`10.0.10.2`**，不是 `127.0.0.1` | 服务跑在服务器上；`127.0.0.1` 指向你自己的电脑 → `ERR_CONNECTION_REFUSED` |
| 3 | 密钥**只进 `.env`**（gitignore，600 权限） | 绝不写进代码、文档、提交、Issue |
| 4 | 本机 `NO_PROXY` 含非法 `[::1]` → httpx 报 `InvalidURL: Invalid port ':1]'` | 用 `scripts/run_without_proxy.py` 绕过 |
| 5 | 模型端点**只支持 `gpt-6-luna`** | `gpt-4o`/`gpt-4o-mini` 返回 404；已配 **DeepSeek 保底**（`config/default.yaml` 的 `fallback_chain` 首位） |
| 6 | 容器内配置文件在 **`/app/config/`** | `PROJECT_ROOT` 已做多级探测（`MAA_PROJECT_ROOT` 环境变量 → app 包位置 → 文件位置 → cwd）；曾因 `parents[3]` 越界导致 `fallback_chain` 为空 |
| 7 | 服务器上有**其他项目的容器**（portainer / qdrant / rsshub 等） | 不要动；曾出现的 200G 磁盘占用属于别的项目 |
| 8 | 冒烟栈用独立 project `maa-test` + `ports: !override` | 防止 `down --volumes` 误删生产数据卷 |
| 9 | 改 `docker-compose.yml` 的 LLM 环境变量必须用**嵌套名** `LLM__XXX` | pydantic-settings 用 `__` 映射；只写扁平名不生效 |
| 10 | 新增 LLM provider 后要检查 `factory.py` 的 provider 循环元组 | 曾因漏加 `deepseek` 导致 base_url 取不到 |

---

## 6. 待办事项

### 6.1 产品化开发（按优先级）

**Phase 1 · 打地基（2026-10-20 已交付五项）**：[计划](docs/superpowers/plans/2026-10-20-00-phase1-foundation-plan.md)

| 项 | 状态 | 说明 |
| --- | --- | --- |
| P1-A Word 模板导入 + 一键切换 | ✅ 已交付 `d44282a` + 端点 `fe56a67` + **前端 `08b7378`/`049f1a8`/`2828a5f`** | 领域模块 + HTTP 端点 + `user_template` 表（迁移 `a7d4e2f9c1b8`）+ **`/templates` 管理页**（占位符展示/上传/渲染下载/删除）；上传准入拒 `.docm`/宏/OLE/zip 炸弹。**遗留**：无 |
| P1-B 检索源扩展 | ✅ 已交付 `d44282a` | OpenAlex + S2 + PubMed + 多源聚合（三级去重、单源失败隔离、各源节流）。**遗留**：S2 需注入 `SEMANTIC_SCHOLAR_API_KEY`（公共档持续 429） |
| P1-C 账号体系 | ✅ 已交付 `d44282a` + 修复 `112c398` + **前端 `f7323ff`** | 邮箱验证 + 密码重置（只存哈希/单次使用/恒定响应）；未配 SMTP 不阻塞注册 + **`/verify-email`、`/password-reset`、`/account/verify` 三页** + 登录页「忘记密码」入口。**遗留**：无 |
| P1-D 限流 + 配额 | ✅ 已交付 `d44282a` + **缺口补齐 `63b1d95`** | 中间件 + Redis/内存实现 + fail-open + 熔断；429 带 Retry-After；**限流覆盖全部 19 条写端点**（新增 task_control/template_write/template_render）；**配额真扣减**（提交时预扣 100k、完成时按实际用量退还差额，`refund()` 原子且不扣到负数）。**遗留**：无 |
| P1-E RIS/BibTeX 导入 | ✅ 已交付 `d44282a` + 端点 `fe56a67` + **前端 `08b7378`** | round-trip 与导出严格一致 + `POST /references/import` + **`/references/import` 预览页**。**遗留**：无 |

**Issue 清单**（全部已关闭）：
| Issue | 标题 | 优先级 | 说明 |
| --- | --- | --- | --- |
| [#3](https://github.com/sakura-del/Multi-agent-collaborative-automation-task-assistant/issues/3) | ✅ 引用真实性校验（CrossRef/OpenAlex） | **P0** | **已全部交付**：后端 `42b5e54`（四态核验 + 导出统计 + 拦截开关）、**前端展示 `c270348`**（`ReferenceVerificationPanel` 四态计数/拦截状态/逐条徽标 + 明确空态；任务级 `verify_references` 开关，默认关闭以保 R6 离线零外呼）。计划：[后端](docs/superpowers/plans/2026-10-18-03-reference-authenticity-plan.md) / [前端](docs/superpowers/plans/2026-10-19-03b-frontend-verification-plan.md)。**遗留项已全部闭环**（`contact_email` 可经环境变量注入，见本表末行配置优先级修复） |
| [#4](https://github.com/sakura-del/Multi-agent-collaborative-automation-task-assistant/issues/4) | ✅ CSL 引用引擎 + GB/T 7714—2025 默认 | P0 | **全部完成（含遗留项）**：M1 `ce86c57`（渲染核心 + 自研 2025 顺序编码制 + 准入 + golden）；M2 `f0123d4`（`citation_style` 表 + 迁移 + 上传/列表/删除 API + `.bib`/`.ris` 下载 + 前端样式选择器与管理页）；著者-出版年制 `deefaaa`（第 6 个预置样式，国标双制式完整）；剩余三项 `a986785`（`CslItem.medium` → `[J/MM]`、**拼音排序**（新增 `pypinyin==0.55.0`，MIT）、全大写姓名归一）。计划见 [M1](docs/superpowers/plans/2026-10-19-04-csl-engine-plan.md) / [M2](docs/superpowers/plans/2026-10-19-04b-csl-m2-plan.md)。**遗留：无** |
| [#5](https://github.com/sakura-del/Multi-agent-collaborative-automation-task-assistant/issues/5) | ✅ CSL-M 双语策略架构决策（ADR） | P1 | **已完成**（2026-10-19）：决策 = **Option C**（标准 CSL 1.0.2 + 应用层逐条 locale 切换），否决 Option B（citeproc-js 为 CPAL-1.0 OR AGPL-1.0，CPAL §15 把网络部署视为分发；且双语样式非 1.0.2 合法却被 citeproc-js **静默**接受）。证据：[ADR 0001](docs/adr/0001-csl-bilingual-strategy.md) + 3 份 research 文档 |
| [#6](https://github.com/sakura-del/Multi-agent-collaborative-automation-task-assistant/issues/6) | ✅ 校验转人工（约 40%） | **P0** | **已修复并关闭**（2026-10-19，`94de098` + `f1c9f78`）：两处成因——① `validate`/`review` 的**机器汇总内容**被施加作者级检查（`duplicate_paragraph_ratio` 跨节点复述必然超阈值，且重试无法修复）→ 引入内容角色 `authored`/`aggregated`；② **语义判定失败被当作不可重试** → `waiting_human`（一次有噪声的 LLM 判断即致命）→ 并入 `retryable`（完整性失败仍不可重试）。验证：原始失败载荷回放 + 连续 **5/5 completed**。**遗留**：`ReviewerAgent` 正文是各节点 `content` 拼接，最终 Markdown 存在跨节点重复段落，建议单列任务（复盘以 write 节点为主干） |
| [#7](https://github.com/sakura-del/Multi-agent-collaborative-automation-task-assistant/issues/7) | ✅ 复盘正文冗余 | **P1** | **已修复并关闭**（2026-10-19，`cdbcd8d`）：根因是复盘对**每个**节点无条件 `body_parts.append(content)` 后拼接，而 validate/review 的内容本身又是上游输出的拼接（拼接的拼接）。修法：`ExecutionResult` 增 `node_type`（工作流标注、随执行结果回流）+ `content_role`；只有 `extract`/`write`（作者正文）进正文，`search`/`validate`/`review` 不进；段落按首次出现去重（只去完全相同，不做模糊合并）；剔除作者节点自带的 `## 参考文献`/`## 局限性`/`## 引用核验`（由复盘统一生成）。**实测：正文重复段落 45.6%/47.1% → 0%；`## 参考文献` 3 次 → 1 次**。**遗留**：extract 与 write 仍各输出一遍条目清单（内容层合理重复，非逐字），如需进一步压缩应让 write 不复述清单 |
| [#2](https://github.com/sakura-del/Multi-agent-collaborative-automation-task-assistant/issues/2) | ✅ 规划生成后页面不自动显示 | P2 | **已修复并关闭**（2026-10-19，`a715398`）：根因是 `tasks.ts::applyEvent` 只在**状态变化**时重拉快照，而生产竞态是「快照 status 已是 `waiting_approval` 但 `plan` 仍为 `null`」→ `plan_ready` 事件因状态等值被跳过 → 只能手动 F5。修法：抽出 `needsSnapshotRefresh()`，三条判据任一成立即重拉（事件类型为 `plan_ready`/`task_completed`、状态变化、**快照 `plan` 为空而状态应展示规划**）。验证：服务器真实浏览器 21.1s 自动出现、`navigation` 条目数不变（无整页重载）。**遗留**：无 |
| — | ✅ 配置优先级缺陷（YAML 无法被 `.env`/环境变量覆盖） | **P1** | **已修复**（2026-10-19，`f943a44`）：根因 `Settings(**yaml_defaults)` 把 yaml 当 **init 参数**，而 pydantic-settings 中 init 参数优先级最高 → yaml 压过环境变量。修法：新增 `_YamlDefaultsSource` 经 `settings_customise_sources` 插到**最低优先级**（`init → env → dotenv → yaml → file_secrets`）。实测容器内 `REFERENCE_VERIFICATION__CONTACT_EMAIL` / `MAX_CONCURRENCY` 均能覆盖 yaml，且 yaml 基线（`fallback_chain`）仍生效。**Issue #3 的 `contact_email` 遗留项就此闭环** |

### 6.2 论文相关（非开发）

- 跑真实 120-run 实验矩阵：`cd backend && python -m evaluation.cli run --profiles all --repetitions 2` → `report`
- 论文撰写与答辩彩排（两次，记录表见 `docs/runbooks/defense-demo.md` 附 C）

---

## 7. 关键文件地图

```
backend/app/orchestration/          # 编排核心
  contracts.py                      # 全部 Pydantic 契约（extra="forbid"）
  runtime.py                        # RuntimeContainer（LLM 注入、FallbackChatClient）
  base_agent.py                     # Agent 模板方法 + 并发状态收敛（_settle）
  services/task_execution.py        # 任务生命周期、规划、瞬态重试
  services/literature_source.py     # 文献来源（目前只有 ArxivLiteratureSource）
  workflows/literature_review.py    # 文献综述工作流
backend/app/knowledge/services/
  citation_validator.py             # ★ 引用校验（#3 要在这里加 check）
  chunker.py                        # 分块策略（recursive/semantic/sliding_window）
backend/app/tools/literature/
  arxiv.py                          # ★ 适配器模式参考（#3 复用）
  exporter.py                       # ★ Markdown/DOCX 导出（#4 要改这里）
backend/app/llm/
  factory.py                        # provider 注册 + 降级链 + FallbackChatClient
  base.py                           # _is_retryable（5xx 判定）
backend/evaluation/                 # 实验框架（120-run 矩阵、图表）
docs/product/                       # ★ 产品化路线图 + 竞品调研
docs/adr/                           # ★ 架构决策记录（0001 = CSL 双语策略）
docs/runbooks/                      # 部署/恢复/答辩/复现 手册
docs/superpowers/plans/             # 计划 + 执行记录（execution-notes）
```

---

## 8. 沟通约定

- 面向用户的报告用**中文**，简洁、给结论、给证据
- 每轮结束给出**明确的下一步**
- 发现真实缺陷时：**先复现 → 定位根因 → 写失败测试 → 修复 → 补回归测试**，并把根因写进 `execution-notes`
- 不确定的信息明确标注"未确认"，**不要编造**
