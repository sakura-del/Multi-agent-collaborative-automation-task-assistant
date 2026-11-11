# ECS Real Embeddings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在受控维护窗口内把 VeriPaper ECS 生产检索从 Mock 嵌入切到 `BAAI/bge-large-zh`，持久化模型缓存、全量重建 1024 维向量，并以真实检索和一致性证据验收。

**Architecture:** 复用现有 SentenceTransformers 嵌入服务、CPU 版 RAG 镜像依赖和按原 chunk ID 重建脚本。新镜像构建与旧服务并行阶段只允许「旧容器运行、新镜像构建」这一对操作；备份、预热、样本重建和全量重建全部串行。维护期间停止 backend，避免混合语义空间对外服务；重建容器限制 1 CPU / 2500 MB，并设置 `OMP_NUM_THREADS=1`。

**Tech Stack:** Docker Compose、FastAPI、SentenceTransformers、PyTorch CPU、PostgreSQL 17 + pgvector、Redis、Python pytest、阿里云 ECS。

---

## 文件结构

- `deploy/ecs/docker-compose.yml`：真实模型构建默认值、HF 缓存位置、CPU 线程上限与模型缓存卷。
- `deploy/ecs/env.example`：真实嵌入部署模板，重排仍为 Mock。
- `docs/runbooks/ecs-deployment.md`：可重复执行的切换、负载保护、验收与回滚手册；只写实测数字。
- `docs/runbooks/2026-11-11-handoff.md`：最终线上状态与后续待办。
- `docs/superpowers/plans/2026-08-09-00-execution-notes.md`：本次命令结果、耗时、偏差和证据。
- 本计划：执行顺序与检查点。

不修改 `backend/app/`。若实际故障迫使修改应用代码，停止本计划的配置路径，先为该缺陷写并运行失败测试。

## Task 1: ECS 真实模型与缓存配置

**Files:**
- Modify: `deploy/ecs/docker-compose.yml`
- Modify: `deploy/ecs/env.example`

- [ ] **Step 1: 修改 ECS backend 构建与运行配置**

`INSTALL_RAG_MODELS` 默认值改为 `true`；backend 增加以下环境变量与卷，其他服务不变：

```yaml
    environment:
      HF_HOME: /app/.cache
      OMP_NUM_THREADS: "1"
    volumes:
      - uploads:/app/data/uploads
      - artifacts:/app/data/artifacts
      - model_cache:/app/.cache
```

顶层卷列表改为：

```yaml
volumes:
  pgdata:
  redisdata:
  uploads:
  artifacts:
  model_cache:
```

- [ ] **Step 2: 修改环境模板**

`deploy/ecs/env.example` 的嵌入段必须是：

```dotenv
# 真实 BGE 嵌入；重排仍用 Mock，模型权重持久化在 model_cache 卷
RAG_EMBEDDING_MOCK=0
RAG_RERANKER_MOCK=1
INSTALL_RAG_MODELS=true
```

- [ ] **Step 3: 检查差异和空白错误**

Run:

```powershell
git diff --check -- deploy/ecs/docker-compose.yml deploy/ecs/env.example
git diff -- deploy/ecs/docker-compose.yml deploy/ecs/env.example
```

Expected: 无空白错误；diff 只包含构建默认值、`HF_HOME`、`OMP_NUM_THREADS`、缓存卷和三项模板值，不包含服务器凭据。

## Task 2: 回归检查并提交部署配置

**Files:**
- Test: `backend/tests/test_embedding.py`
- Test: `backend/tests/test_reindex_vectors.py`
- Test: `backend/tests/test_reindex_vector_index_sync.py`
- Test: `backend/tests/test_pgvector_backend_unit.py`
- Test: `backend/tests/test_vector_consistency.py`
- Test: `backend/tests/test_vector_consistency_ids.py`

