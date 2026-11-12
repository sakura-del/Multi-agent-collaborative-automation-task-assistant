# CI / CD 流程（2026-11-12 重构）

## 1. 一句话

**push → 只跑相关测试（3~8 分钟）→ main 绿 → 自动部署到服务器 → 健康检查 + 检索冒烟 → 失败自动回滚**；
全量测试与覆盖率门禁挪到每天 03:00 JST 的夜间任务。

## 2. 为什么改

| 旧流程 | 问题 |
| --- | --- |
| 每个 push 都跑 3209 条用例 | 本地 19 分钟、GitHub 2 核 runner 超过 45 分钟；反馈慢到没人看，真红时反而被忽略 |
| 覆盖率门禁绑在每次 push | 跑部分文件算覆盖率没有意义，只会误杀 |
| 部署靠手工 bundle + 手工 build | 多一步手工、容易漏、没有回滚 |

## 3. 作业图

```
push / PR
  └─ changes（git diff → scripts/ci/select_tests.py）
       ├─ lint            始终跑（black --check / ruff / mypy）
       ├─ test            只跑选中的测试（mode=none 时跳过）
       ├─ migration       Alembic 往返 + 集成用例（后端有改才跑）
       ├─ frontend        Vitest + vue-tsc + build（前端有改才跑）
       ├─ build-check     Dockerfile 可构建（后端/部署有改才跑）
       └─ smoke           Compose 全链路冒烟（后端有改才跑）
  └─ ci-summary（汇总成单一检查名，供分支保护勾选）

main 上的 CI 绿 → deploy.yml（workflow_run）
  → SSH 到服务器：ff-only 更新 → 打 rollback 标签 → 构建 → 启动 → 健康检查 → 检索冒烟
  → 失败：回滚镜像 + git 回到部署前提交 + job 失败 + 通知
```

## 4. 选片规则（`scripts/ci/select_tests.py`）

| 改动 | 跑什么 |
| --- | --- |
| 只有 `docs/**`、`*.md` | **不跑**后端测试（只跑 lint） |
| `backend/app/core/**`、`db/**`、`main.py`、`alembic/**`、`config/**`、`pyproject.toml`、`tests/conftest.py` | **全量**（全局前提，任何用例都可能受影响） |
| `backend/app/api/v1/endpoints/auth*.py` | 认证相关 6 个测试文件 + 冒烟 |
| `backend/app/knowledge/**` | 知识库/向量/嵌入/文档相关测试 + 冒烟 |
| `deploy/**` | 网关头、防火墙、CORS 的锁配置测试 |
| `ops/**` | 监控看门狗 + 防火墙测试 |
| `backend/tests/<x>.py` | 该文件自身 + 冒烟 |
| **其它未映射的 `backend/app/**`** | **全量**（宁可多跑，不可漏跑） |

冒烟集固定为 `tests/test_config.py` + `tests/test_health.py`（约 1 秒）。

**新增模块时要登记映射**：在 `AREA_TESTS` 里加一条（路径 glob → 测试文件 glob）。
不登记也不会漏——会退化成全量，只是变慢。`backend/tests/test_ci_selection.py` 会校验
映射表里每个路径都真实存在（写错 glob 不会静默少跑）。

## 5. 本地怎么用（省时间的关键）

```bash
# 迭代时：只跑本次改动相关的测试（几秒到几十秒）
python scripts/ci/run_selected_tests.py --base origin/main

# 只想看会跑哪些
python scripts/ci/run_selected_tests.py --base origin/main --dry-run

# 提交前：与 CI 完全同名的静态检查（black 是 --check，不是会改文件的 black）
cd backend && black --check app tests evaluation && ruff check app tests evaluation && mypy app evaluation

# 确实需要全量时（约 20 分钟）再跑
cd backend && pytest tests -q --cov=app --cov-fail-under=80
```

## 6. 部署（CD）

### 6.1 触发方式

- **自动**：`CI` 在 `main` 上成功 → `Deploy` 自动执行（`workflow_run`）。
  用 `workflow_run` 而不是 `on: push`，是为了避免"测试还没跑完就开始部署"；
  同时必须用 `github.event.workflow_run.head_sha` 作为部署目标——在 workflow_run 事件里
  `github.sha` 是默认分支的头，不是被测提交。
- **手动**：`gh workflow run deploy.yml`（会部署当前 main 的头）。

