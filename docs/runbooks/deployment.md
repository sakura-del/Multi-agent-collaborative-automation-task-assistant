# 部署手册（Plan 5 Task 8）

> 面向：答辩前部署、交付环境搭建、故障后重建。
> 本文所有命令均在**服务器 `/home/ylin/projects/multi-agent-assistant`** 上验证过（Ubuntu 24.04 + Docker Compose v2）。
> **不要**把真实密钥写进任何被 git 跟踪的文件。

## 1. 前置条件

| 项 | 要求 | 校验命令 |
| --- | --- | --- |
| Docker | ≥ 24，含 compose v2 | `docker compose version` |
| 内存 | ≥ 8 GB（含 Chroma + Postgres + Redis + 后端） | `free -h` |
| 磁盘 | ≥ 20 GB 可用（模型缓存约 2.5 GB，语料约 55 MB） | `df -h /` |
| 端口 | 80（或 override 里的 48080）、35433、36379、38002 未被占用 | `ss -ltn \| grep -E ':(80\|35433\|36379\|38002)\b'` |
| 网络 | 首次构建需拉镜像与依赖；运行期不需要（离线可用） | — |

## 2. 首次部署

### 2.1 获取代码

```bash
cd ~/projects
git clone https://github.com/sakura-del/Multi-agent-collaborative-automation-task-assistant.git multi-agent-assistant
cd multi-agent-assistant
git checkout main    # 或答辩冻结 tag，见 docs/runbooks/defense-demo.md
```

### 2.2 生成配置

```bash
cp .env.example .env
chmod 600 .env

# 必填 1：JWT 密钥（强随机）
python3 -c "import secrets; print('JWT_SECRET_KEY=' + secrets.token_urlsafe(48))" >> /tmp/jwt.txt
# 把 /tmp/jwt.txt 的内容替换进 .env 的 JWT_SECRET_KEY= 行，然后删除 /tmp/jwt.txt

# 必填 2：模型凭证（正式实验模型 gpt-6-luna）
#   在 .env 中设置：
#   LLM__DEFAULT_PROVIDER=openai
#   LLM__DEFAULT_MODEL=gpt-6-luna
#   LLM__OPENAI_API_KEY=<你的密钥>
#   LLM__OPENAI_BASE_URL=<你的兼容端点>
```

> ⚠️ `LLM__OPENAI_API_KEY` 是**嵌套名**（pydantic-settings 用 `__` 映射）；
> 只写扁平 `OPENAI_API_KEY` 不会生效——这一点在 Plan 5 Task 4 踩过坑。

### 2.3 构建并启动

```bash
docker compose build          # 首次约 5–8 分钟（含 torch CPU wheel）
docker compose up -d
```

### 2.4 等待就绪（唯一验收标准）

```bash
for i in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1/api/v1/health/ready)
  echo "ready_probe=$code"; [ "$code" = "200" ] && break; sleep 5
done
curl -s http://127.0.0.1/api/v1/health/ready
```

期望：

```json
{"status":"ready","checks":{"database":"ok","redis":"ok","chroma":"ok"}}
```

容器状态：

```bash
docker compose ps
# maa-postgres / maa-redis / maa-chroma / maa-backend / maa-nginx 均应 healthy 或 running
```

### 2.5 迁移

迁移由 backend 容器 entrypoint 自动执行（`alembic upgrade head`）。手动核对版本：

```bash
docker compose exec backend alembic current
docker compose exec backend alembic heads
# 两者应一致
```

## 3. 宿主机端口覆盖（多项目共存时）

本机已有其他 PostgreSQL/Redis 时，用**未入库**的 `docker-compose.override.yml` 改宿主端口：

```yaml
# docker-compose.override.yml（gitignore，不提交）
services:
  postgres:
    ports: !override
      - "35433:5432"
  redis:
    ports: !override
      - "36379:6379"
  chroma:
    ports: !override
      - "38002:8000"
  nginx:
    ports: !override
      - "48080:80"
```

应用后 `docker compose up -d` 生效，访问 `http://<host>:48080`。

## 4. 数据准备