- [ ] **Step 1: 运行既有聚焦测试**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_embedding.py tests/test_reindex_vectors.py tests/test_reindex_vector_index_sync.py tests/test_pgvector_backend_unit.py tests/test_vector_consistency.py tests/test_vector_consistency_ids.py -q --basetemp="$env:TEMP\pytest-real-embeddings"
```

Expected: 全部通过；这里没有应用代码改动，不增加只扫描 YAML 字符串的测试。

- [ ] **Step 2: 提交精确路径**

Run:

```powershell
git status --short
git add -- deploy/ecs/docker-compose.yml deploy/ecs/env.example
git commit -m "feat: enable real embeddings on ecs"
```

Expected: 提交只有这两个部署文件；qrels、论文、`.agents`、`.codebuddy` 保持未跟踪且未进入提交。

## Task 3: 同步 ECS、记录资源基线并构建镜像

**Files:**
- Deploy: `deploy/ecs/docker-compose.yml`
- Deploy: `deploy/ecs/env.example`

- [ ] **Step 1: 打包并同步当前 main**

Run locally:

```powershell
$b = Join-Path $env:TEMP 'veripaper-real-embeddings.bundle'
git bundle create $b main
scp $b root@47.253.81.6:/root/veripaper-real-embeddings.bundle
```

服务器端把下列内容保存为 LF 的 `/root/sync-real-embeddings.sh` 后用 `bash` 执行；不要把带引号的 refspec 塞进交互式 zsh：

```bash
#!/usr/bin/env bash
set -euo pipefail
cd /opt/veripaper
git fetch /root/veripaper-real-embeddings.bundle '+refs/heads/*:refs/remotes/win/*'
git merge --ff-only win/main
git log --oneline -3
```

Expected: 服务器 HEAD 包含 Task 2 提交；若 SSH 仍在认证前被关闭，先查本机路由/22 端口与 SSH 日志，不重复原样重试。

- [ ] **Step 2: 核验 compose 渲染且不回显凭据**

Run on ECS:

```bash
cd /opt/veripaper
docker compose -f deploy/ecs/docker-compose.yml config --quiet
docker compose -f deploy/ecs/docker-compose.yml config | grep -E 'HF_HOME:|OMP_NUM_THREADS:|model_cache|INSTALL_RAG_MODELS'
```

Expected: 退出码 0；可见 `/app/.cache`、`OMP_NUM_THREADS: "1"`、`model_cache` 与真实模型构建参数。不要完整打印含 DSN/密钥的渲染结果。

- [ ] **Step 3: 记录健康和资源基线**

Run on ECS:

```bash
cd /opt/veripaper
nproc
uptime
free -m
df -h / /var/lib/docker
docker ps --format '{{.Names}} {{.Status}}'
docker stats --no-stream
curl -fsS http://127.0.0.1/api/v1/health/ready
```

Expected: 五类既有容器符合上线基线，health 为 ready；根盘可用至少 10 GB，`MemAvailable` 至少 800 MB。未达阈值就停止，不构建。

- [ ] **Step 4: 保留旧镜像标签**

Run on ECS:

```bash
old_image="$(docker inspect vp-backend --format '{{.Image}}')"
docker tag "$old_image" veripaper-backend:mock-pre-bge
docker image inspect veripaper-backend:mock-pre-bge --format '{{.Id}}'
```

Expected: 输出镜像 ID 与 `vp-backend` 当前镜像一致。

- [ ] **Step 5: 后台构建真实模型镜像**

Run on ECS:

```bash
cd /opt/veripaper
nohup docker compose -f deploy/ecs/docker-compose.yml build --build-arg INSTALL_RAG_MODELS=true backend >/tmp/vp-bge-build.log 2>&1 &
echo $! >/tmp/vp-bge-build.pid
```

Expected: PID 写入文件；旧 `vp-backend` 保持运行。构建期间不得启动备份、reindex、测试矩阵或批量导入。

- [ ] **Step 6: 每 2–5 分钟检查构建与负载**

Run on ECS:

```bash
pid="$(cat /tmp/vp-bge-build.pid)"
ps -p "$pid" -o pid,etime,stat,cmd
uptime
free -m
df -h /
docker stats --no-stream
tail -n 20 /tmp/vp-bge-build.log
```

Expected: 构建最终成功。若 1 分钟 load 连续三次高于 `2 × nproc`，或 `MemAvailable < 500 MB`，执行 `kill -TERM "$pid"` 并确认构建进程消失；保留缓存，等资源恢复后续建。不得原样并发启动第二个构建。

## Task 4: 备份、维护、真实模型预热与小样本

**Files:**
- Modify on ECS only: `/opt/veripaper/.env`（600，不提交、不打印）

- [ ] **Step 1: 串行执行一次备份**

Run on ECS:

```bash
before="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
nohup docker exec -e RUN_ONCE=1 vp-db-backup /usr/local/bin/backup.sh >/tmp/vp-pre-bge-backup.log 2>&1 &
echo $! >/tmp/vp-pre-bge-backup.pid
printf '%s\n' "$before" >/tmp/vp-pre-bge-backup.started
```

Expected: 备份在镜像构建结束后才开始。每 2–5 分钟查看日志与负载，最终日志必须出现新的 `已上传 s3://.../<UTC 时间>.sql.gz`；对象时间不得早于 `vp-pre-bge-backup.started`。只看到 dump 完成不算备份成功。

