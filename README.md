# 多智能体协作自动化任务助手

> 毕业设计项目 · 基于多智能体协作的自动化任务执行系统

## 项目简介

本项目构建一个支持多智能体协作的自动化任务助手，通过规划、执行、校验、复盘四大 Agent 协同工作，实现复杂任务的自动拆解、执行与质量保障。系统采用五层架构，集成 RAG 知识库、工具调用层与人工干预机制，支持文献综述、代码生成、电商文案等多种业务场景。

## 核心特性

- **四大多智能体协作：** 规划 Agent / 执行 Agent / 校验 Agent / 复盘 Agent
- **五层架构：** 用户交互层 / 多 Agent 调度层 / 工具调用层 / RAG 知识库层 / 底层大模型层
- **DAG 任务编排：** 基于有向无环图的任务调度引擎，支持并行执行与断点续传
- **RAG 知识库：** 向量检索 + BM25 混合检索 + BGE 重排，提供领域知识增强
- **插件化工具：** 爬虫、代码沙箱、文件读写、文档生成等可扩展工具集
- **HITL 人工干预：** 暂停/恢复、强制重跑、跳过步骤、终止任务
- **Web 可视化：** Vue3 + WebSocket 实时监控任务执行状态

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.11+ · FastAPI · SQLAlchemy 2.0 · asyncio |
| 前端 | Vue3 · WebSocket |
| 数据库 | PostgreSQL · Redis · Chroma |
| 大模型 | OpenAI SDK · LangChain · GLM / Qwen 国产模型 |
| 部署 | Docker Compose · Nginx |

## 项目结构

```
├── backend/                # FastAPI 后端
│   ├── app/
│   │   ├── api/            # 路由层（endpoints / deps / middleware）
│   │   ├── core/           # 基础设施（config / logging / security / exceptions）
│   │   ├── db/             # 数据库（base / session / models 13张表ORM）
│   │   ├── schemas/        # Pydantic 请求/响应模型
│   │   ├── interaction/    # 用户交互层（阶段1填充）
│   │   ├── orchestration/  # 多Agent调度层（阶段3-4填充）
│   │   ├── tools/          # 工具调用层（阶段6填充）
│   │   ├── knowledge/      # RAG知识库层（阶段5填充）
│   │   └── llm/            # 底层大模型层（阶段2填充）
│   ├── alembic/            # 数据库迁移
│   └── tests/              # 测试
├── frontend/               # Vue3 + TypeScript 前端
├── config/                 # 全局配置（default.yaml）
├── nginx/                  # Nginx 反向代理配置
├── docs/                   # 项目文档
├── docker-compose.yml      # 五容器编排
├── dev-plan-v2.md          # 分模块开发计划
└── checklist.md            # 任务清单
```

## 开发文档

- [分模块开发计划 v2](dev-plan-v2.md)
- [任务清单](checklist.md)
- [开发规范文档](docs/DEVELOPMENT.md)
- [组合交付路线图](docs/superpowers/plans/2026-10-17-00-combined-delivery-roadmap.md)（明确当前口径与排期）
- [执行记录](docs/superpowers/plans/2026-08-09-00-execution-notes.md)（每个任务的证据与偏差）

## 运行手册

| 手册 | 用途 |
| --- | --- |
| [部署手册](docs/runbooks/deployment.md) | 首次部署、验收检查、备份、更新 |
| [故障恢复手册](docs/runbooks/recovery.md) | 数据库/缓存/向量库故障、迁移失败、任务卡住、回滚 |
| [答辩演示脚本](docs/runbooks/defense-demo.md) | 5–10 分钟演示流程、离线兜底、彩排记录 |
| [实验复现手册](docs/runbooks/experiment-reproduction.md) | 120-run 实验与论文图表复现 |
| [开发环境](docs/runbooks/development-environment.md) | 本地/服务器开发环境准备 |

## 交付状态（四态口径）

四态定义：**implemented**（代码完成）→ **tested**（有自动测试覆盖）→ **integrated**（在真实部署链路中跑通）→ **accepted**（通过对应 Exit Gate 并有证据留档）。
只标注有证据支撑的状态；证据见 [执行记录](docs/superpowers/plans/2026-08-09-00-execution-notes.md)。