### 4.1 冻结离线语料（演示必需，一次性）

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,evaluation]"
python scripts/prepare_demo_corpus.py      # 10 篇 PDF + sha256 清单，唯一需要网络的步骤
```

产物：`backend/demo_data/literature_review/{manifest.json, files/}`（PDF 已 gitignore）。

### 4.2 实验语料（跑 120-run 时必需）

```bash
cd backend && source .venv/bin/activate
python -m evaluation.prepare_corpora       # 三领域各 10 篇
python -m evaluation.cli validate          # 期望 runs: 120
```

### 4.3 模型缓存预热（可选，加速首次任务）

```bash
# 若使用真实 BGE 嵌入/重排（RAG_EMBEDDING_MOCK=0），首次会下载约 2.5 GB
docker compose exec backend python -c "from app.knowledge.services.factory import init_kb; init_kb()"
```

默认 `RAG_EMBEDDING_MOCK=1`（Mock 嵌入，自包含、离线、快），演示与冒烟都用它。

## 5. 验收检查（部署完成的完整判定）

按顺序执行，任一失败即视为未部署成功：

```bash
# 1) 就绪
curl -s http://127.0.0.1/api/v1/health/ready

# 2) 前端可达
curl -s -o /dev/null -w "frontend=%{http_code}\n" http://127.0.0.1/

# 3) 六条任务路由存在（401 = 路由已挂载且要求鉴权，404 = 镜像过期）
curl -s -o /dev/null -w "tasks=%{http_code}\n" http://127.0.0.1/api/v1/tasks

# 4) 知识库路由存在
curl -s -o /dev/null -w "knowledge=%{http_code}\n" http://127.0.0.1/api/v1/knowledge/documents

# 5) 容器内模型连通（不消耗额度的最小探测）
docker compose exec backend python -c "
import asyncio
from app.llm.factory import get_llm_manager
from app.llm.models import Message, MessageRole
async def main():
    r = await get_llm_manager().chat([Message(role=MessageRole.USER, content='ping')], max_tokens=4)
    print('LLM_OK', (r.content or '').strip()[:20])
asyncio.run(main())"

# 6) 发布冒烟（隔离栈，不影响生产数据）
./scripts/smoke.sh    # 期望结尾 SMOKE OK
```

## 6. 备份

| 数据 | 位置 | 备份命令 |
| --- | --- | --- |
| PostgreSQL | 命名卷 `maa-postgres-data` | `docker compose exec -T postgres pg_dump -U maa multi_agent_assistant > backup_$(date +%F).sql` |
| 上传文档 | 命名卷 `maa-uploads-data` | `docker run --rm -v maa-uploads-data:/data -v $PWD:/backup alpine tar czf /backup/uploads_$(date +%F).tgz -C /data .` |
| 任务产物 | 命名卷 `maa-artifacts-data` | 同上，卷名换 `maa-artifacts-data`，文件名换 `artifacts_*.tgz` |
| 实验运行结果 | `backend/evaluation/runs/` | `tar czf runs_$(date +%F).tgz backend/evaluation/runs`（若跑过实验） |
| 配置 | `.env` | 单独安全保存（**含密钥，不入仓库**） |

## 7. 停止与清理

```bash
docker compose stop            # 停止但保留容器与数据
docker compose down            # 删除容器与网络，保留命名卷（数据安全）
docker compose down --volumes  # ⚠️ 会删除所有数据卷，仅用于彻底重置
```

> 生产数据在**命名卷**里；`down` 不会删数据，`down --volumes` 会。冒烟栈用独立 project（`maa-test`）与独立卷名，互不影响。

## 8. 更新到新版本

```bash
cd ~/projects/multi-agent-assistant
git fetch && git checkout <tag 或 commit>
docker compose build
docker compose up -d
# 核对迁移与就绪
docker compose exec backend alembic current
curl -s http://127.0.0.1/api/v1/health/ready
```

## 9. 常见问题

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `ready` 一直 502 | backend 启动中或崩溃 | `docker compose logs backend --tail 120` |
| `POST /tasks` 返回 404 | 后端镜像过期（旧镜像无该路由） | `docker compose build backend && docker compose up -d backend` |
| 文献任务停在 `waiting_human` 且 error 提到 401 | 模型密钥缺失/无效 | 检查 `.env` 的 `LLM__OPENAI_API_KEY` |
| 任务无证据/无引用 | 容器未接知识库检索（旧镜像） | 同上一行重建镜像；确认 `main.lifespan` 里 `knowledge_query` 已注入 |
| `Bind for 0.0.0.0:xxxx failed` | 宿主端口被占用 | 用 `docker-compose.override.yml` 改端口 |
| 冒烟 `SMOKE FAILED` | 见脚本打印的服务日志 | 按日志定位；冒烟栈可用 `KEEP=1 ./scripts/smoke.sh` 保留排查 |
