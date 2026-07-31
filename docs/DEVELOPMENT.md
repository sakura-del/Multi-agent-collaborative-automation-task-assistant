# 开发规范文档

> 多智能体协作自动化任务助手 · 团队协作与代码质量基线
>
> 本文档从阶段0起生效，所有贡献者提交代码前请通读本文。

---

## 1. 项目结构

```
.
├── backend/                # FastAPI 后端
│   ├── app/
│   │   ├── api/            # 路由层：endpoints + deps（依赖项）+ middleware
│   │   ├── core/           # 基础设施：config / logging / exceptions / security
│   │   ├── db/             # 数据库：base / session / models（ORM）
│   │   ├── schemas/        # Pydantic 请求/响应模型（与 ORM 分离）
│   │   ├── interaction/    # 用户交互层（阶段1填充）
│   │   ├── orchestration/  # 多 Agent 调度层（阶段3-4填充）
│   │   ├── tools/          # 工具调用层（阶段6填充）
│   │   ├── knowledge/      # RAG 知识库层（阶段5填充）
│   │   ├── llm/            # 底层大模型层（阶段2填充）
│   │   └── main.py         # 应用入口
│   ├── alembic/            # 数据库迁移
│   ├── tests/              # 测试
│   ├── pyproject.toml      # 依赖与工具配置
│   └── Dockerfile
├── frontend/               # Vue3 前端
│   ├── src/
│   │   ├── api/            # API 封装（axios 实例 + 各业务接口）
│   │   ├── assets/         # 静态资源
│   │   ├── App.vue         # 根组件
│   │   └── main.ts         # 入口
│   ├── vite.config.ts      # Vite 配置（含 /api proxy）
│   └── package.json
├── config/                 # 全局配置（default.yaml）
├── nginx/                  # Nginx 配置
├── docker-compose.yml      # 容器编排
└── docs/                   # 项目文档
```

### 分层约定

- **路由层（api/endpoints）**：只做参数校验、调用 service、返回响应，不含业务逻辑
- **service 层**（后续阶段补充）：业务逻辑，编排 ORM 与外部调用
- **ORM 层（db/models）**：纯数据定义，不含业务方法
- **Schema 层（schemas）**：请求/响应模型，与 ORM 解耦，禁止直接返回 ORM 对象

---

## 2. 代码风格

### 2.1 Python

- **格式化**：Black（line-length=100），自动格式化
- **Lint**：Ruff（E/W/F/I/B/UP/RUF），已忽略 RUF001/002/003（中文全角标点）
- **类型检查**：Mypy（非 strict 模式，新代码鼓励补充类型注解）
- **行宽**：100 字符
- **导入排序**：isort 规则（known-first-party=app）

工具配置统一写在 `backend/pyproject.toml`，禁止局部覆盖。

### 2.2 前端

- **TypeScript**：strict 模式，所有 `.vue` 与 `.ts` 文件需通过 `vue-tsc` 检查
- **构建**：Vite，开发服务器端口 5173（与后端 CORS 白名单一致）
- **风格**：2 空格缩进，单引号，无分号（遵循 Vue 官方 eslint-config 风格，后续接入 eslint）

### 2.3 通用

- 中文项目，注释与文档字符串使用中文
- 注释解释"为什么"而非"做什么"
- 命名有意义，避免缩写（循环变量 i/j 除外）
- 函数单一职责，超过 80 行考虑拆分

---

## 3. Git 工作流

### 3.1 分支策略

| 分支 | 用途 |
|------|------|
| `main` | 可发布的稳定版本，受保护 |
| `dev` | 集成分支，功能合并到此 |
| `feat/<阶段>-<模块>` | 功能分支，如 `feat/2-llm-adapter` |
| `fix/<问题描述>` | 修复分支，如 `fix/jwt-refresh-type-check` |

### 3.2 Commit 规范

遵循 Conventional Commits：

```
<type>(<scope>): <subject>

<body>
```

