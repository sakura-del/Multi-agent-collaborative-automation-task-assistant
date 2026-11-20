# AGENTS.md — 项目上下文与开发约定

> 本文件是**给 AI 编码助手看的交接文档**。新会话开始时请先读完本文件再动手。
> 最后更新：2026-11-11（上线后：M3 嵌入 + Pages Git 集成 + 安全排查与 #9 修复）
>
> **新会话请先读** → [docs/runbooks/2026-11-17-handoff.md](docs/runbooks/2026-11-17-handoff.md)
> （当前待办 = 仓库转 public 前的安全扫描清单；本轮 = 模型容量预设 + 显式选模型，已全绿）。本文件查约定与历史坑，handoff 查「现在该干什么」。
> 安全排查结论见 [docs/security/2026-11-11-security-audit.md](docs/security/2026-11-11-security-audit.md)。
> 历史基线见 [2026-10-28-handoff.md](docs/runbooks/2026-10-28-handoff.md)。

---

## 1. 这是什么项目

**多智能体协作自动化任务助手** —— FastAPI + Vue3 的五层架构系统，当前唯一正式业务场景是**文献综述**（证据约束 + 引用可追溯 + Markdown/DOCX 产出）。

- **双重目标**：① 毕业设计交付物（已达成）；② **正在产品化**，目标是"生成即合规、引用可核验的学术综述工作台"
- **仓库位置（本地）**：`E:\Multi-agent collaborative automation task assistant`
- **仓库位置（服务器）**：`/home/ylin/projects/multi-agent-assistant`
- **产品化路线图**：[docs/product/2026-10-18-demo-to-product-roadmap.md](docs/product/2026-10-18-demo-to-product-roadmap.md)（含竞品分析、功能蓝图、优先级）

---

## 2. 状态快照（2026-11-11 上线后；详见 2026-11-11 handoff）

| 项 | 值 |
| --- | --- |
| 分支 | `main` |
| HEAD | **`5a779cc`**（注册 ALTCHA 人机验证）（CI/CD 重构 + 冒烟修复，自动部署已验证）（`feat(ops): 站内自检 + 外部探测的双层监控告警` + 格式修复）← `dfd6b0f`（`chore(ops): SSH 加固 + 清密钥备份 + 系统包 + 源站只允许 Cloudflare 回源`）← `54ce78a`（`fix(security): refresh 令牌轮换后旧令牌立即失效`）→ `105b383`（口令轮换）→ `5421a98`（SSTI 修复）→ `090e29a`（M3 上线）、Pages Git 集成、安全排查与 `security.txt` |
| 冻结 tag | `defense-v1` |
| Plan 状态 | Plan 2 / 3 / 4 / 5 **全部 Exit Gate PASSED**；**Phase 1 五项全部端到端可用且遗留清零**；**Phase 2 五项全部交付完成**（PRISMA ✅ + 可观测性 ✅ + 团队工作区 ✅ + 开放 API ✅ + 计费套餐 ✅） |
| 产品化进度 | **Issue #2~#7 全部关闭且遗留清零**；**P1-D 配额遗留已修复**；**`max_results` 已夹紧**；**PRISMA 完成**；**可观测性完成**（8 指标 + `/metrics` + 看板，**看板数字已全部为真**）；**团队工作区完成**；**开放 API 完成**；**计费套餐完成**（四档 + 四条功能门禁 + 手工开通 + **套餐页已重设计**）；**引用样式页已重设计**（**真实渲染预览** + 删除二次确认）；**Word 模板页已修**（**不再把示例填充谎称成稿** + 删除二次确认）；**前端已迁移到 Tailwind v4 + shadcn-vue**；**引用库 + 证据闭环完成**；**review 长度上限缺陷已修**（`max_length` 不再施加到机器汇总） |
| 数据库迁移 head | **`a7d3e9b6c1f4`**（`revoked_refresh_token`；前一个 `c4f2a8d7e9b1` = `knowledge_vectors_bge` 影子表） |
| 后端测试 | **3157 passed**, 21 skipped；覆盖率 **90%**；`mypy` 0 错误（219 文件）；`.csl` 4 红已由 `.gitattributes`（`*.csl -text`）消除 |
| 前端测试 | Vitest **458 passed**（+6 skipped）；**Playwright E2E 44 场景**（chromium + mobile-chrome 双视口） |
| 发布方式 | **前端：`git push backup main` 后 Cloudflare Pages 自动构建部署**（Git 集成，无需本机 Node）；**后端：bundle + `deploy/ecs` 目录 `docker compose build backend`** |
| CI | ⚠️ **2026-10-28 起 CI 因 GitHub Actions 账单额度停摆**（报 spending limit），作业 0~2s 就 X，**不是代码问题**。⚠️ **GitHub 直连时通时不通**：不通时走代理（`git -c http.proxy=http://127.0.0.1:7894 ...`，`gh` 需 `$env:HTTPS_PROXY`） |
| 服务器 | `root@47.253.81.6`，HEAD **`9fceda6`**（CD 自动部署），backend 镜像 `c476209d879c`（1.78 GB，已无 chromadb）；80/443 仅接受 Cloudflare 回源（`DOCKER-USER`，第三方探测 443=0/4）；UFW active；SSH 3 把密钥、X11 关、MaxAuthTries 3；监控两层已上线（站内 2 分钟 / 站外 5 分钟），**webhook 已配置并验证**（`/etc/veripaper/notify.env` + 仓库 secret `WECOM_WEBHOOK_URL`，2026-11-13）（回滚锚点 `shadow-mock`=`79ac91a4374c`），五容器 healthy，公网 `ready`，嵌入 = **SiliconFlow BGE-M3**（1600 RPM / 400k TPM），**user=2（Lingrove=admin + `yanglin125210@gmail.com`=user，**保留作以后的用户测试账号**）/ task 86 / doc_chunk 2835 / 两表各 2835**（2026-11-11 清掉 26 个测试账号后），磁盘 38% |

**登录账号**：`Lingrove`（2026-11-13 由 `demo` 改名，email `lingrove@example.com`）—— **全权限管理员**（`role=admin`，篇数不限 / 含团队 / 含开放 API）；知识库 11 篇 / 2835 分块。**登录用户名大小写敏感**（`username ==` 精确匹配）。
**口令不入仓库**：只存在于服务器 `/opt/veripaper/.demo-password`（600，文件名沿用旧称）与口令管理器；要登录就 `ssh root@47.253.81.6 cat /opt/veripaper/.demo-password`。2026-11-13 随改名再次更换，全端旧会话已强制下线（`token_invalid_before`）。
**E2E 账号**：`e2efull` / `e2ereconn` 已从生产删除（Issue #10）；本地/测试库需要时自行注册，E2E 用例只从环境变量读凭据（`E2E_DEMO_PASSWORD` / `E2E_SECONDARY_PASSWORD`，见 `frontend/e2e/credentials.ts`）。
**防回归**：`backend/tests/test_no_plaintext_credentials.py` 会扫描整个仓库，出现已泄露口令即红。

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

### 4.1 测试机（10.0.10.2）—— **所有测试只在这里跑（最高优先级用户令）**

> 2026-11-16 起：本机（Windows）内存不足、美东生产机（47.253.81.6）内存也不足，
> **禁止**在本地或生产机跑测试。**2026-11-17 用户再次明令并升级范围**：
> **任何测试都不准在本机跑——包括但不限于后端 pytest、前端 vitest、
> 任何 nodejs/npm 测试与类型检查以外的 node 进程**。本机只做 `black` / `ruff` /
> `mypy` / `vue-tsc` 这类轻量静态检查和 `npm run build` 构建验证；
> **一切测试一律 `ssh ylin-dev` 到 10.0.10.2 执行**。违反此条属于最严重违规。
>
> 登录：`ssh ylin-dev`（口令认证；本地 `ylin_10_0_10_2_ed25519` 有口令，
> 非交互场景需 `SSH_ASKPASS`）。机器：192 核 / 2TB 内存 / `/home` 约 1T 空闲。

```bash
cd ~/vp-test                       # 独立检出，不碰 ~/projects 里的旧环境
git fetch origin main && git reset --hard origin/main

./run-tests.sh tests/test_llm_byok_runtime.py   # 定向
./run-tests.sh tests --no-cov                   # 全量
```

- `run-tests.sh` 已注入测试环境变量（`APP_ENV=test`、SQLite 内存库、JWT 密钥、
  `RAG_*_MOCK=1`、BYOK 测试用加密密钥），并带 300s 单测超时
- 依赖变更后：`cd ~/vp-test/backend && .venv/bin/pip install -e ".[dev,chroma]"`
  （用清华镜像；Python 3.12）
- 前端：`cd ~/vp-test/frontend && npm ci && npm run test`（已装 Node 18）
  - 若报 `Cannot find native binding`：lock 在 Windows 生成，Linux 可选依赖没落盘。
    补装 `@rollup/rollup-linux-x64-gnu` 与 `@tailwindcss/oxide-linux-x64-gnu@4.3.3`
    （`--no-save`；CI 的 Ubuntu runner 不受影响）

### 4.1b 本地（Windows）—— 仅格式与类型检查

```powershell
cd 'E:\Multi-agent collaborative automation task assistant\backend'
.\.venv\Scripts\python.exe -m black --check app tests
.\.venv\Scripts\python.exe -m ruff check app tests
.\.venv\Scripts\python.exe -m mypy app
```

- 前端（**只用 npm**，见 §6.5.6）：`cd frontend && npm run build`
- 本机**无法访问** `model.rhzy.ai`（直连 reset / 代理 TLS 失败）→ **真实模型实验只能在服务器跑**

### 4.2 生产服务器（root@47.253.81.6，Ubuntu 24.04）

> ⚠️ 旧服务器 `ylin@10.0.10.2` 已停用（后端 stop）。**Supabase 旧库已于 2026-11-12 下线**，
> 整库回滚到 Supabase 的路径已关闭，只能靠 `/opt/veripaper/backup/` 下的 dump 重建。
> 现在**所有生产操作都在 `47.253.81.6` / `/opt/veripaper`**。

```bash
ssh root@47.253.81.6            # 私钥 C:\Users\Lenovo\.ssh\id_ed25519_hermes
cd /opt/veripaper
cd deploy/ecs && docker compose ps          # ← 生产编排文件在这个目录
curl -s https://api.veripaper.me/api/v1/health/ready
```

- 服务器写 `.sh` 再 `bash` 执行（PowerShell 引号/glob 会被远端拆坏）
- 长任务用 `setsid nohup ... > /tmp/x.log 2>&1 &` 脱离 SSH，再短命令轮询
- ⚠️ 不要再在服务器跑全量 `pytest tests`；全量门禁以本地为准