- [ ] **Step 2: 停止 backend 并调整生产开关**

Run on ECS:

```bash
cd /opt/veripaper
docker compose -f deploy/ecs/docker-compose.yml stop backend
cp --preserve=mode /opt/veripaper/.env /opt/veripaper/.env.pre-bge
```

再用服务器 Python 3 执行以下精确更新，禁止回显文件内容：

```python
from pathlib import Path
import os

path = Path("/opt/veripaper/.env")
updates = {"RAG_EMBEDDING_MOCK": "0", "INSTALL_RAG_MODELS": "true"}
lines = path.read_text(encoding="utf-8").splitlines()
seen = set()
out = []
for line in lines:
    key = line.split("=", 1)[0].strip() if "=" in line else ""
    if key in updates:
        out.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        out.append(line)
for key, value in updates.items():
    if key not in seen:
        out.append(f"{key}={value}")
path.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
os.chmod(path, 0o600)
```

Run `free -m`；只有 `MemAvailable >= 2200 MB` 才继续。维护期间不得启动旧 backend。

- [ ] **Step 3: 预热并断言真实向量可用**

Run on ECS as one LF script：

```bash
docker run --rm --cpus=1 --memory=2500m \
  --network veripaper_default \
  --env-file /opt/veripaper/.env \
  -e HF_HOME=/app/.cache -e OMP_NUM_THREADS=1 \
  -v veripaper_model_cache:/app/.cache \
  --entrypoint python veripaper-backend:latest -c '
import math
from app.knowledge.services.embedding import create_embedding_service, SentenceTransformerEmbeddingService
svc = create_embedding_service()
assert isinstance(svc, SentenceTransformerEmbeddingService), type(svc)
vector = svc.embed_one("VeriPaper 中文语义检索模型预热")
assert len(vector) == 1024
assert any(value != 0 for value in vector)
assert all(math.isfinite(value) for value in vector)
print({"service": type(svc).__name__, "model": svc._model_name, "dimension": len(vector)})
'
```

Expected: 输出真实服务类、`BAAI/bge-large-zh` 和 1024；模型下载只发生在此串行步骤。OOM、超时或 ImportError 都停止计划，不放大内存限制硬闯。

- [ ] **Step 4: dry-run 获取现场规模**

Run on ECS:

```bash
docker run --rm --cpus=1 --memory=2500m \
  --network veripaper_default \
  --env-file /opt/veripaper/.env \
  -e HF_HOME=/app/.cache -e OMP_NUM_THREADS=1 \
  -v veripaper_model_cache:/app/.cache \
  --entrypoint python veripaper-backend:latest \
  -m scripts.reindex_vectors --dry-run --json
```