- **type**：feat / fix / docs / refactor / test / chore / perf
- **scope**：模块名（auth / db / llm / rag / frontend 等）
- **subject**：祈使句，不超过 50 字

示例：
```
feat(auth): 实现注册/登录/刷新端点
fix(db): 修复 Alembic 中文注释编码问题
docs: 补充阶段0开发规范文档
```

### 3.3 提交前检查

pre-commit hooks 自动执行：
- Black 格式化
- Ruff lint
- 大文件 / 私钥泄露检查

---

## 4. 数据库迁移规范

### 4.1 核心原则

- **禁止手动改库**：所有 Schema 变更通过 Alembic 迁移
- **迁移可回滚**：每个 revision 必须实现 `upgrade()` 与 `downgrade()`
- **禁止删迁移**：已合入的迁移文件不可删除，需回滚则新建反向迁移

### 4.2 操作流程

```bash
# 1. 修改 ORM 模型（app/db/models/）

# 2. 生成迁移脚本（自动对比 metadata 与数据库）
cd backend
alembic revision --autogenerate -m "add xxx table"

# 3. 检查生成的迁移脚本，确认无误后应用
alembic upgrade head

# 4. 回滚到上一版本（如需）
alembic downgrade -1
```

### 4.3 本地运行注意事项

本地（宿主机）运行 alembic 时，数据库地址需指向 `localhost:5433`（Docker 端口映射）：

```powershell
$env:DATABASE__URL="postgresql+asyncpg://maa:maa_secret_pwd@localhost:5433/multi_agent_assistant"
```

容器内运行则用默认地址 `postgres:5432`。

---

## 5. API 设计规范

### 5.1 路由

- 统一前缀 `/api/v1`
- 资源用复数名词：`/api/v1/tasks`、`/api/v1/auth`
- RESTful 动词：GET 查询 / POST 创建 / PUT 更新 / DELETE 删除

### 5.2 统一响应结构

**成功响应**：直接返回数据对象或数组。

**错误响应**（由全局异常处理器统一生成）：

```json
{
  "error_code": "VALIDATION_ERROR",
  "message": "该username已被注册",
  "details": { "conflict_field": "username" }
}
```

### 5.3 错误码

| error_code | status_code | 含义 |
|------------|-------------|------|
| UNAUTHORIZED | 401 | 未登录或令牌失效 |
| FORBIDDEN | 403 | 无权访问该资源 |
| NOT_FOUND | 404 | 资源不存在 |
| VALIDATION_ERROR | 422 | 请求参数校验失败 |
| DAG_CYCLE | 400 | 任务依赖图存在循环 |
| LLM_ERROR | 502 | LLM 调用失败 |
| INTERNAL_ERROR | 500 | 服务内部错误 |

业务层抛 `AppException` 子类，由 `main.py` 全局处理器统一转换为上述结构。

---

## 6. 鉴权与用户隔离规范

### 6.1 JWT 令牌

- **access_token**：业务接口调用凭据，有效期 60 分钟
- **refresh_token**：仅用于换发新 access_token，有效期 7 天，使用后轮换
- **令牌类型校验**：access_token 不能用于 `/auth/refresh`，refresh_token 不能用于业务接口
- **强制下线**：设置 `user.token_invalid_before` 后，早于该时间的令牌立即失效

### 6.2 路由鉴权

- 公开端点（health / auth/register / auth/login / auth/refresh）：无需 token
- 受保护端点：通过 `Depends(get_current_active_user)` 注入当前用户
- 管理员端点：通过 `Depends(get_current_admin_user)` 校验角色

### 6.3 用户隔离（防越权）

**核心约定：所有按 user_id 隔离的资源（Task / Subtask / KnowledgeDoc / HumanFeedback），查询时必须用 `WHERE user_id = :current_user_id` 过滤。**

```python
# 正确：查询时即过滤 user_id
stmt = select(Task).where(Task.user_id == current_user.id, Task.id == task_id)
task = (await db.execute(stmt)).scalar_one_or_none()
if task is None:
    raise NotFoundException("任务不存在")  # 越权访问也返回 404，避免泄漏存在性
```

