# AGENTS.md — 项目上下文与开发约定

> 本文件是**给 AI 编码助手看的交接文档**。新会话开始时请先读完本文件再动手。
> 最后更新：2026-11-11（上线后：M3 嵌入 + Pages Git 集成 + 安全排查与 #9 修复）
>
> **新会话请先读** → [docs/runbooks/2026-11-11-handoff.md](docs/runbooks/2026-11-11-handoff.md)
> （紧凑版：现状 / 未完成项 / 硬约束 / 命令与坑）。本文件查约定与历史坑，handoff 查「现在该干什么」。
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
| HEAD | **`9fceda6`**（CI/CD 重构 + 冒烟修复，自动部署已验证）（`feat(ops): 站内自检 + 外部探测的双层监控告警` + 格式修复）← `dfd6b0f`（`chore(ops): SSH 加固 + 清密钥备份 + 系统包 + 源站只允许 Cloudflare 回源`）← `54ce78a`（`fix(security): refresh 令牌轮换后旧令牌立即失效`）→ `105b383`（口令轮换）→ `5421a98`（SSTI 修复）→ `090e29a`（M3 上线）、Pages Git 集成、安全排查与 `security.txt` |
| 冻结 tag | `defense-v1` |
| Plan 状态 | Plan 2 / 3 / 4 / 5 **全部 Exit Gate PASSED**；**Phase 1 五项全部端到端可用且遗留清零**；**Phase 2 五项全部交付完成**（PRISMA ✅ + 可观测性 ✅ + 团队工作区 ✅ + 开放 API ✅ + 计费套餐 ✅） |
| 产品化进度 | **Issue #2~#7 全部关闭且遗留清零**；**P1-D 配额遗留已修复**；**`max_results` 已夹紧**；**PRISMA 完成**；**可观测性完成**（8 指标 + `/metrics` + 看板，**看板数字已全部为真**）；**团队工作区完成**；**开放 API 完成**；**计费套餐完成**（四档 + 四条功能门禁 + 手工开通 + **套餐页已重设计**）；**引用样式页已重设计**（**真实渲染预览** + 删除二次确认）；**Word 模板页已修**（**不再把示例填充谎称成稿** + 删除二次确认）；**前端已迁移到 Tailwind v4 + shadcn-vue**；**引用库 + 证据闭环完成**；**review 长度上限缺陷已修**（`max_length` 不再施加到机器汇总） |
| 数据库迁移 head | **`a7d3e9b6c1f4`**（`revoked_refresh_token`；前一个 `c4f2a8d7e9b1` = `knowledge_vectors_bge` 影子表） |
| 后端测试 | **3157 passed**, 21 skipped；覆盖率 **90%**；`mypy` 0 错误（219 文件）；`.csl` 4 红已由 `.gitattributes`（`*.csl -text`）消除 |
| 前端测试 | Vitest **458 passed**（+6 skipped）；**Playwright E2E 44 场景**（chromium + mobile-chrome 双视口） |
| 发布方式 | **前端：`git push backup main` 后 Cloudflare Pages 自动构建部署**（Git 集成，无需本机 Node）；**后端：bundle + `deploy/ecs` 目录 `docker compose build backend`** |
| CI | ⚠️ **2026-10-28 起 CI 因 GitHub Actions 账单额度停摆**（报 spending limit），作业 0~2s 就 X，**不是代码问题**。⚠️ **GitHub 直连时通时不通**：不通时走代理（`git -c http.proxy=http://127.0.0.1:7894 ...`，`gh` 需 `$env:HTTPS_PROXY`） |
| 服务器 | `root@47.253.81.6`，HEAD **`9fceda6`**（CD 自动部署），backend 镜像 `c476209d879c`（1.78 GB，已无 chromadb）；80/443 仅接受 Cloudflare 回源（`DOCKER-USER`，第三方探测 443=0/4）；UFW active；SSH 3 把密钥、X11 关、MaxAuthTries 3；监控两层已上线（站内 2 分钟 / 站外 5 分钟），**webhook 已配置并验证**（`/etc/veripaper/notify.env` + 仓库 secret `WECOM_WEBHOOK_URL`，2026-11-13）（回滚锚点 `shadow-mock`=`79ac91a4374c`），五容器 healthy，公网 `ready`，嵌入 = **SiliconFlow BGE-M3**（1600 RPM / 400k TPM），**user=1（仅 demo）/ task 86 / doc_chunk 2835 / 两表各 2835**（2026-11-11 清掉 26 个测试账号后），磁盘 38% |

**演示账号**：`demo` —— **全权限管理员**（`role=admin` + `institution` 套餐：篇数不限 / 含团队 / 含开放 API）；知识库 11 篇 / 2835 分块（2026-11-11 清理测试账号后，全库仅剩 demo）。
**口令不入仓库**：2026-11-11 已轮换为强随机口令，只存在于服务器 `/opt/veripaper/.demo-password`（600）与口令管理器；要登录就 `ssh root@47.253.81.6 cat /opt/veripaper/.demo-password`。
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

### 4.1 本地（Windows）

```powershell
cd 'E:\Multi-agent collaborative automation task assistant\backend'
# 全量测试（必须指定 basetemp，否则会撞权限问题）
.\.venv\Scripts\python.exe -m pytest tests -q --basetemp="$env:TEMP\pytest-dsh"
```

- 前端（**只用 npm**，见 §6.5.6）：`cd frontend && npm run test` / `npm run build`
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
