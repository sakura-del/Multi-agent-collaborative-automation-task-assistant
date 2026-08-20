# Literature Review System Master Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有模块级原型交付为安全、可恢复、可观测、可答辩并可重复开展论文实验的自动化文献综述系统。

**Architecture:** 以 PostgreSQL 为任务状态唯一事实来源，由 `TaskExecution` 深模块编排 Planner、Scheduler、Executor、Validator、Reviewer 和用户隔离 RAG。HTTP 只负责命令与查询，Redis 只负责短期事件分发，前端通过快照加增量事件展示任务。

**Tech Stack:** Python 3.11、FastAPI、SQLAlchemy 2.0、Alembic、PostgreSQL 16、Redis 7、Chroma、NetworkX、OpenAI-compatible LLM adapters、Vue 3、TypeScript、Pinia、Vue Router、Vite、Vitest、Playwright、Docker Compose、Nginx。

---

## Source Of Truth

- 设计规格：`docs/superpowers/specs/2026-08-09-literature-review-design.md`
- 本文件只定义执行顺序、里程碑和跨计划约束。
- 具体文件、测试、代码接口和提交点在五份子计划中定义。

## Scope Decomposition

| 顺序 | 计划 | 独立交付结果 | 进入条件 |
|---|---|---|---|
| 1 | `2026-08-09-01-foundation-security-plan.md` | 安全且可通过 Compose 启动的鉴权知识库基线 | 设计规格已确认 |
| 2 | `2026-08-09-02-task-runtime-plan.md` | 固定 adapter 驱动的持久化任务闭环 | 计划 1 全部验收通过 |
| 3 | `2026-08-09-03-literature-workflow-plan.md` | 真实文献输入、证据校验与 Markdown/DOCX 导出 | 计划 2 的重启恢复通过 |
| 4 | `2026-08-09-04-frontend-workspace-plan.md` | 无需 Swagger 或终端的完整操作工作台 | 计划 3 的 HTTP 契约冻结 |
| 5 | `2026-08-09-05-hardening-experiments-plan.md` | 答辩候选版本、CI 门禁、论文实验和运行手册 | 前四份计划全部通过 |

## Global File Map

```text
backend/app/api/v1/endpoints/       HTTP 与 WebSocket adapter
backend/app/schemas/                HTTP 请求/响应模型
backend/app/db/models/              PostgreSQL ORM
backend/app/db/repositories/        owner-scoped 数据访问
backend/app/orchestration/contracts.py  Agent 与运行时数据契约
backend/app/orchestration/services/ TaskExecution 与恢复运行时
backend/app/knowledge/services/     文档存储、索引、检索与证据上下文
backend/app/tools/literature/       arXiv 与导出工具
backend/evaluation/                 可重复论文实验
frontend/src/api/                   HTTP adapter
frontend/src/stores/                Pinia 状态
frontend/src/views/                 页面
frontend/src/components/            任务与知识库界面模块
frontend/e2e/                       Playwright 验收
docs/runbooks/                      部署、演示与恢复手册
```

## Global Engineering Rules

- 实施前创建独立 worktree，建议分支名 `codex/literature-review`；不得直接在当前 `main` 工作树执行实现。
- 每个任务严格按红灯测试、最小实现、绿灯测试、局部质量检查、提交的顺序执行。
- 不读取、输出或提交 `.env`；只修改 `.env.example` 中的占位配置。
- PostgreSQL 是任务状态唯一事实来源。Redis、进程内字典和 WebSocket 都不能覆盖数据库状态。
- 所有 `Task`、`KnowledgeDoc`、`HumanFeedback`、日志和产出物查询必须在 SQL 中包含 `user_id`。
- 测试不得访问真实模型或公网；真实模型与 arXiv 只在显式验收命令中使用。
- 计划 4 开始前冻结任务 HTTP Schema；计划 5 开始后停止增加产品功能。
- 每个提交使用 Conventional Commits，提交主题不超过 72 个字符。

## Baseline Commands

在实施第一项任务前记录以下命令的真实结果，不修复基线外问题：

```powershell
Set-Location backend
python -m pytest tests -q
python -m black --check app tests
python -m ruff check app tests
python -m mypy app
```

```powershell
Set-Location frontend
npm run type-check
npm run build
```

预期：记录每条命令的通过数或失败原因。不得沿用旧文档中的测试数。

## Milestone Gates

### M1: Secure Baseline

- 知识库接口全部鉴权。
- 任意服务端路径输入已删除。
- 两名用户的文档、BM25、Chroma 和缓存结果隔离。
- Compose 自动构建前端、应用迁移并通过真实 readiness。

### M2: Persistent Runtime

- `POST /tasks` 返回持久化任务 ID。
- 规划确认后才执行 DAG。
- 每次节点转换落库并产生递增事件序号。
- 重启后只重跑未完成节点。
- 固定 adapter 端到端测试通过。

### M3: Literature Workflow

- 上传或离线文献能够生成带证据映射的综述。
- 无证据论述不能标记为文献事实。
- arXiv 不可用时离线流程继续。
- Markdown 与 DOCX 可下载。

### M4: Frontend Workspace

- 用户可完成登录、上传、建任务、确认计划、查看 DAG、干预、查看结果和下载。
- 刷新、WebSocket 重连和轮询降级不丢任务状态。

### M5: Defense Candidate

- 后端、前端、迁移、Compose smoke、固定数据 E2E 均为阻断门禁。
- 15 个课题、3 个领域、4 个实验组、每组 2 次的运行入口可重复。
- 答辩候选版本没有未解决的 P0/P1 缺陷。

## Final Verification

五份计划全部完成后运行：

```powershell
Set-Location backend
python -m pytest tests --cov=app --cov-fail-under=80
python -m black --check app tests evaluation
python -m ruff check app tests evaluation
python -m mypy app
```

```powershell
Set-Location frontend
npm run type-check
npm run test:unit -- --run
npm run build
npm run test:e2e
```

```powershell
docker compose config
docker compose build
docker compose up -d
docker compose run --rm smoke
```

预期：全部退出码为 0；`/api/v1/health/ready` 返回所有依赖 `ok`；固定离线任务能够生成 Markdown 和 DOCX。

## Completion Commit

最终文档与清单对齐使用独立提交：

```powershell
git add README.md checklist.md dev-plan-v2.md docs
git commit -m "docs(project): align roadmap with literature review delivery"
```
