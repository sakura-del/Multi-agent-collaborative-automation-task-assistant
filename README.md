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

## 开发阶段

项目按 11 个阶段、5 个里程碑推进，预计总工期 23 周：

- 阶段 0：项目初始化与环境搭建
- 阶段 1：基础骨架 — 五层架构框架
- 阶段 2：底层大模型层
- 阶段 3：Agent 核心骨架
- 阶段 4：任务调度器与 DAG 引擎
- 阶段 5：RAG 知识库层
- 阶段 6：工具调用层
- 阶段 7：Agent 协作与调度整合
- 阶段 8：创新功能开发
- 阶段 9：业务场景实现
- 阶段 10：测试、优化与文档

## 代码规范

- Python: PEP 8 + Black + Ruff
- 提交信息: 遵循 Conventional Commits（feat / fix / docs / refactor 等）
- 单元测试覆盖率门禁: 各阶段验收标准见 dev-plan-v2.md

## License

本项目为毕业设计作品，仅用于学术与学习用途。