### 4.3 本地 → 生产服务器同步（2026-11-11 起）

```powershell
# 本地：打包并上传（服务器 origin 指向 /root/vp.bundle）
$b = Join-Path $env:TEMP 'vp.bundle'
git bundle create $b --all
scp -i 'C:\Users\Lenovo\.ssh\id_ed25519_hermes' $b root@47.253.81.6:/root/vp.bundle
```
```bash
# 服务器（写成 deploy.sh 再 bash 执行）
cd /opt/veripaper
git fetch origin '+refs/heads/*:refs/remotes/origin/*'
git merge --ff-only origin/main
cd deploy/ecs                    # ← 必须在这个目录，否则镜像名会变成 multi-agent-assistant-backend
docker compose build backend
docker compose up -d backend
```

前端**不需要**这一步：`git push backup main` 后 Cloudflare Pages 自动构建部署。

### 4.4 冒烟测试（隔离栈，不碰生产数据）

```bash
./scripts/smoke.sh     # 期望结尾 SMOKE OK；失败时 KEEP=1 ./scripts/smoke.sh 保留现场
```

---

## 5. 铁律与踩过的坑（**必须遵守**）

| # | 规则 | 原因 |
| --- | --- | --- |
| 0 | **一切测试只在 `10.0.10.2` 跑（`ssh ylin-dev`）；本机（Windows）不准运行任何测试进程——pytest、vitest、任何 nodejs/npm 测试都不行**。本机只允许 black/ruff/mypy/vue-tsc 与 `npm run build` | 2026-11-17 用户明令（此前 2026-11-16 已针对后端下过令，本条扩展到全部测试）。本机内存不足；**再犯用户将直接删库/删环境，没有第二次机会** |
| 1 | **绝不用 PowerShell 做 UTF-8 文本往返**（`Get-Content`/`Set-Content`/`-replace`） | 会破坏中文（mojibake + 换行塌缩），本会话踩过 4 次。改文件用 `edit`/`write` 工具，或写 Python 脚本执行 |
| 2 | 本机访问服务器服务要用 **`10.0.10.2`**，不是 `127.0.0.1` | 服务跑在服务器上；`127.0.0.1` 指向你自己的电脑 → `ERR_CONNECTION_REFUSED` |
| 3 | 密钥**只进 `.env`**（gitignore，600 权限） | 绝不写进代码、文档、提交、Issue |
| 4 | 本机 `NO_PROXY` 含非法 `[::1]` → httpx 报 `InvalidURL: Invalid port ':1]'` | 用 `scripts/run_without_proxy.py` 绕过 |
| 5 | 模型端点 `model.rhzy.ai` 上**只有 3 个模型真正可用**（2026-10-27 实测 21 个） | 可用：`workbuddy-ai/deepseek-v4.1-flash`（1.5s）、`-sg`（1.6s）、`hy3`（2.0s，但内容在 `reasoning_content`）。`qoder/qfmodel` 与 `qoder/dfmodel` **180s 超时无响应**；`opencode-zen-free/*` 14 个全部 502；`gpt-6-luna` 已下架。**换模型前必须逐个实测**，不要只看 `/models` 列表 |
| 5b | **404（模型不存在）现在会触发降级**（2026-10-27 修） | 原 `_is_retryable` 把 404 归为「参数错误」，不重试也不降级 → 主模型一下架，整个应用直接不可用（而降级链里明明有可用模型）。404 与 400/422 的本质区别：**400 是请求不合法（换谁都失败），404 是模型不存在（换一个就行）**。401/403/400/422 **仍不降级**，测试锁住 |
| 5c | **`config/default.yaml` 的 fallback_chain 只放实测可用的项** | `glm`/`qwen` 的 key 在 `.env` 里是 `your-glm-...` / `your-qwen-...` 占位符，实测 **401**。留着不是「多一层兜底」，而是降级时**先浪费一次 401** 才轮到下一个。测试 `test_fallback_chain_first_entry_is_usable` 断言链里不含这两家 |
| 6 | 容器内配置文件在 **`/app/config/`** | `PROJECT_ROOT` 已做多级探测（`MAA_PROJECT_ROOT` 环境变量 → app 包位置 → 文件位置 → cwd）；曾因 `parents[3]` 越界导致 `fallback_chain` 为空 |
| 7 | 服务器上有**其他项目的容器**（portainer / qdrant / rsshub 等） | 不要动；曾出现的 200G 磁盘占用属于别的项目 |
| 8 | 冒烟栈用独立 project `maa-test` + `ports: !override` | 防止 `down --volumes` 误删生产数据卷 |
| 9 | 改 `docker-compose.yml` 的 LLM 环境变量必须用**嵌套名** `LLM__XXX` | pydantic-settings 用 `__` 映射；只写扁平名不生效 |
| 10 | 新增 LLM provider 后要检查 `factory.py` 的 provider 循环元组 | 曾因漏加 `deepseek` 导致 base_url 取不到 |
| 11 | **验证/清理脚本必须回查确认，不能只打印成功** | 2026-10-21 发现：Task 3 的容器验证脚本打印了「探针数据已清理 ✅」，实际残留 1 条 `search_log` + 2 条 `screening_record`（次日核查才暴露）。清理后必须再查一次 count，并提供「演示数据未被误删」的对照数字 |
| 12 | 生产库**没有** `DELETE /tasks/{id}`（返回 405），探针任务要直接清库 | 清理顺序：`screening_decision` → `screening_record` → `search_log` → `task`（外键依赖） |
| 13 | **回归测试必须验证「回退修复就失败」** | 2026-10-21 踩到：为两个前端时序缺陷写的「回归测试」，把修复回退后**仍然全绿**（`flushPromises()` 观测不到中间帧），属于假测试。做法：临时回退修复 → 跑测试确认变红 → 恢复实现。抓不住的断言要**删掉**而不是留着装门面 |
| 14 | **只按 `created_at` 排序 = 顺序不稳定** | `created_at` 由 `server_default now()` 生成，**同一事务写入的多行时间戳完全相同**，PostgreSQL 对并列行返回任意顺序。已踩两次：`screening_decision`（用 `seq` 修）、`screening_record`（用 `(created_at, id)` 修）。凡「同事务批量插入 + 分页/列表」的表，排序键必须带**唯一列** |
| 15 | **删探针任务会留下未结算的配额预扣** | 配额结算靠 `task.quota_settled` 标记；**直接 `DELETE FROM task` 跳过了结算**，Redis 里预扣的 100k（`quota:{user_id}:{UTC日期}`）会残留。实测：删掉 2 个 E2E 探针后配额仍占 200k。清理脚本应额外 `redis-cli DEL` 当日 quota key，并回查 `KEYS 'quota:*'` 与 `GET` 值 |
| 16 | **测试载荷必须与生产载荷同形状**（2026-10-26） | `sum_actual_tokens` 只读 `output["prompt_tokens"]`，而生产载荷把 token 放在 `output["execution_result"]` 里。**三条单测的载荷都是「顶层平铺」的测试替身形状**，全绿；线上 `task.used_tokens` 一直是 0，配额每次都退满（用户白嫖算力）。写载荷前先去 `_run` 里看**真实返回结构** |
| 17 | **Core `update()` 也会触发 `onupdate`**（2026-10-26） | 用 SQLAlchemy Core `update()` 回填 `used_tokens`，把 28 个任务的 `updated_at` 全刷成同一时刻（`Base.updated_at` 带 `onupdate=func.now()`），而看板耗时用 `updated_at - created_at` → 90 秒显示成 9708 分。**写库脚本必须在 `values()` 里显式带上不想改的 `onupdate` 列**，并回查「只改了想改的那一列」 |
| 18 | **本地前端门禁必须跑 `vue-tsc -b`，不是 `tsc --noEmit`**（2026-10-26） | `tsc --noEmit` 不检查 `.vue` 模板里的类型；可选字段在模板里直接 `> 0` 比较（`possibly undefined`）只在镜像构建的 `vue-tsc -b`（= `npm run build`）阶段才报错 → Docker 构建失败、部署半途而废。**本地跑 `node node_modules/vue-tsc/bin/vue-tsc.js -b`** |
| 19 | **数据修复脚本必须自动检测目标，不硬编码时刻**（2026-10-26） | 恢复脚本硬编码污染时刻且用 naive datetime，而该列是 `TIMESTAMP WITH TIME ZONE` → 比较永不相等 → 脚本报「污染 0 行」，比报错更危险（看起来像没事）。改成检测「多任务共享同一 `updated_at`」这个批量 UPDATE 特征 |
| 20 | **`getComputedStyle` 在 Tailwind v4 项目里返回 `oklch()`，不是 `rgb()`**（2026-10-26） | 对比度测量脚本用正则抠 `rgb()` 数字 → 拿到 NaN → 比值算成 1 并误报失败。改用 canvas `fillStyle` + `getImageData` 转 sRGB 再算亮度 |
| 21 | **「当前对象」的信息不要靠前端去别处查**（2026-10-26） | 套餐页当前卡原先只有名字+篇数，权益要用户自己拿名字去下方四张卡里对照找。前端拿 `plan_id` 去 `plans` 里查 = **第二份配置**，后端改权益时静默显示旧值。**后端 `Plan` 表本来就有这些列，直接加进 `UsageOut` 返回** |
| 22 | **`period_key`（用量周期）≠ `period_end`（订阅到期）**（2026-10-26） | 页面只显示 `2026-10`，用户会以为「10 月底才续费」，而实际订阅 `2026-11-22` 到期。**凡「时间」字段先问它是什么周期**；免费档无到期概念 → 显示「免费档（无到期日）」，**不编假日期** |
| 23 | **`Promise.all` 会让一个接口失败吞掉另一个的结果**（2026-10-26） | 套餐页 `usage` 返回 500 时 `plans` 明明成功，4 张套餐卡却全消失（`planCardCount=0`）——用户在最需要看套餐时看不到。**同页多个独立接口用 `Promise.allSettled`，分别报错** |
| 24 | **回退验证必须用 `edit` 精确替换，不用 PowerShell `-replace`**（2026-10-26） | 用 `-replace` 改 `.vue` 模板把文件改坏 → 编译失败 → 输出 `Tests no tests`，看起来像「测试没覆盖」而实际是脚本坏了。与 §5 #1 同源 |
| 25 | **探针脚本不要 `sys.path.insert` 项目根**（2026-10-26） | 镜像里 `site-packages/app` 是 `pip install` 的**另一份副本**（缺 `styles/` 等资产）。探针 `sys.path.insert(0, BACKEND_DIR)` 让 `app` 抢先解析到副本 → 6 个样式**全部静默回退兜底格式化器**，输出只有 2 种却报「6/6 渲染成功」。容器内 WORKDIR=`/app` 已在 sys.path，**直接跑即可** |
| 26 | **`CitationRenderer` 兜底是静默的，必须检查 `warnings`**（2026-10-26） | citeproc 失败时 `render()` 回退极简格式化器并**返回非空条目** —— 看起来完全成功。凡涉及 CSL 渲染的验证，断言里必须加「`warnings` 为空」，否则测不出「所有样式长得一样」 |
| 27 | **读取工具的输出层会做脱敏，不要据此改代码**（2026-10-26） | `read` 显示 `placeholder="选择 .csl 文件"`，一度当成真实缺陷。用字符码核实（`[112,108,97,99,101,...]` = `placeholder`）确认文件本身正确。**怀疑是脱敏时，先打印字符码/hex 再判断** |
| 28 | **契约校验的是形状，不是真实性**（2026-10-26） | 模板页「渲染」按钮发的是假内容（`sections[key] = "示例内容（key）"`），后端 `_validate_contract` 只检查「占位符是否都有值」——**填假的照样通过**。契约能防「少填」，防不住「填假的」。**凡前端为通过校验而自己编内容，都是在制造假成功**；应改成「校验/预览」语义并显式标注示例 |
| 29 | **一个 E2E 文件只做一次登录**（2026-10-26） | `auth_login` 限流 **5 次/3600s**。模板页原本 4 条 test 各做「API 登录 + 页面登录」= 8 次 → **必然 429**，表现为「登录页超时」，看起来像页面缺陷（实测踩到，同一坑此前也打过 `plan.spec.ts`）。**拆 test 前先算登录次数**；需要多段断言就合并成一条 test 在一次页面访问里跑完 |
| 30 | **验证「引用可核验」时测试数据必须真实可核验**（2026-10-26） | 引用库端到端验证用 `10.1234/jcs.2024.001` 当 DOI，任务跑成 `waiting_human`。查校验报告：LLM 判定「10.1234 是示例性或占位符 DOI」——**它判对了**（10.1234 正是 DOI 手册的示例前缀）。换成真实 DOI 后同一流程 `completed`。**不要把校验器的正确判断当成系统缺陷**；造测试数据时先确认它在真实世界里站得住 |
| 31 | **一条命令只跑一个门禁，不要用 `Select-Object -Last` 截断**（2026-10-26） | 我把 `mypy` 和 `ruff` 串在一条命令里、都 `2>&1 \| Select-Object -Last N`，ruff 的 stderr 混进同一流并把 mypy 的错误行挤出尾部窗口 → **我只看到 mypy 的 `Success`，向用户报告「mypy 0 错误」，实际 CI 当场红了**（`"object" has no attribute "id"`）。真实状态一直是坏的，是我的读法坏了。**门禁逐个跑、看完整输出；`Select-Object -Last` 只用于已知格式的稳定输出** |
| 32 | **本地绿 ≠ CI 绿，push 后必须看 CI**（2026-10-26） | 本轮 3 个提交里前两个 CI 是 `failure`（同一个 mypy 错误），而我在简报里写的是「本轮未复验 CI」。**`gh` 已装且已认证（含 `workflow` 权限）**，`gh run list` / `gh run view --log-failed` 一条命令就能看。**不要把「没验证」写成「未复验」就当免责** —— 该查就查 |
| 33 | **§5 #16 会反复犯：写提取逻辑前先打印真实载荷**（2026-10-26） | 修「引用库任务证据为空」时，`_metadata_evidence_from` 读 `dependency_outputs[...]['artifacts']`，而调度器存的是 `nr.output`，形状是 `{result, status, execution_result: {artifacts: [...]}}` —— **artifacts 在 `execution_result` 里面**。单元测试夹具用扁平形状，**9 条全绿**；真实任务 `evidence_pool` 里 metadata = 0 才暴露。**同一轮里 #16 犯了第二次**（第一次是 token 统计）。做法：写提取逻辑前先 `print(json.dumps(真实载荷, indent=2))`，并让测试夹具**照抄**那个输出 |
| 34 | **「没触发」不等于「已修好」**（2026-10-27） | 修 review 长度上限后跑真实任务 → `completed`、`rules_passed=True`，看着像验证通过。但 review 内容只有 **7497 字符，根本没超过 10000** —— 这一段只证明「没回归」，**不能证明修复在超限场景生效**。补做容器内直接构造 **10230 字符**（与真实缺陷同长度）才确认修复。**凡是「条件触发型」缺陷，真实环境的随机输入可能刚好绕开触发条件**；验证必须显式构造触发条件，或用 `aggregated`/`authored`/不传角色**三行对照**证明「放宽了该放宽的、没放宽不该放宽的」 |
| 35 | **Issue 类修复要 grep 全部同类调用点**（2026-10-27） | Issue #6 引入 `content_role` 修「机器汇总被施加作者级检查」，但只改了 `citation_validator` 的 `duplicate_paragraph_ratio`；`ValidatorAgent._check_rules` 的 `max_length` **签名里根本没有这个参数**，于是同一类缺陷换了个检查项继续存在 8 天。**修「某类错误」时要 grep 该模式的所有出现位置**，不是只修眼前报错的那一处 |
| 36 | **回退验证前必须确认「回退版本真的部署上去了」**（2026-10-27） | 做 E2E 诚实性验证时，我改坏代码后跑测试 → **仍然通过**，差点判定「这条 E2E 抓不住缺陷」。实际原因：改动**没提交**，`git bundle` 里没有它，`df.sh` 部署的还是旧代码 —— **回退根本没生效**。第二次提交后再跑，E2E 正常变红。**回退验证必须做两步确认**：① `ssh ... 'git log --oneline -1'` 看服务器 HEAD 是不是回退版本；② `grep` 关键代码确认改动在位。**「测试通过了」可能是「改动没上」，不是「测试不行」** |
| 37 | **`git reset --hard` 会删掉未跟踪/未提交的新文件**（2026-10-27） | 回退验证后我用 `git reset --hard HEAD~1` 恢复，把**刚写好但只在那个 TEMP 提交里**的 E2E 文件一起删了（`git status` 干净，看起来像「什么都没丢」）。**新文件要先进 `$env:TEMP` 备份或先单独提交**，再动 reset。同类：`git reset --hard` 之后 `df.sh` 的 `merge --ff-only` 会失败（服务器 HEAD 成了本地不存在的提交）→ 部署静默不生效，需要用 `git reset --hard win/main` 强制对齐 |
| 38 | **禁止 `git add -A`：本仓库常有用户并行任务**（2026-10-28） | 用户同时在做 UI 重设计任务，工作区里有未跟踪的 `docs/design/`、`docs/superpowers/specs/*.md`。我用 `git add -A` 把它们一起提交进我的 fix commit，只能 `git reset --soft HEAD~1` 重做。**只 add 本任务的确切路径**，提交前 `git status --short` 逐条过一遍 |
| 39 | **改画像/配置里的「约定值」要 grep 全部同类断言**（2026-10-28） | 切默认模型时把 `fallback_chain` 首项从 `deepseek` 改成 `workbuddy-ai/-sg`，有 4 处断言硬编码 `fallback_chain[0].provider == "deepseek"`，我只改了 3 处，**全量测试跑 14 分钟才暴露第 4 处**（`test_config_priority.py`）。改约定值前先 `grep -r 'fallback_chain\[0\]\|chain\[0\]' tests/` |
| 40 | **实验类修复做完就停，不要顺手重跑矩阵**（2026-10-28） | 指标采集修好后我提议「重跑 60 run 验证」，被用户否决两次：交付在**次年 3 月**，现在重跑白烧 73 分钟和模型额度。采集逻辑有 11 条单测覆盖即可，**真实重跑等临近交付再做**。见 §6.2 |
| 41 | **后端 build 必须在 `/opt/veripaper/deploy/ecs` 目录**（2026-11-11） | 在 `/opt/veripaper` 根目录跑 `docker compose build backend`，项目名不同 → 产出 `multi-agent-assistant-backend` 镜像，生产仍在跑旧镜像，**白构建一次**。判断部署是否生效看**镜像 ID 变化**，不是看 compose 输出「Built」 |
| 42 | **SSH 长命令若输出被缓冲会断连**（2026-11-11） | `docker compose build \| tail -n 8` 在构建期间无输出，SSH 约 60 秒被对端断开，代码已同步但镜像没建。做法：`setsid nohup cmd > /tmp/x.log 2>&1 &`，再短命令轮询日志与镜像 ID |
| 43 | **对生产做接口测试会消耗全局限流配额**（2026-11-11） | 限流按 nginx 容器 IP 计数（`trust_proxy=false`），`auth_register` 5 次/小时是**全局**的，测试后真实用户无法注册。测试完必须清理：`docker exec vp-redis redis-cli --scan --pattern 'ratelimit:*' \| xargs -r redis-cli DEL` |
| 44 | **用户上传的 .docx 必须经 `validate_template_bytes` + 沙箱渲染**（2026-11-11） | docxtpl 默认用非沙箱 Jinja2，模板里的 `{{ ... }}` 可执行任意代码（SSTI→RCE，Issue #9）。新增模板相关入口时不要绕开校验；渲染必须传 `jinja_env=SandboxedEnvironment(autoescape=True)`。回归测试在 `tests/test_template_ssti_security.py` |
| 45 | **WorkBuddy worktree 里 `.csl` 哈希测试会红 4 条，是换行不是代码**（2026-11-11） | 系统级 `core.autocrlf=true`（PortableGit `etc/gitconfig`）让 checkout 把 LF 换成 CRLF，`sha256` 与 `ATTRIBUTION.md` 记录不符 → `test_csl_style_files::TestAttribution` 4 红。**判据**：`tr -d '\r' < 文件 \| sha256sum` 与记录值一致即属此类。**不要为了变绿去改 ATTRIBUTION 里的哈希**，也别提交被转换过的 `.csl` |
| 46 | **凭据吊销策略必须考虑「自己人重放」**（2026-11-12） | refresh 轮换做了「旧令牌重放 → 作废整个家族」，本地测试全绿；但浏览器开两个标签页时，access 过期会让两边**同时**用同一个 refresh 去换，第二个必然 401 → 一旦连坐就把用户每小时踢下线一次。**凡「检测到异常就全量吊销」的规则，先问一遍：正常客户端并发会不会命中同一条路径？** 结论：60 秒宽限期内只拒该次请求，超时才判定泄露（`JWT_REFRESH_REUSE_GRACE_SECONDS`） |
| 47 | **测试里改配置要改 `app.core.config.settings`，不是 `get_settings()`**（2026-11-12） | `test_config_priority` / `test_embedding` 会 `get_settings.cache_clear()`，之后 `get_settings()` 返回**新对象**，而端点读的是导入时的模块级 `settings`。`monkeypatch.setattr(get_settings(), ...)` 改的是没人用的那一个 → 同一个测试单跑绿、全量跑红（最难查的一类）。**判据**：先断言 `get_settings() is config.settings`，或直接改 `config.settings` |
| 48 | **改库的脚本必须「先落盘、再提交」**（2026-11-11） | 口令轮换脚本首版先 `commit` 再写口令文件；容器里写 `/opt/veripaper`（未挂载）直接 `FileNotFoundError` → **库里的口令已改、新口令却没人知道**，账号当场锁死。凡「生成一个只出现一次的秘密 → 写库」，顺序必须是先持久化秘密再提交 |
| 49 | **单文件 bind mount 不会跟着 git 更新**（2026-11-12） | `docker-compose` 把 `./nginx-api-tls.conf` 单文件挂进容器；`git merge` 是「写新文件 + rename」，inode 变了，容器仍绑在旧 inode 上 → `nginx -t` 说 OK、`nginx -s reload` 不报错、**但跑的还是旧配置**。判据：`docker exec vp-gateway grep -c add_header /etc/nginx/conf.d/default.conf`，为 0 即旧文件；解法 `docker compose up -d --force-recreate gateway`。**配了不变的东西没生效，先怀疑挂载** |
| 50 | **限流/审计用的客户端 IP 只能取自「网关覆盖写」的头**（2026-11-12） | `X-Forwarded-For` 由客户端起头，nginx 的 `$proxy_add_x_forwarded_for` 只是**追加**真实地址，第一段永远是客户端写的。原实现取第一段 → 任何人加 `X-Forwarded-For: 1.1.1.1` 就换一个限流桶（已实测复现）。正确做法：网关 `set_real_ip_from` + `real_ip_header CF-Connecting-IP` + `proxy_set_header X-Real-IP $remote_addr`，应用只读 `X-Real-IP` 且校验可解析，非法值退回 socket 地址 |
| 51 | **两层限流会互相遮挡，验收要看 reason**（2026-11-12） | 登录同时有「IP 5 次/分」与「账号 10 次/15 分」两层。从单个 IP 攒账号锁定次数时，先撞的是 IP 层，返回的 429 里 `reason` 是路由限流而不是 `account_locked` —— 看起来像「账号锁定没生效」。验收要么分轮打（5 次 + 等 61 秒），要么直接看 Redis 计数 |
| 52 | **改配置默认值 = 批量改测试前提**（2026-11-12） | 把 `rag.vector_backend` 默认从 chroma 改成 pgvector 后，34 个用例在 setup 阶段就报 `pgvector 后端不能使用 sqlite DSN` —— 它们默认「后端是 chroma」，只是从没写出来。**改默认值前先 grep 谁依赖它**；测试用值在 `conftest` 里 `os.environ.setdefault` 钉住，需要验证新默认的用例自己显式声明 |
| 53 | **`DOCKER-USER` 链是双向的，限制规则必须带 `-i <公网网卡>`**（2026-11-12） | 只写 `-p tcp --dports 80,443 -j DROP` 会把**容器出网**一起掐掉（容器发出的包同样走 `FORWARD → DOCKER-USER`，源地址 `172.18.0.x` 不在放行网段）。症状极具欺骗性：网站能开、健康检查 200，只有真正调模型/外部 API 时才 `Network is unreachable`。修法与自检见 `deploy/ecs/firewall-cloudflare-only.sh`（DROP 规则缺 `-i` 直接拒绝生效） |
| 54 | **「直连源站」测试必须先排除本机代理**（2026-11-12） | 用 `curl --resolve 域名:443:<源站 IP>` 测直连，返回 200 就以为源站裸奔；实际环境有 `http_proxy` 与 SDK 透明代理，`--resolve` 被完全忽略。**判据：响应头出现 `CF-RAY` 或 `Server: cloudflare` 就是没直连**。可靠办法：服务器自身打自己公网 IP（hairpin）、或第三方节点探测（check-host.net），并把「22 端口可连」当对照，证明探测本身有效 |
| 55 | **本地门禁要用 CI 的那个形态**（2026-11-12） | 本地跑 `black`（会改文件）以为门禁过了，CI 跑的是 `black --check`；若格式化发生在提交之后，改动就没进那次提交 → 主干 CI 连红两次。**做法：提交前跑 `black --check` + `ruff check` + `mypy`，与 CI 逐条同名**；`black` 只用来修，不用来验收 |
| 56 | **监控会以三种方式静默**（2026-11-12） | ① 容器 IP 写死（重启后失效）；② 依赖项目 venv/第三方包（升级后 ImportError）；③ 只看 HTTP 状态码（CF 错误页也是 200/502）。三者都不会报错，只会让检查「看起来在跑但永远不报」。**判据：定期真实制造一次故障验证链路**（本项目用 workflow_dispatch 的 `api_url` 覆盖跑通了「失败→开 Issue→恢复→关 Issue」） |
| 57 | **告警通道：HTTP 200 不等于发送成功**（2026-11-12） | 企业微信在 key 失效时仍返回 200，body 里 `errcode=93000`；只看状态码等于把告警丢进黑洞。另外消息上限是 **4096 字节**（中文 1 字 3 字节），按字符截断会算成 ~11 KB 被网关直接拒收。**两个都必须在代码里判**（`send_wecom` + `truncate_bytes`） |
| 58 | **CI 超时要跟着套件增长调整，别把「长大」误判成「卡死」**（2026-11-12） | 单元测试连续两次在 30 分钟被 cancel，第一反应是 runner 卡住（历史上真有先例，注释里就记着）。但同一套用例本地也要 **20m24s** —— 是 3128→3207 条用例把余量吃光了。**判据：先跑一遍本地全量拿真实耗时**，再按注释里的「实测 × 2.5」调 `timeout-minutes`；套件再涨 50% 就该按前缀拆分 job，而不是继续加时间 |
| 59 | **CI 不要每次跑全量；本地迭代同理**（2026-11-12） | 3209 条用例本地 19 分钟、GitHub 2 核 runner 超过 45 分钟——每次 push 都等一遍的结果是没人看结果。现在 push 只跑 `scripts/ci/select_tests.py` 选中的测试（全局前提改动→全量，未映射模块→全量，纯文档→不跑），全量与覆盖率门禁在 `nightly.yml`。**本地迭代也用 `python scripts/ci/run_selected_tests.py --base origin/main`**，别再习惯性跑全量 |
| 60 | **CD 必须是「测试绿 → 部署 → 冒烟 → 失败回滚」四段**（2026-11-12） | 用 `workflow_run` 触发（`on: push` 会在测试没跑完时部署），目标提交取 `github.event.workflow_run.head_sha`（该事件里 `github.sha` 是默认分支头，不是被测提交）。部署前打 `veripaper-backend:rollback` 标签；启动后轮询 `/health/ready` 并做一次真实检索；任一失败就回滚镜像 + `git reset --hard` 到部署前 sha。**没有回滚点的自动化部署等于把手工操作搬到流水线** |
| 61 | **会话中途失联，先查 fail2ban 再怀疑网络**（2026-11-12） | CD 部署会话挂 15 分钟无输出，服务器上却找不到报错 —— 是 `ssh-keyscan` 的多次密钥协商失败触发了 fail2ban，**封禁发生在会话已建立之后**，已建立的连接被 DROP，客户端只见 `Timeout, server not responding`。判据：`fail2ban-client status sshd` 看 Banned IP 是否是自己/CI 的出口；修法：脚本别开多条连接，CI 网段进 `ignoreip` |
| 62 | **「单跑绿、全量红」也可能是 get_settings() 对象分叉的镜像**（2026-11-13） | 端点里写 `get_settings()`、测试 patch 模块级 `settings`：CI 全量先跑 `test_config_priority`（cache_clear）后 get_settings() 返回新对象，patch 落空；本地单跑没有 cache_clear，两对象同一 → 绿。**修法不是改测试，是统一读取路径**：端点也读模块级 settings（生产里两者本来就是同一对象） |
| 63 | **第三方 Web Component 升级会改属性名，传错属性不报错、只回退到默认行为**（2026-11-13） | altcha 3.x 把 v2 的 `challengeurl` 改成 `challenge`；传旧名字时 widget **不报错**，而是回退去请求**当前页面**（拿到 SPA 的 index.html），报错却是 `invalid content-type: expected application/json, received text/html`——看起来像 CORS/后端问题。**判据：抓 xhr/fetch 响应，看它到底请求了哪个 URL**（出现 `/login` text/html 就是这个坑）；升级前先读包内 README 的 Supported Attributes 表 |
| 64 | **本地 `pip install` 的包必须同时写进 pyproject**（2026-11-13） | 本地装了 altcha 却没写依赖 → CI `pip install -e .` 没有它 → `import` 失败 → pytest **收集阶段 exit 2**（日志里没有 FAILED 行，只有 exit code 2，极易误判为 runner 问题）。**凡新增第三方 import，改代码的同一次提交必须改 pyproject** |