### 6.2 服务器上发生了什么

```bash
cd /opt/veripaper
git fetch https://github.com/sakura-del/... main && git merge --ff-only <sha>   # 非快进即停
docker tag veripaper-backend:latest veripaper-backend:rollback                 # 回滚点
docker compose build backend && docker compose up -d backend
# 轮询 /api/v1/health/ready 最多 150 秒，再做一次真实检索
```

失败时：`rollback` 镜像重新打成 `latest` + `up -d` + `git reset --hard <部署前 sha>`。

### 6.3 需要的仓库 secret

| secret | 用途 | 说明 |
| --- | --- | --- |
| `DEPLOY_SSH_KEY` | 服务器登录 | **专用密钥**（`veripaper-cd@github-actions`），不是个人密钥 |
| `DEPLOY_HOST` | `47.253.81.6` | |
| `DEPLOY_USER` | `root` | |
| `WECOM_WEBHOOK_URL` | 部署/夜间结果通知 | 可选；未配置时只写日志 |

轮换部署密钥：本地 `ssh-keygen` 生成新对 → 旧公钥从服务器
`/root/.ssh/authorized_keys` 删掉 → `gh secret set DEPLOY_SSH_KEY < 新私钥`。

> 服务器能匿名 `git fetch` 公开仓库，所以服务器侧不需要 GitHub 凭据。

### 6.4 手工回滚（不依赖 Actions）

```bash
ssh root@47.253.81.6
cd /opt/veripaper/deploy/ecs
docker tag veripaper-backend:rollback veripaper-backend:latest
docker compose up -d backend
git -C /opt/veripaper log --oneline -3     # 确认代码版本，必要时 git reset --hard <旧 sha>
```

`rollback` 标签只保留上一次部署前的镜像；连续多次部署后旧的会被覆盖。

## 7. 这次改动的实测（2026-11-12 18:35 JST）

| 检查点 | 结果 |
| --- | --- |
| 选片：纯文档提交 | `mode=none`（只跑 lint） |
| 选片：只改 auth | `selected`，命中 6 个认证测试 |
| 选片：改 `conftest.py` / `config/**` / 新模块 | `full` |
| 选片器自测 | 19 条通过（含映射表存在性校验，抓到并删掉 2 个写错 glob） |
| 部署密钥 | 独立 ed25519（`veripaper-cd@github-actions`）已加入服务器 `authorized_keys`（现 4 把） |
| CD 手工演练 | 服务器 `b372e7c → 91eda32`：ff-only 合并、镜像重建、`vp-backend` healthy、`/health/ready` 200 |
| 回滚点 | `veripaper-backend:rollback` = 部署前镜像 `c476209d879c`（3 小时前） |

## 8. 已知取舍（不要当成 bug）

1. **选片可能漏判**：新模块、跨模块重构时可能少跑。三层兜底：夜间全量 + 部署后冒烟 +
   映射表存在性测试。真出问题时的修法不是"改回全量"，而是**补映射表**。
2. **纯文档提交也会走一遍部署**：镜像有 Docker 层缓存，通常 1~2 分钟；换来的是
   "main 永远等于线上"。如果以后觉得浪费，可在 CI 里把 `needs_build` 传成 workflow 输出，
   部署时按需跳过构建。
3. **夜间失败不会自动回滚线上**：那时线上跑的是白天验证过的版本，夜间失败只说明
   "有改动影响了没被选中的用例"，需要人来看。

## 9. 附：冒烟/本地栈也改用 pgvector（2026-11-12 同批修）

CI 的 `Compose Release Smoke` 暴露了一个 A 组遗留问题：
根 `docker-compose.yml` 里 `RAG__VECTOR_BACKEND` 仍默认 `chroma`，而 chromadb 已从镜像移除
→ 冒烟在"上传文档"时 500：`chroma 向量后端需要可选依赖`。

已改为与生产一致：
- `postgres` 镜像 → `pgvector/pgvector:pg17`（与 `deploy/ecs` 相同）；
- `RAG__VECTOR_BACKEND` 默认 `pgvector`；
- `RAG__PGVECTOR_DSN` 默认指向 compose 内的 `postgres:5432`，本地无需外部 DSN。

chroma 服务仍保留在 compose 里（想回滚到旧后端时把 `RAG__VECTOR_BACKEND=chroma` 并
`pip install -e '.[chroma]'`），但**不再默认启用**。