禁止先查资源再判断 user_id 是否匹配（会通过 403 泄漏资源存在性）。

---

## 7. 测试规范

### 7.1 命名

- 测试文件：`test_<模块>.py`
- 测试函数：`test_<行为>_<条件>()`，如 `test_login_wrong_password_returns_401`

### 7.2 覆盖率

- CI 门禁：每个阶段验收标准见 `dev-plan-v2.md`
- 阶段0：基础骨架，不强制覆盖率
- 阶段1起：覆盖率低于阶段验收标准即 CI 失败

### 7.3 测试数据库

- 单元测试用 SQLite 内存库（`aiosqlite`），通过 `conftest.py` 覆盖 engine
- 集成测试用 Docker Compose 的 PostgreSQL，标记 `@pytest.mark.integration`

### 7.4 运行

```bash
cd backend
# 全量测试
python -m pytest
# 仅单元测试（排除集成测试）
python -m pytest -m "not integration"
```

---

## 8. 配置管理规范

### 8.1 加载优先级

1. `config/default.yaml` 提供默认值
2. `.env` 文件与系统环境变量覆盖（嵌套字段用 `__` 分隔）
3. 代码内默认值（`config.py` 中的 Field default）

### 8.2 环境变量命名

- 顶层标量：全大写下划线，如 `JWT_SECRET_KEY`
- 嵌套字段：用双下划线分隔，如 `DATABASE__URL`（注入到 `settings.database.url`）

> **注意**：`.env.example` 中的 `DATABASE_URL`（单下划线）不会被 pydantic-settings 识别为嵌套字段，应使用 `DATABASE__URL`。容器内依赖代码默认值 `postgres:5432` 运行，此问题将在后续修复。

### 8.3 敏感信息

- 密钥、API Key 等只放 `.env`（已 gitignore），禁止入仓
- `.env.example` 只放占位符

---

## 9. 日志规范

- 使用 `app.core.logging.get_logger(__name__)` 获取 logger
- 结构化 JSON 输出，字段含 `trace_id` / `level` / `event` / `timestamp`
- 请求级追踪：`RequestIDMiddleware` 注入 `trace_id`，全链路共享
- 禁止 `print()`，统一用 logger
- 日志级别：
  - DEBUG：开发调试细节
  - INFO：关键业务节点（任务创建 / Agent 启动 / 迁移应用）
  - WARNING：可恢复异常（校验失败 / 重试触发）
  - ERROR：不可恢复异常（数据库连接失败 / LLM 超时）

---

## 10. 依赖管理规范

### 10.1 Python

- 依赖声明在 `backend/pyproject.toml` 的 `[project.dependencies]`
- 开发依赖在 `[project.optional-dependencies].dev`
- Dockerfile 阶段0仅安装基础运行依赖，RAG/LLM 重依赖在对应阶段按需加入 Dockerfile
- 禁止 `pip install` 不写入 pyproject.toml 的依赖

### 10.2 前端

- 依赖声明在 `frontend/package.json`
- `dependencies` 为运行时依赖，`devDependencies` 为构建/开发工具

---

## 11. CI/CD 门禁

GitHub Actions（`.github/workflows/ci.yml`）在每个 PR 触发：

1. Black 格式检查（--check）
2. Ruff lint
3. Mypy 类型检查
4. 单元测试 + 覆盖率报告

**从阶段1起，覆盖率低于阶段验收标准即视为构建失败。**

---

## 12. 阶段0验收清单

- [x] Docker Compose 五容器一键启动（postgres / redis / chroma / backend / nginx）
- [x] 13 张表 ORM + Alembic 首版迁移已应用
- [x] JWT 鉴权框架（注册 / 登录 / 刷新 / me + 中间件 + 依赖项）
- [x] user_id 隔离约定文档化（见第 6 节）
- [x] Vue3 前端项目初始化 + 前后端联调通过
- [x] Git 仓库 + pre-commit + GitHub Actions CI
- [x] 开发规范文档（本文档）