Expected: `indexed=0`、`failed_batches=0`；把现场 `planned` 写入执行记录，历史基线是 28,341，但不替代现场值。

- [ ] **Step 5: 重建 32 条小样本**

Run on ECS:

```bash
docker run --rm --cpus=1 --memory=2500m \
  --network veripaper_default \
  --env-file /opt/veripaper/.env \
  -e HF_HOME=/app/.cache -e OMP_NUM_THREADS=1 \
  -v veripaper_model_cache:/app/.cache \
  --entrypoint python veripaper-backend:latest \
  -m scripts.reindex_vectors --limit 32 --batch-size 32 --json
```

Expected: 现场规模至少 32 时，`planned=32`、`indexed=32`、`failed_batches=0`；任一失败批次都停止，保持维护状态。

## Task 5: 限速全量重建与一致性检查

**Files:**
- Runtime only: `/tmp/vp-bge-reindex.log`
- Runtime only: `/tmp/vp-bge-reindex.pid`

- [ ] **Step 1: 启动唯一一个全量重建进程**

Run on ECS:

```bash
nohup docker run --rm --name vp-reindex-bge --cpus=1 --memory=2500m \
  --network veripaper_default \
  --env-file /opt/veripaper/.env \
  -e HF_HOME=/app/.cache -e OMP_NUM_THREADS=1 \
  -v veripaper_model_cache:/app/.cache \
  --entrypoint python veripaper-backend:latest \
  -m scripts.reindex_vectors --batch-size 100 --json >/tmp/vp-bge-reindex.log 2>&1 &
echo $! >/tmp/vp-bge-reindex.pid
```

Expected: 只存在一个名为 `vp-reindex-bge` 的容器；不得同时运行构建、备份或其他重建。

- [ ] **Step 2: 每 2–5 分钟检查进度和主机资源**

Run on ECS:

```bash
docker ps --filter name=vp-reindex-bge --format '{{.Names}} {{.Status}}'
uptime
free -m
df -h /
docker stats --no-stream vp-reindex-bge vp-postgres vp-redis
tail -n 30 /tmp/vp-bge-reindex.log
```

Expected: 进度持续增长，资源有余量。若 1 分钟 load 连续三次高于 `2 × nproc`、`MemAvailable < 300 MB` 或根盘可用低于 3 GB，执行 `docker stop vp-reindex-bge` 并保持维护；资源恢复后重新运行全量命令，不并行补跑。

- [ ] **Step 3: 核验全量结果**

Run `tail -n 60 /tmp/vp-bge-reindex.log` after the container exits.

Expected: `indexed == planned`、`failed_batches=0`、`errors=[]`；记录 `elapsed_ms`。容器消失但没有完整成功 JSON 也算失败，不启动 backend。

- [ ] **Step 4: 检查数量与 ID 双向一致性**

Run on ECS:

```bash
docker run --rm --cpus=1 --memory=2500m \
  --network veripaper_default \
  --env-file /opt/veripaper/.env \
  -e HF_HOME=/app/.cache -e OMP_NUM_THREADS=1 \
  -v veripaper_model_cache:/app/.cache \
  --entrypoint python veripaper-backend:latest \
  -m scripts.check_kb_consistency --json
```

Expected: `consistent=true`、`missing_vectors=0`、`orphan_vectors=0`，且 ID 已验证；若不一致，继续维护并按报告处理，不能仅凭行数相等通过。

## Task 6: 清缓存、恢复服务与真实检索验收

**Files:**
- Runtime only: Redis `rag:retrieval:*` 键
- Runtime only: ECS backend 容器

- [ ] **Step 1: 定向清除旧检索缓存**

Run on ECS:

