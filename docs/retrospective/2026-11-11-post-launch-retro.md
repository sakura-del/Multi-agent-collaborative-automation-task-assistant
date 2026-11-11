# 复盘：上线后收尾（2026-11-11）

> 本会话任务跨度大（嵌入上线 → 磁盘 → Cloudflare → 安全排查 → RCE 修复 → 交接），
> 这里只记「做错了什么」和「下次怎么做」，不重复执行细节（细节在 handoff 与 execution-notes）。

## 1. 任务清单与结果

| # | 任务 | 结果 | 返工 |
| --- | --- | --- | --- |
| 1 | SiliconFlow BGE-M3 嵌入上线 | 28,341 条 10 分钟重建完、0 失败 | **一次方向性返工**：先按本地 BGE-large 跑，用户质疑后改 API 方案 |
| 2 | 磁盘清理 81% → 33% | 释放 18.7 GB，服务无感 | 无 |
| 3 | Cloudflare 建议处置 | security.txt 上线；Bot Fight/AI Labyrinth 判定不开 | 无 |
| 4 | Pages 改 Git 集成 | push 即部署，闭环验证 | 2 次（给错 App slug、变量晚于构建） |
| 5 | 安全排查 | 1 Critical + 2 High + 5 Medium + Low | 无（方法有效） |
| 6 | 修 #9 模板 RCE | 已修、已部署、已生产验证、Issue 关闭 | 1 次（compose 目录错） |
| 7 | 交接与蒸馏 | handoff + AGENTS.md + 本文件 | 无 |

## 2. 我的错（按代价排序）

### 2.1 方向性错误：默认「本机装模型」而不是先算清成本（最贵）

接手「启用真实嵌入」时，直接沿用设计里的本地 SentenceTransformers 方案，
跑起来才被用户点破：**2 vCPU 跑 28,341 条要 9 小时，为自有测试数据不值得**。

改判依据其实早就在手边：模型维度、条数、机器规格、API 单价都能查。
**先花 10 分钟做「成本 × 收益」三行估算，再决定动手**；这次省下的 9 小时
足够覆盖十次估算。

### 2.2 在错误的目录构建镜像（白构建一次）

生产编排文件在 `/opt/veripaper/deploy/ecs/docker-compose.yml`，
我在 `/opt/veripaper` 根目录跑 `docker compose build`，产出
`multi-agent-assistant-backend`（另一个项目名），生产镜像没变。
**判断部署生效看镜像 ID，不看 compose 输出。**

### 2.3 SSH 命令输出被缓冲导致断连

`docker compose build | tail -n 8`：构建期间 `tail` 不输出 → SSH 空闲 60 秒被断。
代码同步成功、镜像没建，容易误判「部署完成」。
**长任务一律 `setsid nohup ... > /tmp/log &` + 短命令轮询。**

### 2.4 验证脚本传错 context，差点否掉正确方案

验证「沙箱下正常占位符能否渲染」时，脚本里 `template.render({})` 传了空 dict，
输出没有值，一度以为 `SandboxedEnvironment` 有功能回归。实际是脚本 bug。
**验证修复方案时，先确认「基线本来就正常」**——正常路径输出为空要先怀疑脚本。

### 2.5 猜 URL 而不是查

给用户 `github.com/apps/cloudflare-pages`（404）。正确 slug 是
`cloudflare-workers-and-pages`。**给用户点链接前先 curl 一次状态码。**

## 3. 用户侧反复碰到的坑（已根治的）

| 坑 | 本会话处理 |
| --- | --- |
| Cloudflare 令牌 IP 白名单 vs 动态出口 IP | **根治**：Pages 改 Git 集成，发布不再依赖令牌 |
| 磁盘 80% 告警 | 清理 18.7 GB + 记录「哪些能删/哪些不能删」 |
| 弱口令（`demo` 与 e2e 账号） | **已根治**（Issue #10，同日完成）：口令轮换为强随机并只存服务器 600 文件、26 个测试账号删除、仓库明文清零并由测试锁住 |

## 4. 做得对的（保持）

- **先本地沙箱复现再下结论**：SSTI 用无害回显在本地验证，未在生产执行命令；
- **诚实性验证**：回退两处修复后确认 10 条测试变红，避免假测试；
- **生产验证用真实上传**：三类恶意模板 + 正常模板，用完即删并回查数量；
- **发现错误立即止损并说明**：误构建的镜像、限流副作用都主动清理并报告。

## 5. 已固化到哪

| 结论 | 落点 |
| --- | --- |
| build 目录、SSH 缓冲、限流副作用、模板安全 | `AGENTS.md` §5 铁律 #41–#44 |
| 当前状态、待办、命令、部署流程 | `docs/runbooks/2026-11-11-handoff.md` |
| 安全分级清单与修复优先级 | `docs/security/2026-11-11-security-audit.md` + Issue #9/#10/#11 |
| Pages Git 集成的两个坑 | `docs/runbooks/ecs-deployment.md` §13 |