| 能力 | 状态 | 证据 |
| --- | --- | --- |
| 五层架构 + 鉴权（JWT/刷新轮换/owner 隔离） | accepted | Plan 1；集成测试覆盖双用户隔离 |
| 任务运行时（提交/确认/快照/事件/控制/恢复） | accepted | Plan 2 Exit Gate；服务器 PG+Redis 验收 `ok=true` |
| 文献综述闭环（证据约束 + 引用可回溯 + Markdown/DOCX） | accepted | Plan 3 Exit Gate；真实模型离线 10 篇任务 `completed`，引用零越界 |
| 前端工作台（登录/建任务/规划确认/DAG/干预/结果/知识库） | accepted | Plan 4 Exit Gate；桌面 + 移动双视口 E2E 4 passed |
| 进程级确定性 E2E | tested, integrated | Plan 5 Task 1；本地与服务器各连跑两遍 3 passed |
| 恢复 / 隔离 / 故障降级 | accepted | Plan 5 Task 2；7 个故障注入 + 14 个安全边界用例 |
| Compose 发布冒烟 | accepted | Plan 5 Task 3；服务器实跑 `SMOKE OK`，零残留且不影响生产栈 |
| CI 质量门禁（lint/类型/覆盖率/迁移/前端/镜像/冒烟） | accepted | Plan 5 Task 4；mypy 0 错误、覆盖率 87.86%、6 作业全阻断 |
| 可复现实验输入（4 组 × 15 课题 × 3 语料） | accepted | Plan 5 Task 5；`validate` 输出 `runs: 120` |
| 实验 runner 与指标 | accepted | Plan 5 Task 6；服务器 fixed 适配器 60 runs 全完成 |
| 论文报告生成（CSV + 5 图 + report.md） | accepted | Plan 5 Task 7；服务器 9 产物，固定 seed 可复现 |
| 部署/恢复/答辩运行手册 | accepted | Plan 5 Task 8；命令均来自实测；**两次彩排记录**见 defense-demo.md 附 C（第 2 次 55s 完成、引用可回溯、全程无需手工修库/终端） |

> 当前仅支持**文献综述**这一正式业务场景。代码生成、电商文案等属于论文中的扩展讨论，**不作为当前交付能力声明**。

## 开发阶段

项目按 11 个阶段推进，当前实际执行口径以 [组合交付路线图](docs/superpowers/plans/2026-10-17-00-combined-delivery-roadmap.md) 的 **十周路线**为准（十周为最短可行排期，交付后仍有论文撰写与补跑缓冲）：

- 阶段 0：项目初始化与环境搭建
- 阶段 1：基础骨架 — 五层架构框架
- 阶段 2：底层大模型层
- 阶段 3：Agent 核心骨架
- 阶段 4：任务调度器与 DAG 引擎
- 阶段 5：RAG 知识库层
- 阶段 6：工具调用层
- 阶段 7：Agent 协作与调度整合
- 阶段 8：创新功能开发
- 阶段 9：业务场景实现（文献综述）
- 阶段 10：测试、优化与文档

## 代码规范

- Python: PEP 8 + Black + Ruff + Mypy（均为 CI 阻断门禁）
- 提交信息: 遵循 Conventional Commits（feat / fix / docs / test / build 等）
- 覆盖率门禁: `pytest --cov=app --cov-fail-under=80`（当前 87.86%）

## License

本项目为毕业设计作品，仅用于学术与学习用途。

## 离线文献综述演示（Plan 3 Task 6）

冻结语料只提交 `backend/demo_data/literature_review/query.json` 与 `manifest.json`；
PDF 位于 `files/`（已 gitignore）。

```powershell
Set-Location backend

# 1) 准备语料（唯一需要网络的步骤：检索并下载 10 篇 arXiv PDF，写入 sha256 清单）
python scripts/prepare_demo_corpus.py
python -m pytest tests/test_demo_corpus.py -v      # 未准备时以明确提示 SKIP

# 2) 离线演示：知识库入库 → 规划确认 → 证据约束执行 → Markdown/DOCX 产物
python -m app.cli run-literature-demo --offline --max-documents 10
python -m app.cli run-literature-demo --offline --check   # 仅清单与 LLM 连通性
```

产物路径固定为 `<artifact_root>/<user_id>/<task_id>/<artifact_id>.{md,docx}`，
可通过 `GET /api/v1/tasks/{task_id}/artifacts/{artifact_id}` 下载。