```bash
docker run --rm --network veripaper_default --env-file /opt/veripaper/.env \
  --entrypoint python veripaper-backend:latest -c '
import os
import redis
client = redis.Redis.from_url(os.environ["REDIS__URL"])
deleted = 0
cursor = 0
while True:
    cursor, keys = client.scan(cursor=cursor, match="rag:retrieval:*", count=500)
    if keys:
        deleted += client.delete(*keys)
    if cursor == 0:
        break
remaining = sum(1 for _ in client.scan_iter(match="rag:retrieval:*", count=500))
print({"deleted": deleted, "remaining": remaining})
assert remaining == 0
'
```

Expected: `remaining=0`。禁止 `FLUSHDB`；`quota:*`、限流和会话相关键不动。

- [ ] **Step 2: 用真实嵌入启动新 backend**

Run on ECS:

```bash
cd /opt/veripaper
docker compose -f deploy/ecs/docker-compose.yml up -d --no-build backend
docker compose -f deploy/ecs/docker-compose.yml ps backend
```

Expected: `vp-backend` healthy；启动日志的 `knowledge_base_initialized` 显示 `SentenceTransformerEmbeddingService`，不是 Mock。

- [ ] **Step 3: 验证持久缓存并重建 backend 容器**

先记录模型缓存文件数量，再强制重建 backend：

```bash
docker run --rm -v veripaper_model_cache:/app/.cache \
  --entrypoint sh veripaper-backend:latest -c 'find /app/.cache -type f | wc -l'
cd /opt/veripaper
docker compose -f deploy/ecs/docker-compose.yml up -d --no-build --force-recreate backend
docker compose -f deploy/ecs/docker-compose.yml ps backend
```

待 healthy 后执行离线预热：

```bash
docker run --rm --cpus=1 --memory=2500m \
  --network veripaper_default \
  --env-file /opt/veripaper/.env \
  -e HF_HOME=/app/.cache -e OMP_NUM_THREADS=1 -e HF_HUB_OFFLINE=1 \
  -v veripaper_model_cache:/app/.cache \
  --entrypoint python veripaper-backend:latest -c '
import math
from app.knowledge.services.embedding import create_embedding_service, SentenceTransformerEmbeddingService
svc = create_embedding_service()
assert isinstance(svc, SentenceTransformerEmbeddingService), type(svc)
vector = svc.embed_one("VeriPaper 离线模型缓存验证")
assert len(vector) == 1024
assert any(value != 0 for value in vector)
assert all(math.isfinite(value) for value in vector)
print({"service": type(svc).__name__, "model": svc._model_name, "dimension": len(vector), "offline": True})
'
```

Expected: 离线仍输出 1024 维真实向量，证明缓存可独立支撑新容器；随后再次确认 backend healthy。

- [ ] **Step 4: 选择真实文档并执行非逐字语义查询**

用 Python `httpx`（不用 PowerShell 解析业务 JSON）。把交接文档里的 demo 密码只放进当前进程环境变量 `VP_DEMO_PASSWORD`，不写入脚本或命令历史。先登录并列出 ready 文档，打印 `id/title`，不打印 token：

```python
import os
import httpx

client = httpx.Client(base_url="https://api.veripaper.me/api/v1", timeout=60)
login = client.post(
    "/auth/login",
    json={"account": "demo", "password": os.environ["VP_DEMO_PASSWORD"]},
)
login.raise_for_status()
access_token = login.json()["access_token"]
headers = {"Authorization": f"Bearer {access_token}"}
documents = client.get("/knowledge/documents", headers=headers)
documents.raise_for_status()
for doc in documents.json()["documents"]:
    if doc["index_status"] == "ready":
        print({"id": doc["id"], "title": doc["title"], "chunks": doc["chunk_count"]})
```

选一个主题明确的文档，把其 ID 放入当前进程 `VP_EXPECTED_DOC_ID`，把不复制标题的自然语言中文查询放入 `VP_QUERY`，再执行：

