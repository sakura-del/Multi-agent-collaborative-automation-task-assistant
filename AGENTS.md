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
| HEAD | `ed5f2cc`（Issue #3 功能提交 `42b5e54`） |
| 冻结 tag | `defense-v1` |
| Plan 状态 | Plan 2 / 3 / 4 / 5 **全部 Exit Gate PASSED** |
| 产品化进度 | **Issue #3 引用真实性校验已交付**（CrossRef/OpenAlex 四态核验 + 导出统计 + 拦截开关） |
| 后端测试 | **1159 passed**, 8 skipped；覆盖率 **88.83%**；`mypy` 0 错误（131 文件） |
| 前端测试 | Vitest **69 passed**；Playwright E2E **4 passed**（桌面 + 移动双视口） |
| 服务器 | 已同步至 `ed5f2cc`，五容器 healthy，`curl http://127.0.0.1:48080/api/v1/health/ready` → `{"status":"ready"}` |

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

| Issue | 标题 | 优先级 | 说明 |
| --- | --- | --- | --- |
| [#3](https://github.com/sakura-del/Multi-agent-collaborative-automation-task-assistant/issues/3) | ✅ 引用真实性校验（CrossRef/OpenAlex） | **P0** | **已交付**（`42b5e54`，2026-10-19）：四态核验 + 导出统计 + 拦截开关；计划与实测见 [2026-10-18-03-reference-authenticity-plan.md](docs/superpowers/plans/2026-10-18-03-reference-authenticity-plan.md)。**遗留**：前端展示核验结果；`contact_email` 受配置优先级缺陷限制只能改 yaml |
| [#4](https://github.com/sakura-del/Multi-agent-collaborative-automation-task-assistant/issues/4) | CSL 引用引擎 + GB/T 7714—2025 默认 | P0 | 双语部分**被 #5 阻塞**。⚠️ `citeproc-py` 维护停滞不要用；GB/T 7714—2025 的 CSL 需自行编写（官方仓库未收录）。**建议下一个做** |
| [#5](https://github.com/sakura-del/Multi-agent-collaborative-automation-task-assistant/issues/5) | CSL-M 双语策略架构决策（写 ADR） | P1 | **0.5 天，但决定 #4 的技术栈**（是否引入 Node 跑 citeproc-js）。⚠️ citeproc-js 的 SPDX 许可未确认 |
| [#2](https://github.com/sakura-del/Multi-agent-collaborative-automation-task-assistant/issues/2) | 规划生成后页面不自动显示，需刷新 | P2 | **用户要求暂缓**；演示绕过已写入 `docs/runbooks/defense-demo.md` 第 5 步 |
| — | 配置优先级缺陷（YAML 无法被 `.env`/环境变量覆盖） | P1 | `get_settings()` 用 `Settings(**yaml_defaults)`，init 参数优先级最高。已阻塞 Issue #3 的 `contact_email` 注入（只能改 yaml）；建议改 `settings_customise_sources` 并补覆盖顺序测试 |

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
docs/runbooks/                      # 部署/恢复/答辩/复现 手册
docs/superpowers/plans/             # 计划 + 执行记录（execution-notes）
```

---

## 8. 沟通约定

- 面向用户的报告用**中文**，简洁、给结论、给证据
- 每轮结束给出**明确的下一步**
- 发现真实缺陷时：**先复现 → 定位根因 → 写失败测试 → 修复 → 补回归测试**，并把根因写进 `execution-notes`
- 不确定的信息明确标注"未确认"，**不要编造**
