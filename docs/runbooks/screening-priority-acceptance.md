# PRISMA 主动筛选排序验收记录

> 日期：2026-11-02 ｜ 分支：`codex/screening-priority` ｜ 状态：自动化验收通过，已合并并部署到服务器（代码提交 `a2c46d4`），未推送远端
>
> 设计：[主动筛选排序设计](../superpowers/specs/2026-11-02-screening-priority-design.md) ·
> 计划：[实施计划](../superpowers/plans/2026-11-02-screening-priority-plan.md) ·
> 使用手册：[主动筛选排序](screening-priority.md)

## 1. 交付结果

| 能力 | 状态 | 证据 |
| --- | --- | --- |
| 纯排序器（TF-IDF/逻辑回归） | 完成 | 8 条纯排序测试；重复三次一致 |
| 任务全员标签聚合 | 完成 | Service 测试覆盖 include/exclude/maybe/无决策/跨筛选者冲突 |
| 候选过滤 | 完成 | 已有决策记录不进入候选；任务隔离测试 |
| 只读 API | 完成 | `GET /screening/priority`；401/404/400/503/三种状态测试 |
| 前端默认关闭开关 | 完成 | 默认零请求；ready 置顶保留其余；不足/异常原序 |
| 阶段边界 | 完成 | 全文/已纳入自动关闭并禁用；独立测试 |
| 无数据库迁移 | 完成 | `git diff --name-only main...HEAD` 无 alembic/migration 文件 |
| 真实浏览器冒烟 | 通过 | Playwright 登录 Preview 任务，确认开关、提示、4 条置顶名次实际渲染；完整 E2E 套件未重跑 |
| 真实部署 | 通过 | 服务器 backend/nginx 重建，五容器健康；代码提交 `a2c46d4` |

## 2. 提交记录

| 提交 | 内容 |
| --- | --- |
| `dca9732` | 设计 spec |
| `e8bb8d5` | 实施计划 |
| `04e51f1` | scikit-learn 依赖 + 纯排序器 |
| `c87e290` | Repository 任务级决策查询 + Service 标签聚合 |
| `cbdc111` | 只读优先级 API + schema |
| `0ea10d9` | 前端 API/开关/置顶展示 |
| `bb65146` | 边界测试（冲突标签、API URL、阶段关闭） |

## 3. 验收门禁

### 3.1 后端定向回归

```powershell
cd backend
& 'E:\Multi-agent collaborative automation task assistant\backend\.venv\Scripts\python.exe' `
  -m pytest tests/test_screening_priority.py tests/test_screening_priority_service.py `
  tests/test_screening_api.py tests/test_screening_service.py `
  tests/test_screening_repository.py tests/test_screening_counts_semantics.py `
  tests/test_screening_dedupe_parity.py -q --no-cov
```

结果：**99 passed in 18.53s**。

### 3.2 前端全量单测与类型

```powershell
cd frontend
$node='C:\Users\Lenovo\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe'
& $node node_modules/vitest/vitest.mjs run
& $node node_modules/vue-tsc/bin/vue-tsc.js -b
```

结果：**41 个测试文件 / 483 passed / 6 skipped；`vue-tsc -b` exit 0**。

其中：

- `ScreeningView.test.ts`：22 passed；
- `api/screening.test.ts`：10 passed。

### 3.3 静态门禁

```powershell
cd backend
python -m black --check app tests      # 386 files unchanged
python -m ruff check app tests         # All checks passed
python -m mypy app                     # Success: 200 files
```

结果：全部 exit 0。

### 3.4 1000 条候选性能

```powershell
cd backend
& 'E:\...\.venv\Scripts\python.exe' -c "... rank_candidates(pos+neg, cand) ..."
```

结果：

```text
ready 1000 0.058
```

即纯排序器在 50 正 + 50 负训练样本、1000 条候选下实测 **0.058 秒**（不含 DB 查询与前端渲染）。该数字来自当前开发机，不是生产 SLA。

### 3.5 部署与真实浏览器冒烟

服务器代码与镜像：

```text
server HEAD = a2c46d4
maa-backend healthy / maa-nginx running (rebuilt)
sklearn = 1.9.1
alembic current = b7d4e9f1c6a2 (head)
alembic heads   = b7d4e9f1c6a2 (head)
```

接口与前端产物：

```text
GET /api/v1/screening/priority 无 token → 401（路由已挂载）
有 demo token + 有筛选数据的任务 → 200, status=ready, 4 candidates
nginx 静态 JS 包含“智能排序”字符串
```

真实浏览器（Playwright，headless Chromium）：

```text
登录 demo → /tasks/b698044a-2570-425a-a5b3-24d497b2f11f/screening
状态提示：基于任务内 1 条纳入 / 1 条排除决策排序
推荐徽标：推荐优先 #1 / #2 / #3 / #4
```

预览任务：`Preview: Active Screening Ranking`（`b698044a-2570-425a-a5b3-24d497b2f11f`）。
该任务是独立 demo 数据，含 6 条记录、1 条纳入、1 条排除，可随时删除，不改动现有任务。

访问入口：`http://10.0.10.2:48080/tasks/b698044a-2570-425a-a5b3-24d497b2f11f/screening`（需先登录 demo）。

## 4. 验收项对照

| # | 验收项 | 结果 | 证据 |
| --- | --- | --- | --- |
| A1 | 排序确定可复现 | 通过 | 纯测试重复三次；固定算法版本与 random_state |
| A2 | 标签语义正确 | 通过 | Service 聚合测试 + 跨筛选者 include/exclude 冲突测试 |
| A3 | 无数据泄漏 | 通过 | 有决策记录绝不进入候选的断言 |
| A4 | 冷启动诚实 | 通过 | insufficient/no_candidates/error 均保持原序并提示 |
| A5 | 不改变既有流程 | 通过 | 默认关闭零请求；既有 ScreeningView/PRISMA 测试全绿 |
| A6 | 中英文可用 | 通过 | 英文与中文排序测试 |
| A7 | 安全与边界 | 通过 | 401/404/400/503；无新迁移、无外部调用 |
| A8 | 前端可解释 | 通过 | 只显示 rank，不显示概率；显示标签计数提示 |

## 5. 已知边界

- 仅标题/摘要阶段；
- 任务级标签，不做按筛选者独立队列；
- 已被第一位筛选者处理的记录不会进入第二位筛选者的推荐队列，但仍出现在普通列表；
- 请求时现算，不持久化模型/分数，历史顺序只能靠同数据/同版本复现；
- 不做模拟评估、WSS@95、学习曲线、自动停止；
- 真实浏览器已做一次 Preview 冒烟；**完整 Playwright E2E 套件未重跑**；
- 未推送远端。

## 6. 下一步

1. Preview 实际观感**已由用户确认通过**（2026-11-03），Preview demo 任务按用户要求保留；
2. 如需要论文方法学结果，再独立设计 ASReview 模拟评估切片；
3. 后续正常开发时补跑完整 Playwright E2E 套件。