```python
import os
import uuid

query = os.environ["VP_QUERY"]
expected_doc_id = os.environ["VP_EXPECTED_DOC_ID"]
response = client.post(
    "/knowledge/search",
    json={"query": query, "top_k": 5, "skip_cache": True, "skip_rerank": True},
    headers=headers,
)
response.raise_for_status()
results = response.json()["results"]
print({"query": query, "doc_ids": [item["doc_id"] for item in results]})
assert expected_doc_id in [item["doc_id"] for item in results]
for item in results:
    uuid.UUID(item["doc_id"])
    uuid.UUID(item["chunk_id"])
```

Expected: 目标 `doc_id` 在 top 5，所有 `chunk_id/doc_id` 是可回溯 UUID。当前 endpoint 会把 `skip_cache` 丢失，因此缓存必须在 Step 1 已清空，且查询必须是本轮新查询；不得靠额外触发制造通过。另执行一条英文语义查询并如实记录结果，不预设 BGE 中文模型一定满足英文目标。

- [ ] **Step 5: 公网验收**

Run locally:

```powershell
curl.exe -fsS https://api.veripaper.me/api/v1/health/ready
curl.exe -fsS -o NUL -w "%{http_code}" https://veripaper.me
```

Expected: API 为 ready，database/redis/vector 全 ok；frontend 为 200。再从公网 API 执行一次知识库检索，结果与 ECS 内一致。

- [ ] **Step 6: 最终负载与容器检查**

Run on ECS:

```bash
uptime
free -m
df -h /
docker compose -f deploy/ecs/docker-compose.yml ps
docker stats --no-stream
```

Expected: 无重建/构建遗留进程，容器健康，负载回落至基线附近；若持续高负载，保持 `OMP_NUM_THREADS=1`，先定位真实进程，不重启其他项目容器。

## Task 7: 回填证据、提交文档与备份

**Files:**
- Modify: `docs/runbooks/ecs-deployment.md`
- Modify: `docs/runbooks/2026-11-11-handoff.md`
- Modify: `docs/superpowers/plans/2026-08-09-00-execution-notes.md`
- Modify: `docs/superpowers/plans/2026-11-11-real-embeddings.md`

- [ ] **Step 1: 写可重复执行的 runbook**

在 `ecs-deployment.md` 增加「真实 BGE 嵌入」一节，完整记录本计划的串行顺序、1 CPU/2500 MB/单线程限制、负载阈值、缓存清理、验收与 Mock 全量重建回滚。实测表必须逐项来自输出：现场 planned/indexed、失败批次、耗时、一致性、模型类/维度、中文与英文查询、health、容器重建后的离线缓存结果。未知或失败项如实写，不能预填 28,341 为最终实测值。

- [ ] **Step 2: 更新交接与执行记录**

只有生产验收全部通过后，才把 handoff 待办 #1 标为完成并记录真实模型、缓存卷、重建规模和耗时；未完成则写明阻塞，不改状态。execution-notes 记录 SSH、依赖下载、资源限制或重试的偏差。勾选本计划已实际完成的步骤，不把「已启动」勾成「已完成」。

- [ ] **Step 3: 检查并提交文档**

Run:

```powershell
git diff --check -- docs/runbooks/ecs-deployment.md docs/runbooks/2026-11-11-handoff.md docs/superpowers/plans/2026-08-09-00-execution-notes.md docs/superpowers/plans/2026-11-11-real-embeddings.md
git status --short
git add -- docs/runbooks/ecs-deployment.md docs/runbooks/2026-11-11-handoff.md docs/superpowers/plans/2026-08-09-00-execution-notes.md docs/superpowers/plans/2026-11-11-real-embeddings.md
git commit -m "docs: record ecs real embedding cutover"
```

Expected: 只提交本任务文档；用户并行的 qrels、论文和工具目录不进入提交。

- [ ] **Step 4: 推送备份**

Run:

```powershell
git push backup main
```

若直连失败，按仓库约定从注册表读取实际代理端口并只对本次命令设置 `http.proxy/https.proxy`。Expected: backup/main 包含配置与最终证据提交；服务器生产 HEAD 与本地 main 一致。