---

## 6. 待办事项

### 6.1 产品化开发（按优先级）

**Phase 1 · 打地基（2026-10-20 已交付五项）**：[计划](docs/superpowers/plans/2026-10-20-00-phase1-foundation-plan.md)

| 项 | 状态 | 说明 |
| --- | --- | --- |
| P1-A Word 模板导入 + 一键切换 | ✅ 已交付 `d44282a` + 端点 `fe56a67` + **前端 `08b7378`/`049f1a8`/`2828a5f`** + **修复 `cd30286`** | 领域模块 + HTTP 端点 + `user_template` 表（迁移 `a7d4e2f9c1b8`）+ **`/templates` 管理页**（占位符展示/上传/**校验**下载/删除）；上传准入拒 `.docm`/宏/OLE/zip 炸弹。**`cd30286` 修掉的缺陷**：按钮原叫「渲染」但发的是假内容（`示例内容（key）`），成稿正文 129 字符全是占位文本而页面零提示 → 改为「校验模板」语义 + 页面/成功提示都标注示例；占位符优先用后端 `label`；删除加二次确认。**遗留**：无 |
| P1-B 检索源扩展 | ✅ 已交付 `d44282a` | OpenAlex + S2 + PubMed + 多源聚合（三级去重、单源失败隔离、各源节流）。**遗留**：S2 需注入 `SEMANTIC_SCHOLAR_API_KEY`（公共档持续 429） |
| P1-C 账号体系 | ✅ 已交付 `d44282a` + 修复 `112c398` + **前端 `f7323ff`** | 邮箱验证 + 密码重置（只存哈希/单次使用/恒定响应）；未配 SMTP 不阻塞注册 + **`/verify-email`、`/password-reset`、`/account/verify` 三页** + 登录页「忘记密码」入口。**遗留**：无 |
| P1-D 限流 + 配额 | ✅ 已交付 `d44282a` + **缺口补齐 `63b1d95`** | 中间件 + Redis/内存实现 + fail-open + 熔断；429 带 Retry-After；**限流覆盖全部 19 条写端点**（新增 task_control/template_write/template_render）；**配额真扣减**（提交时预扣 100k、完成时按实际用量退还差额，`refund()` 原子且不扣到负数）。**遗留**：无 |
| P1-E RIS/BibTeX 导入 | ✅ 已交付 `d44282a` + 端点 `fe56a67` + **前端 `08b7378`** + **引用库 `6fdd213`** + **遗留三项 `8cfed9b`** | round-trip 与导出严格一致 + `POST /references/import` + **`/references/import` 预览页**。**`6fdd213` 补齐的能力缺口**：原先解析完**什么也做不了** → 新增 `reference_library_item` 表（迁移 `b7d4e9f1c6a2`）+ 4 个端点 + `LibraryLiteratureSource` + 任务 `reference_library=true` 开关 + 引用库页。**`8cfed9b` 补齐的三项遗留**：① 检索到的文献进证据池（`Evidence.kind` 区分 `excerpt`/`metadata`，`metadata` 不含编造摘要）；② 按归一化 DOI 去重合并（`merged` 与 `skipped` 分开报）；③ `PATCH` 条目编辑（改完重新准入）。**实测：真实任务 `evidence_pool` 里 metadata 4/4 可追溯 source_url**。**遗留**：引用库只存元数据（无摘要/全文，所以元数据证据只能支撑「存在这样一篇文献」，不能支撑「作者指出……」）；无跨用户去重 |
| **P1-D 遗留（P1）** | ✅ **已修复** `8794cc1` | 原缺陷：`quota_refund_for_usage` 只在完成时调用，**取消/失败/转人工/放弃确认 → 预扣 100k 白扣**。修法：`task.used_tokens`（用量落库，结算依据）+ `task.quota_settled`（幂等标记）；**四条终止路径全部结算**；`WAITING_APPROVAL` 改为「进入即结算、确认时重新预扣」（额度不足 429 拒绝确认）。迁移 `d4b1e7f2a8c3`（存量 completed 置已结算）。计划：[quota-settlement](docs/superpowers/plans/2026-10-21-00-quota-settlement-plan.md)。**遗留**：规划阶段用量不统计（规划 API 不返回 token）；存量 5 个修复前任务不回补（跨日窗口） |

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

> **⏸ 实验矩阵：暂不重跑（2026-10-28 用户明确决定）**
>
> 60 run 已于 2026-10-27 跑完（`rs-20261027T061627Z`：56 completed / 4 waiting_human，
> 73 分钟）。指标采集已修好，但**现在不要重跑** —— 交付在**次年 3 月**，
> 现在重跑是白烧时间和模型额度。
>
> **重跑时机**：临近交付、或需要定稿数据时再跑。跑之前先看
> [handoff §3.14](docs/runbooks/2026-10-26-handoff.md) 的「未修的两个缺口」。
>
> **不要主动提议重跑**，也不要因为「指标修好了」就顺手跑一遍验证 ——
> 采集逻辑已有单测覆盖（`tests/test_evaluation_metrics_collection.py` 11 条）。

- ~~跑真实 120-run 实验矩阵~~：**暂缓**（见上方说明）
- 论文撰写与答辩彩排（两次，记录表见 `docs/runbooks/defense-demo.md` 附 C）

**检索指标的硬阻塞（别当成 bug 去修）**：

`recall_at_5` / `mrr` / `ndcg_at_5` 需要**每个课题的相关文档标注**（qrels），
而 `evaluation/datasets/topics.json` 里**没有这个字段**（只有
`topic_id`/`domain`/`topic_zh`/`query_en`/`corpus_manifest`）。

所以这三个指标为 null **不是代码缺陷，是缺标注数据**。
要算它们必须先补「课题 → 相关文档」的人工标注（研究设计工作），
之后才能接进 `evaluation/metrics.py`。**在补标注之前，任何「修检索指标」的
改动都只是把一个 null 换成另一个 null。**

---

## 6.5 前端技术栈与设计规范（**强制**）

> 目标：**统一设计语言 + 技术栈锁定**，让任何一次改动（甚至 oneshot）都产出风格一致的界面。
> 新增页面/组件前必须先读本节；不按本节的写法一律视为不合格。

### 6.5.1 四条铁律（优先级最高）

| # | 规则 | 含义 |
| --- | --- | --- |
| 1 | **优先复用成熟库** | 不自研基础交互（弹窗、下拉、日期、拖拽、动画）。库能做的就不要手写 |
| 2 | **组件源代码可控** | 用**复制源码到仓库**的模式（shadcn 风格），组件代码进 `src/components/ui/`，可随时改，不做黑盒 npm 依赖 |
| 3 | **统一设计语言** | 只用本节定义的 tokens 与组件；不写一次性颜色/间距/字号；不引入第二套设计体系 |
| 4 | **严格限制重复依赖** | 同一能力只允许一个库（见 §6.5.2）；新增依赖前先确认现有库能否满足；**禁止**同时引入两个图标库/两个动画库/两个状态库 |
| 5 | **UI 设计先查 beUI（2026-11-18 用户指令，优先级最高）** | 所有新 UI（页面/组件/交互/动效）**必须先**到 beUI（<https://asharca.github.io/ui/>）找对应设计；找不到才降级到其他平台（Vue 生态成熟实现）；最后才是自己设计。详见 §6.5.1.1 |

### 6.5.1.1 beUI 优先规则（§6.5.1 铁律 5 的展开）

**beUI**（<https://asharca.github.io/ui/>，作者 arsharca，MIT）：React 19 + Next.js +
Motion（Framer Motion）+ Tailwind 4，经 shadcn CLI 分发。**511 个 Web 组件**（Motion 交互组件
~80 / Blocks 组合模块 ~108 / AI Agent 组件 ~28 / 图表 ~17）+ 73 个 React Native 组件。

## 技术事实（不要搞错）

beUI 是 **React 生态组件库，本项目是 Vue 3 —— 组件源码无法直接安装**。
shadcn CLI 的 `@beui/*` 分发同样面向 React。因此「用 beUI」指的是**用它的设计**，
不是引入 React 运行时（那是 §6.5.2 明令禁止的）。

## 三级查找顺序（新 UI 一律按此执行）

| 优先级 | 来源 | 落地方式 |
| --- | --- | --- |
| **① beUI 有对应设计** | 在 <https://asharca.github.io/ui/> 的 Motion 组件 / Blocks / AI Agents / Charts 里找 | **照搬它的交互模式与视觉规格**（动效参数、状态机、布局、CSS tokens——组件页的 Manual 标签可直接读 React 源码作参考），用本项目锁定的 **shadcn-vue + motion-v + Tailwind tokens** 复刻实现 |
| **② beUI 没有，Vue 生态有成熟实现** | shadcn-vue 官方组件、Aceternity、originui 等开源 Vue/Tailwind 组件 | 直接用/复制源码（仍须遵守 §6.5.2 的栈锁定与 §6.5.3 tokens） |
| **③ 都没有** | 自己设计 | 必须遵守 §6.5.3 Nova tokens；交互模式仍应先回 beUI 找同类组件作参照 |

## 使用要点

- beUI 组件页均提供源码（Manual 标签页），**先读源码再复刻**：重点提取动效时序（Motion
  的 spring/时长参数）、状态切换逻辑与响应式断点，而不是像素级照抄；
- beUI 的 **AI Agents 分类**（Agent Plan / Agent Team / Trace Waterfall / Context Meter /
  Voice Orb 等）与本产品的 Agent 编排场景高度相关，做编排类 UI 时**必查**；
- 复刻进项目的新组件放 `src/components/ui/`（基础）或按页面就近放置（组合件），
  与既有组件同等待遇（可改、可测）；
- beUI 的主题 tokens 与本项目 Nova 预设（§6.5.3）不同——**Nova 优先**：颜色/间距用
  本项目 tokens，只借鉴 beUI 的结构与动效。

### 全局 AI 入口（Issue #8 P0，2026-11-19，commit `9b800e16`）

**一句话 → 自动综述**的对话式入口已上线：

- `src/components/layout/GlobalPromptEntry.vue`：侧栏入口——展开态 BePromptInput
  紧凑版（Enter 直提交）、折叠态 Sparkles 圆按钮（点击展开侧栏）；
- 提交 → `taskApi.create({ topic, keywords: [句首 30 字] })`（后端契约 keywords
  min_length=1）→ **平台默认模型**（Qwen 网关）跑规划 → 跳转任务详情页；
- 付费墙复用既有套餐门禁（创建端点顺序：凭据 → 篇数月配额 → token 日预算）；
  免费档每月 1 篇即「不付费的额度」；
- 生产验收（2026-11-19 10:14）：「综述多智能体协作系统在软件工程中的研究进展」
  一句话 → 78s 规划成功（v1 · 5 节点，研究问题/检索式/验收标准齐全）→
  待确认规划（Agent Plan 卡片 Awaitng approval）；
- P1 待办：意图分类（非综述意图的提示与路由）；任务创建后的流式状态回显到入口。

### 联网搜索（2026-11-19 用户决策：Tavily 为默认方案）

- **provider：Tavily**（`SEARCH__PROVIDER=tavily`，`basic` 深度 = 最低 credit 档，
  `max_results=6`，snippet 截断 500 字控上下文）；
- **端点**：`GET /api/v1/search/web?q=...`（强制 JWT——搜索按 credit 计费，
  开放 Key 会被脚本刷爆）；401/429/5xx 分别给可读文案
  （「搜不到」与「搜索服务挂了」必须可区分）；
- **定位**：会话内核（P0）工具网关的「联网搜索」工具直接复用该 service；
- **不启用 DeepSeek 搜索**：用户本地 harness（`127.0.0.1:3425`）生产不可达，
  官方端点待评估；若后续搜索质量不足，提醒用户提供 DeepSeek 端点/key 后再切
  （`SearchConfig.provider` 已留切换口子，新增 provider 只需实现
  `search(query) -> list[WebSearchResult]`）；
- 生产验收（2026-11-19 11:35）：`q=RAG evaluation benchmark 2025` →
  6 条（GitHub MIRAGE / ACL Anthology 2025.findings-naacl / DEV 社区），
  provider=tavily 正确回显。

### 会话内核 P0（Issue #8，2026-11-19，后端 `0f2c884` 起）

**会话式 Agent 界面的地基**，与现有 19 个页面并存（入口是右下 FAB / `/chat` 全屏）：

- **数据模型**：`conversation`（title/archived/message_count/leaf_id）+ `message`
  （`parent_id` 树、role、status 五态、model/tokens/error），迁移
  `f5b6c7d8e9a0` → `a7b8c9d0e1f2`（时间戳默认值）→ 循环外键 ALTER；
- **API**：会话 CRUD + 详情（消息扁平返回，前端建树）+ 发消息（user + assistant 占位）
  + `GET .../stream`（SSE：start/delta/done/error）+ `POST .../stop`（协同取消）；
- **中断语义（重要）**：客户端 abort **不会**让生成循环退出（它挂在 LLM await 上），
  必须走 stop 端点写进程内标志；循环每 delta 检查并落库 `interrupted`；
  另有周期落库（每 20 delta）与「GET detail 时收敛 >5 分钟僵尸 streaming」；
- **前端**：Pinia `stores/chat.ts`（扁平建树 + 分支切换 + streamingText 独立存避免
  整树重渲染）、`ChatShell.vue`（floating FAB / dock / full 三形态，localStorage 记忆）、
  `ChatMessageItem.vue`（渲染/分支 ‹1/2›/复制/编辑重发/重试/流式光标）；
  SSE 用**原生 fetch + ReadableStream**（EventSource 不带 JWT），跨 chunk 缓冲到
  空行再分帧（防中文多字节被切碎）；
- **渲染**：`utils/chat-markdown.ts`（独立 Marked 实例 + KaTeX inline/block 扩展 +
  DOMPurify 追加 MathML 白名单；Mermaid 动态 import + strict + 失败保留源码）。

## 三个生产实测事故（都已修复，教训优先）

1. **全站白屏**：`chat-markdown` 在模块加载期 `new Marked()` + `use()` 抛错 →
   整个 bundle 崩（布局静态 import）。修复：惰性初始化 + 异常降级 +
   `defineAsyncComponent` 隔离。**教训：任何模块级副作用都必须可失败隔离**；
2. **流式 0 字符**：`stream_chat()` 漏了 `apply_model_profile`（`chat()` 有）→
   思考模型把 max_tokens 全花在 reasoning。**教训：成对的 chat/stream 路径必须
   同源配置**；
3. **时间戳 500**：手写迁移漏 `server_default=now()`；`Mapped[datetime|None]`
   默认推导成无时区 TIMESTAMP。**教训：手写迁移对照 Base 约定逐列核对**。

### 会话 E2E 与 Agent 工具网关（Issue #8 P1，2026-11-19）

**E2E**：`frontend/e2e/chat-kernel.spec.ts`（4 用例 × 2 视口）

- 真实链路（`E2E_REAL_LLM=1` 才跑）：FAB → 输入 → Enter → SSE 200
  （content-type 断言）→ 流式结束 → 助手正文非空 → 刷新后历史恢复；pageerror 为零；
- SSE 桩状态机（不耗 token、零抖动）：多 delta 流式 + Markdown/KaTeX/Mermaid
  节点断言；停止生成 → `已中断` 徽章且按钮消失；编辑重发 → 分支 `2/2` 切 `1/2`
  后仍能看到旧分支的助手回答；
- 稳定等待：只用 web-first 断言 + 显式超时，无「sleep 等结果」；登录辅助含限流
  重试与移动端 `nav-mobile-trigger`（Pixel 5 侧栏默认折叠）；
- **验收**：测试机 10.0.10.2 隔离栈（chromium + mobile-chrome）**8/8 passed**，
  后端定向用例 **15 passed**。
- **E2E 抓到的真缺陷**：编辑**首条**消息时 `parent_id=null` 表示「挂回根」，
  后端把 null 当「未传」→ 接当前叶节点 → 两条分支串成一条链。修复：
  后端用 `model_fields_set` 区分显式 null；前端 `null` 必须真实下发
  （`parentId ? ... : {}` 这类真值判断会把 null 丢掉）。

**工具网关**：`backend/app/tools/agent/`（12 个工具，全部复用既有 service 层）

| 域 | 工具 |
| --- | --- |
| 任务 6 | `list_tasks` / `get_task` / `create_task` / `approve_task_plan` / `cancel_task` / `retry_task_node` |
| 引用库 2 | `search_references` / `import_references` |
| 知识库 2 | `search_knowledge` / `list_knowledge_documents` |
| 配额 1 | `get_quota` |
| 搜索 1 | `search_web`（与 `/api/v1/search/web` 同一 Tavily provider） |

- 参数是 Pydantic 模型，目录里的 JSON Schema 由模型生成（与校验永不漂移）；
- **写操作必须 `confirmed=true`**，否则 409 `TOOL_CONFIRMATION_REQUIRED` 且不执行；
- 任务创建抽出 `services/task_submission.py::submit_review_task`，HTTP 与工具共用
  「凭据 → 篇数 → token」门禁顺序；意图分类抽出 `services/intent_service.py`
  （fail-open），`/tasks/classify-intent` 与 `/agent/route` 同一实现；
- 端点：`GET /agent/tools`、`POST /agent/tools/{name}/invoke`、`POST /agent/route`
  （**仅 JWT**：写工具没有 API Key scope 体系，先不开放给开放 API）；
- 生产验收：目录 12 / 配额 / Tavily 6 条 / route 5 关键词 / 写确认 409 /
  `create_task → get_task → cancel_task` 闭环（任务 `cancelled`）。

**E2E 隔离栈要点**（不碰 `~/projects` 旧环境）：独立 compose project + 覆盖
`container_name` 与**卷名**（compose 里是显式 `name: maa-*`，不覆盖会复用旧库）+
端口；`RATE_LIMIT__ENABLED=false`（8 用例串行会撞 5 次/分钟登录限制）；
Node 20 + `vite preview`；结束后 `down -v` 并删除含密钥的临时 env/override。

### 会话附件与语音（Issue #8 P2，2026-11-19）

- **附件模型**：`message.attachments` JSONB（迁移 `b8c9d0e1f2a3`），只存
  `{kind: "document", document_id, title}` 引用 + 标题快照，不复制文件；
  文档删除后历史消息仍保留可读标题。发送时校验文档归属（越权/不存在 400）。
- **上下文注入**：`build_context` 把附件标题与 `document_id` 追加到对应用户消息，
  **不注入正文**（正文检索走知识库工具，全文入上下文会打穿 token 预算）。
- **能力策略**：`GET /conversations/capabilities` 返回 documents / images /
  max_attachments / max_file_mb；`images` 由 `LLM__SUPPORTS_IMAGE`（默认 false）
  决定，当前平台 Qwen 为纯文本 → 前端选择图片时给出明确原因，**不假装能理解**。
  换视觉模型只需改环境变量，前端不用动。
- **前端**：ChatShell 附件按钮 + 拖放遮罩 + 上传中状态 + 可移除 chips；
  上传走知识库管线（PDF/DOCX/MD/TXT，≤30MB），上传中禁止发送；编辑重发/重试
  保留原附件。语音用浏览器 Web Speech API（zh-CN，本机识别，不上传音频），
  不支持时隐藏按钮。
- **验收**：后端定向 18 passed；E2E 双视口 **10/10 passed**（新增「上传 →
  发送请求带 document_id → 气泡展示附件」）；生产实测 capabilities、附件发送/
  回读、越权 400 全部符合预期。

### 聊天壳布局事故（2026-11-19，已修复）

症状：点「停靠到右侧」放大聊天栏后，任务页正文被挤成一条竖线（截图事故）。

两层根因：

1. `panelClass` 之后又追加 `relative`：Tailwind 的 `.relative` 声明在 `.fixed`
   之后，同一元素上两个 position 工具类时 **relative 生效** → 浮层变成布局内
   flex 子项，直接挤压主内容；
2. `vp:chat:variant` 把 `full` 也写入 localStorage，普通页初始化直接采纳，
   `flex h-full w-full` 进一步抢占宽度。

修复：position 工具类互斥（只有 full 形态用 `relative`）；`full` 不落盘，
普通页只接受 dock/floating；全屏改为直接导航，不再先设 full。
回归：E2E「持久化 full 形态不影响普通页布局」断言 `position=fixed`、
壳宽 <500、主区宽 >300，并覆盖点「停靠」的实际路径。

**教训：不要在同一个元素上叠加 fixed/absolute/relative 工具类——CSS 声明顺序
决定胜者，与 class 书写顺序无关。**

### 会话工具调用（Issue #8 P3，2026-11-19）

**契约**

- `BaseLLM.stream_chat_events()`：流式事件（`delta` / `done` + `tool_calls`）。
  默认实现降级为一次 `chat()`（语义不变、首 token 延迟变大）；OpenAI 适配器真流式，
  按 `index` 聚合 `tool_calls` 分片——`id/name/arguments` 会跨多个 chunk 到达，
  逐片处理会拿到残缺 JSON。
- 工具循环最多 `MAX_TOOL_ROUNDS = 4` 轮：**只读工具自动执行**并把结果回灌
  （`role=tool` + `tool_call_id`）；**写工具暂停**为 `waiting_approval`，
  由 `POST /conversations/{cid}/messages/{mid}/tool-confirm` 决定执行或取消。
  拒绝也要落库（`rejected`），否则刷新后确认卡会复活。
- SSE 事件：`start`(tools, resumed) / `delta` / `tool_call`(write) / `tool_result`(ok,preview) /
  `tool_approval`(arguments) / `done`(status) / `error`；`?tools=1` 才启用工具。
- **多步连续写操作（确认后续跑）**：`?tools=1&resume=1` 重开同一条消息的流——
  保留正文与轨迹，把已执行结果按 `assistant(tool_calls)+tool` **成对**回灌，模型继续
  生成并可提议下一个写工具 → 再暂停 → 再确认。前端 `confirmTool` 确认后自动续跑，
  拒绝不续跑。**确认摘要/取消说明必须追加到正文**（续跑以正文为基底重建上下文，
  覆盖会丢提议前文）。
- 防套娃护栏：单条消息写提议上限 `MAX_WRITE_PROPOSALS = 5`；同一 (工具,参数)
  被拒绝过 → `_write_blocked` 直接拦下并回灌原因（模型改口，不再弹卡）；
  空轨迹 resume 显式报 `nothing_to_resume`。
- `MessageToolCall.write` 字段必须存在（契约 `extra=forbid`）：轨迹记录带 `write`
  键但契约不认时，确认接口与会话详情回读都会 500——E2E 实测踩过。
- 持久化：`message.tool_calls` / `message.pending_tool` JSONB（迁移 `c9d0e1f2a3b4`）。
  存在消息上而不是独立表——它是这一轮回答的「过程」，与内容同生命周期。
- 前端：输入区「工具开/关」（`vp:chat:tools`）；气泡内「执行过程（N 步）」
  **默认折叠**（点 summary 展开）；确认卡读 `pending_tool`。

**网关硬约束（踩过）**：平台网关（Qwen）**只允许 system 出现在最前，且不接受
第二条 system**。工具策略必须**并入** `context[0]` 正文，不能 `insert` 一条新
system——否则开了工具整轮 400「System message must be at the beginning」。

- **可等待工具（wait_seconds 轮询）**：`get_task` / `approve_task_plan` 带
  `wait_seconds`（0~180），任务仍在 created/planning/retrying 时**工具内轮询**
  （5s 间隔）直到就绪/离开过渡态；`approve_task_plan` 就绪即自动确认，
  超时如实回报 `approved=false`（绝不硬确认）。为什么在工具内轮询而不是让
  模型反复调 get_task：每轮都要过一次模型（慢、贵、可能跑偏）。
- **执行期 keepalive**：慢工具执行期间流上每 15s 发 `tool_wait` 帧——Cloudflare
  约 100s 空闲掐断静默流；等待期间点停止同样生效（循环里检查 `_should_stop`）。
  前端把 wait 写进时间线预览（「等待任务状态变化…已等 Ns」）。
- ⚠️ **tool-confirm 必须单独放宽 axios 超时**：写工具在确认请求里执行，
  `wait_seconds=180` 时服务端轮询可达 180s+，全局 15s 超时会假失败
  （前端报错但服务端其实已确认）→ 当前 `confirmTool` 单独 240s。
- ⚠️ **E2E：resume 断言必须先于点击注册**：确认接口一返回前端就发起
  resume 请求，先点击再 `waitForRequest` 会漏接 → 假超时（实测踩过）。

**移动端可用性（踩过）**：任务页时间线两侧卡片把布局宽度撑到 614px
（Pixel 5 视口 393px），`position: fixed` 按布局宽度定位 → 聊天 FAB 落在可视区外，
点击无反应。三道修法：`BeTimeline` 加 `overflow-x-clip`；时间线容器
`pointer-events-none` + 卡片 `pointer-events-auto`；聊天浮层 `Teleport to body`
（`full` 形态除外）并提到 `z-200`。**教训：固定定位元素「点不到」先量布局宽度，
不要只加 z-index。**

**验收**：后端定向 21 passed（含 resume 续跑/回灌成对/空轨迹报错/拒绝防重复）；
E2E 双视口 **16/16 passed**（写工具用例覆盖 resume=1 请求与多步卡收口）。
生产实测多步链（2026-11-20）：`create_task(ok)` → 确认 → 续跑 →
`get_task(ok)` + `approve_task_plan` 提议 → 确认 → 规划未就绪的真实错误回灌 →
再续跑 → 模型如实总结两步结果；任务可查、可取消，全程无编造。
**E2E 注意**：第一张确认卡点掉后模型可能立刻提议下一张（多步链路的正常表现），
不能用 `toHaveCount(0)` 断言「卡没了」，要用循环处理后续卡。

**上游抖动**：平台模型偶发 `Concurrency limit exceeded`，属共享密钥并发限流，
重试即可（验收脚本已内置 15s×4 重试）。遇到它不要当代码缺陷排查。

### 后台管理控制台（Issue #12，2026-11-20）

- **只读原则**：管理端不提供代用户操作任务的接口（不代取消/重试/改配置）——
  需要处置时走用户侧已有控制命令，责任边界清晰。唯一写操作在原有
  BYOK 看板与套餐开通端点里。
- 端点（均 `get_current_admin_user`，普通用户 403）：
  - `GET /admin/console/overview?days=7`：用户/任务/失败率/待处理/token/BYOK
    一次聚合（同快照，避免首屏 6 个请求数字不同步）；
  - `GET /admin/console/tasks`：跨用户列表（owner、模型快照标签、BYOK 标记、
    token），状态/关键词/分页下推 SQL，`total` 在 body（不做 X-Total-Count）；
  - `GET /admin/console/users`：active 订阅 join 套餐名 + 任务数/凭据数。
- **口径**：窗口按 `created_at` 切（「这周失败率」= 这周提交的任务里失败占比）；
  失败率分母是**已结束**（completed/failed/cancelled），不是全部任务。
- 前端 `/admin`（requiresAdmin，侧栏「后台管理」）：6 张 KPI 卡（骨架屏）+
  任务/用户两表；状态色只表语义且必带文字。

**门禁教训**：前端类型检查必须用 `vue-tsc -b`（deploy 脚本用的就是它）。
`npx vue-tsc --noEmit` 会漏检部分文件——本次 `AppLayout` 少一个图标导入，
`--noEmit` 绿、`-b` 红、Pages 构建直接失败。

### 磁盘维护（生产 47.253.81.6）

2026-11-19 清理：`docker builder prune -af` 回收 **18.8GB**（containerd 里的
构建缓存，均可重建），磁盘 82% → 36%；未动任何数据卷（postgres/uploads/
artifacts）。**每次部署构建后建议顺手 prune**，否则每次 build 都会累积数 GB。
不要用 `docker system prune -a --volumes`：会删掉 uploads/artifacts 卷。

### 6.5.2 技术栈（**已锁定，不得擅自替换**）

| 能力 | 选型 | 说明 |
| --- | --- | --- |
| 框架 | **Vue 3.4+**（`<script setup>` + TS） | 本项目是 Vue，**不是 React** |
| 构建 | Vite 5 | — |
| 样式 | **Tailwind CSS v4** | 原子类 + design tokens；**禁止**新增手写 CSS 文件（除 `main.css` 的 token 定义） |
| UI 组件 | **shadcn-vue**（Reka UI 无头层 + CVA 变体） | 源码进 `src/components/ui/`，风格为 **Nova** 预设（中性灰 + 紧凑；见 §6.5.3） |
| 图标 | **Lucide**（`lucide-vue-next`） | 唯一图标库；禁止混用其他图标集 |
| 动画 | **motion-v**（Motion 官方 Vue 移植） | 统一过渡/入场；**禁止**再引入 GSAP/animate.css 等第二套 |
| 布局 | **splitpanes** | 可调整分栏（React 版 `react-resizable-panels` 的 Vue 对应物） |
| 拖拽 | **vue-draggable-plus** | React 版 `dnd-kit` 的 Vue 对应物 |
| 状态管理 | **Pinia** | Vue 官方方案（React 版 `Zustand` 的对应物）；**禁止**再引入 Vuex/其他状态库 |
| 表单校验 | **vee-validate + zod** | 只在复杂表单用；简单表单可直接绑 ref |
| 通知 | **vue-sonner** | 统一 toast；禁止 `alert()` / 自研 toast |
| 测试 | Vitest + @vue/test-utils + Playwright | 既有，不变 |

### 已交付的 beUI 复刻（2026-11-18，前端 Pages 已上线）

| 复刻组件 | 文件 | 落点 | beUI 规格 |
| --- | --- | --- | --- |
| Agent Plan | `src/components/motion/BeAgentPlan.vue` | 任务详情·规划 Tab（「执行计划」卡片，DAG 降级为折叠详情） | Bot 徽章/状态徽章/分段进度条/竖连线步骤/类型工具徽章/mono 时长/EASE_OUT/reduced-motion |
| Trace Waterfall | `src/components/motion/BeTraceWaterfall.vue` | 任务详情·执行 Tab（`data-test="trace-waterfall"`） | Header 统计/类型过滤 dimmed/条形按占比定位/失败斜条纹/时间刻度 |
| Context Meter | `src/components/motion/BeContextMeter.vue` | 我的模型·路由卡片 | 分段覆盖率条（6 段调色板）/大号百分比/分类列表 |
| Prompt Input | `src/components/motion/BePromptInput.vue` | 导入文献页（attrs 落 textarea，保持 `import-content` E2E 契约） | 自动增高/focus ring/字数统计/Ctrl⌘Enter |

约定：

- 新 beUI 复刻一律放 `src/components/motion/Be*.vue`，规格注释头部写明 beUI 来源页与常量；
- E2E 的 `data-test` 契约（plan-approve / plan-regenerate / import-content / import-submit /
  task-dag）**不可破坏**；
- 动效统一 motion-v + EASE_OUT `[0.16, 1, 0.3, 1]`，全部尊重 reduced-motion。

### beUI 增强（2026-11-18 晚，commit `1ca9c870`）

- **Trace Waterfall 播放头**：Replay/Pause + 40ms tick（2s 全程）+ range scrub +
  span scaleX 生长 + 越前 span 变暗 + 竖线（left 对齐时间轴列：
  `calc(14.75rem + P × (100% − 14.75rem))`）；reduced-motion 直达末帧；
- **Agent Plan 行展开**：running/failed 行可点击（`<component :is>` 动态标签）展开
  活动区——完整错误 + 「重试节点」（`emit retry` → TaskDetailView `retry_node` 命令，
  与执行页工具栏同路径）；ChevronDown 旋转；错误折叠态单行截断；
- **NodeDetail 重做为 span 详情面板**（beUI Trace Waterfall 详情侧栏规格）：
  Timing 条（兄弟窗口占比）+ started/completed/duration tabular-nums +
  错误 destructive 卡 + Attributes 列表（span id/kind/依赖/重试/output keys）；
  新增 `siblings` prop；`data-test="node-detail"` 不变。
  **遗留**：beUI 详情面板含 Model/Token/Cost 区块——本项目 subtask 快照尚无
  per-node token 字段，接入后补齐（需后端把 token 用量写进 subtask output）。

**为什么不是用户最初列的库**：`shadcn/ui`、`react-resizable-panels`、`dnd-kit`、`Zustand` 均为
React 专属，无法在 Vue 项目安装。上表是**逐项对应的 Vue 生态等价物**，设计理念一致
（源码可控、无头组件、原子样式）。**不要**为了「照抄清单」而强行引入 React 运行时。

### 6.5.3 设计语言（Nova 预设）

**基调**：中性、紧凑、安静。工作是读文献和写综述，界面不能抢注意力。

| Token | 值（OKLCH/HSL） | 用途 |
| --- | --- | --- |
| `--background` | 中性白/近黑（随主题） | 页面底色 |
| `--foreground` | 中性深灰 | 正文 |
| `--muted` / `--muted-foreground` | 浅灰 / 中灰 | 次要文本、禁用态 |
| `--border` | 极浅灰 | 分隔线、卡片描边 |
| `--primary` | 蓝（`oklch(0.55 0.2 255)` 附近） | 主操作、选中态 |
| `--destructive` | 红 | 删除、错误 |
| `--ring` | primary 半透明 | 焦点环 |
| 圆角 | `--radius: 0.5rem`（sm 0.375 / lg 0.625） | 统一圆角 |
| 间距 | 4px 基准（Tailwind 默认刻度） | 禁止 0.3rem 这类非标值 |
| 字号 | `text-sm` 为主（0.875rem），标题 `text-lg`/`text-xl` | 工作台信息密度高 |

**组件使用规则**
- 按钮一律 `<Button variant="default|secondary|ghost|destructive" size="sm|default|icon">`；
  不再手写 `.btn` / `.icon-button`。
- 状态徽标用 `<Badge variant="...">`；不再手写 `.badge`。
- 卡片用 `<Card>` 系列（`CardHeader/CardTitle/CardContent`）。
- 错误提示用 `<Alert variant="destructive">`；**不再自研 banner**。
- 空态用统一 `<EmptyState>`（项目自建，基于 shadcn 风格）。

**无障碍（硬要求）**
- 所有可交互元素键盘可达，焦点环用 `--ring`；
- 图标按钮**必须**有 `aria-label`；
- 颜色对比度 ≥ WCAG AA（4.5:1）；
- 动效尊重 `prefers-reduced-motion`。

### 6.5.4 目录约定

```
frontend/src/
  assets/main.css              # 唯一全局样式：tokens + @theme（不写业务类）
  components/ui/               # shadcn-vue 组件源码（可控、可改）
  components/common/           # 项目级通用件（ErrorBanner→改造成 Alert 包装、EmptyState）
  components/<domain>/         # 业务组件（tasks / knowledge / screening）
  lib/utils.ts                 # cn() 等工具
  views/                       # 路由页面
  stores/                      # Pinia
  api/                         # HTTP 层
```

### 6.5.6 包管理器：**npm（唯一）**

**前端只用 npm，不要用 pnpm/yarn。**

| 场合 | 命令 |
| --- | --- |
| 装依赖 | `cd frontend && npm install <pkg>` |
| 按锁文件安装 | `npm ci`（CI 与 Dockerfile 都用它） |
| 跑测试/构建 | `npm run test` / `npm run build` |

**为什么**：`nginx/Dockerfile` 与 `.github/workflows/ci.yml` 都用 `npm ci` + `package-lock.json`。
历史上仓库同时存在 `pnpm-lock.yaml` 与 `package-lock.json`，导致
「本地 pnpm 装完、Docker 的 npm ci 失败」（2026-10-20 实际踩到：镜像构建报
`process "/bin/sh -c npm ci" did not complete successfully`）。
已删除 pnpm 锁文件，**不要重新引入**。

装完依赖必须把 `package-lock.json` 一起提交，否则 CI/Docker 会因锁文件与
`package.json` 不同步而失败。

### 6.5.5 迁移状态（**M2 已完成**）

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| M0 | 基础设施：Tailwind v4 + tokens + `cn()` | ✅ |
| M1 | 基础组件：Button/Input/Label/Card/Badge/Alert/EmptyState/FilePicker | ✅ |
| M2 | 页面迁移（8 页 + 13 业务组件） | ✅ **8/8 完成** |
| M3 | 新页面直接用新规范（Phase 2 PRISMA 工作台） | ✅ 规则生效 |
| M4 | 删除 `main.css` 旧工具类 | ✅ 完成（无残留引用） |

**现状**：24 个 Vue 文件的 `<style scoped>` **已全部清零**；
`main.css` 只剩 tokens 与 `@theme` 映射。
**新增代码一律按本节规范，不要再写手写 CSS。**

**两个必须遵守的细节**

1. **绝不动态拼接 Tailwind 类名**：`stroke-${tone}`、`badge--${status}` 这类写法
   不会被 Tailwind 的静态扫描识别，样式会**静默丢失**（不报错）。必须用查表式
   字面量映射：

   ```ts
   const TONE_CLASS = { neutral: "stroke-border", active: "stroke-primary/50" }
   ```

2. **测试用 `data-test` 而非样式类定位**：类名是版式细节，改版式不该弄坏测试。
   用 `[data-test="plan-approve"]`，不要用 `.plan-panel__actions button`。

**组件清单**

| 组件 | 用途 |
| --- | --- |
| `Button` | variant: default / secondary / outline / ghost / destructive / link；size: sm / default / lg / icon / icon-sm |
| `Input` / `Label` | 表单输入与标签 |
| `Card` + `CardHeader` / `CardTitle` / `CardContent` | 卡片容器 |
| `Badge` | variant: default / secondary / outline / success / warning / destructive / muted |
| `Alert` | variant: default / destructive / success / warning / muted |
| `EmptyState`（common） | 空态（必须给「下一步做什么」的引导，不能只写「暂无数据」） |
| `ErrorBanner`（common） | `Alert` 包装，保留 `testid` 默认值 `error-banner` |
| `FilePicker`（common） | 文件选择（解决原生 file input 的英文文案问题） |
| `StatusBadge`（common） | 业务状态 → Badge 变体映射 |

### 6.5.7 前端改动的「完成」标准（**2026-10-26 复盘新增，必守**）

> 背景：前端重设计五个阶段里，**前三个每次报完成后各补了 2~3 个修复提交**
> （共 8 个）；后两个改成「先拿证据再改」后一次成型。
> 教训不是能力，是**顺序**。详见 [复盘](docs/retrospective/2026-10-26-frontend-redesign-retro.md)。

**报完成之前，下面五条必须都满足：**

| # | 要求 | 做法 |
| --- | --- | --- |
| 1 | **真实浏览器截图**看过渲染结果 | Playwright 登录 → 目标页 → `screenshot`，然后 `read_image` 亲自看 |
| 2 | **量过关键数值**，不是凭推理 | 元素可见性、节点个数、溢出宽度都用 `boundingBox()` / `count()` 取数 |
| 3 | **每次调整尺寸后重新量** | DAG 节点加高后必须重新确认容器够不够高（踩过：修好叠字又裁切） |
| 4 | **改 DOM 前 grep 过 E2E 断言** | `Select-String -Path e2e/*.spec.ts`；区分「设计变更（改测试）」与「回归（改实现）」 |
| 5 | **诚实性验证做过**：回退修复 → 测试必须变红 | 抓不住的断言删掉，不要留着装门面（§5 #13） |

**必须验证的页面清单**（改任一处都要重跑）：

```powershell
# 全量单测 + 类型（⚠️ 类型检查必须用 vue-tsc -b，见 §5 #18：
# tsc --noEmit 不检查 .vue 模板，镜像构建阶段才会暴露模板里的类型错误）
cd frontend
node node_modules/vitest/vitest.mjs run
node node_modules/vue-tsc/bin/vue-tsc.js -b

# 关键 E2E（无需 LLM 余额）
$env:E2E_BASE_URL='http://10.0.10.2:48080'
node node_modules/@playwright/test/cli.js test e2e/m2-all-preview.spec.ts `
  e2e/team-workspace.spec.ts e2e/phase1-pages.spec.ts --project=chromium
```

**跨用户/真实数据的验证不能用 mock 代替**：涉及金额、席位数、索引状态、配额这类
「显示的数字必须与实际一致」的地方，必须在服务器用 demo 账号打真实接口核对
（例：知识库页的 `vector_index_ready` 曾硬编码为 `true`，只在真实接口上才暴露）。

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

---

## 9. 文档入口（按需读，不必全读）

| 想知道什么 | 读哪个 |
| --- | --- |
| **接手新会话**（当前状态 / 剩余任务 / 命令 / 阻塞项） | [docs/runbooks/2026-11-11-handoff.md](docs/runbooks/2026-11-11-handoff.md) ⭐ **先读这个**；历史基线查 [2026-10-28-handoff.md](docs/runbooks/2026-10-28-handoff.md) |
| **安全排查结论与待修项**（1 Critical 已修 + 1 High 未修 + Medium/Low） | [docs/security/2026-11-11-security-audit.md](docs/security/2026-11-11-security-audit.md) |
| **前端重设计怎么做的**（10 个阶段的设计取舍与实测证据） | [docs/superpowers/specs/2026-10-23-frontend-redesign-design.md](docs/superpowers/specs/2026-10-23-frontend-redesign-design.md) §4.5~§4.18 |
| **用量看板为什么重做**（token 用量从未落库 + 5 个缺陷 + 数据修复） | 同上 §4.14 + [execution-notes](docs/superpowers/plans/2026-08-09-00-execution-notes.md) |
| **套餐页为什么重做**（当前卡零权益 + 到期日缺失 + 无升级入口 + 6 缺陷） | 同上 §4.15 + [execution-notes](docs/superpowers/plans/2026-08-09-00-execution-notes.md) |
| **引用样式页为什么重做**（零预览 + 两个国标样式无法区分 + 删除无确认） | 同上 §4.16 + [execution-notes](docs/superpowers/plans/2026-08-09-00-execution-notes.md) |
| **Word 模板页为什么重做**（「渲染」按钮发假内容 + 成稿全是占位文本 + 页面零提示） | 同上 §4.17 + [execution-notes](docs/superpowers/plans/2026-08-09-00-execution-notes.md) |
| **引用库为什么新建**（导入页解析完什么也做不了 + 任务文献只来自检索源） | 同上 §4.18 + [计划](docs/superpowers/plans/2026-10-26-00-reference-library-plan.md) + [execution-notes](docs/superpowers/plans/2026-08-09-00-execution-notes.md) 末节 |
| **为什么会返工**（我的错 + 环境坑 + 打分） | [docs/retrospective/2026-10-26-frontend-redesign-retro.md](docs/retrospective/2026-10-26-frontend-redesign-retro.md) |
| 产品化路线与竞品 | [docs/product/2026-10-18-demo-to-product-roadmap.md](docs/product/2026-10-18-demo-to-product-roadmap.md) |
| 架构决策（为什么不用 citeproc-js 等） | [docs/adr/](docs/adr/) |
| 部署 / 恢复 / 答辩 / 复现 | [docs/runbooks/](docs/runbooks/) |
| 用路径巡检找缺陷的方法与历史结果 | [docs/runbooks/usage-path-audit.md](docs/runbooks/usage-path-audit.md) / [...-2.md](docs/runbooks/usage-path-audit-2.md) |
| 各 Task 的计划与执行记录 | [docs/superpowers/plans/](docs/superpowers/plans/) |
