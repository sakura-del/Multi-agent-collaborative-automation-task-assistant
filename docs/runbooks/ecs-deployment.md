# ECS 部署（后端 + 数据库，美东）

> **状态：已上线（2026-11-10）。** 后端与数据库运行在阿里云 ECS
> `47.253.81.6`（Ubuntu 24.04 / 2C / 3.6G / 40G，已配 4G swap），
> 公网入口 **https://api.veripaper.me**（Cloudflare 橙云 + Origin 证书 + **Full (strict)**）；
> 前端仍在 Cloudflare Pages（`multi-agent-assistant.pages.dev`）。

## 1. 架构

```
浏览器 ──► Pages（静态）──跨域 XHR / wss──┐
                                          ▼
                    Cloudflare 代理 api.veripaper.me（Full strict）
                                          ▼
                          ECS 47.253.81.6:443  vp-gateway（nginx）
                                          ▼
                          vp-backend（FastAPI:8000，仅容器网络）
                              ├─ vp-postgres  pgvector/pgvector:pg17
                              ├─ vp-redis     redis:7-alpine
                              └─ 七牛云 Kodo（文档对象，华南 cn-south-1）
```

数据库与后端**同机**，接口延迟从「每次查询 ~0.48s 的国际往返」变成进程内网：

| 接口 | 旧（国内服务器 → Supabase 新加坡） | 新（ECS 同机） |
| --- | --- | --- |
| `GET /api/v1/tasks` | 4.2 s | **0.027 s**（经 Cloudflare 从国内访问约 0.33 s） |
| `POST /api/v1/knowledge/search` | 6.2 s | **0.054 s**（同上约 0.33 s） |

## 2. 目录与编排

| 路径 | 说明 |
| --- | --- |
| `/opt/veripaper` | 代码（`git bundle` 部署，非 git 远端） |
| `/opt/veripaper/.env` | 全部配置（600），字段见 `deploy/ecs/env.example` 与下文 |
| `/opt/veripaper/certs/{cert,key}.pem` | Cloudflare **Origin** 证书（600，2041 年到期） |
| `/opt/veripaper/backup/` | Supabase dump 归档（迁移留档） |
| `deploy/ecs/docker-compose.yml` | **standalone 编排**（不用 override，见下文「坑」） |
| `deploy/ecs/nginx-api-tls.conf` | 网关配置（80+443 双监听，均反代 backend） |

启动/更新：

```bash
cd /opt/veripaper
docker compose -f deploy/ecs/docker-compose.yml up -d postgres redis
docker compose -f deploy/ecs/docker-compose.yml up -d backend
docker compose -f deploy/ecs/docker-compose.yml up -d gateway
docker compose -f deploy/ecs/docker-compose.yml ps
```

> compose 的**项目目录是 `deploy/ecs/`**，它只会从该目录读 `.env`。
> 仓库根 `.env` 通过 `deploy/ecs/.env -> ../../.env` 软链暴露（已建，勿删），
> 否则 `docker compose config` 会报 `POSTGRES_USER 必填`。

## 3. 关键配置（`.env`，600）

```dotenv
POSTGRES_USER=veripaper
POSTGRES_PASSWORD=<随机 32 位>
POSTGRES_DB=veripaper

DATABASE__URL=postgresql+asyncpg://veripaper:<pwd>@postgres:5432/veripaper
REDIS__URL=redis://redis:6379/0
RAG__VECTOR_BACKEND=pgvector
RAG__PGVECTOR_DSN=postgresql://veripaper:<pwd>@postgres:5432/veripaper
# 本机直连（不是 pgbouncer），无需关闭 prepared statement
RAG__PGVECTOR_DISABLE_PREPARED_STATEMENTS=false

RAG_EMBEDDING_MOCK=1          # 镜像未装 torch（INSTALL_RAG_MODELS=false）
RAG_RERANKER_MOCK=1
INSTALL_RAG_MODELS=false
JWT_SECRET_KEY=<随机 64 位>

STORAGE__BACKEND=s3
STORAGE__S3_ENDPOINT=https://s3-cn-south-1.qiniucs.com
STORAGE__S3_REGION=cn-south-1
STORAGE__S3_BUCKET=veripaper-storage
STORAGE__S3_ACCESS_KEY_ID=<AK>
STORAGE__S3_SECRET_ACCESS_KEY=<SK>
STORAGE__S3_PREFIX=uploads
STORAGE__S3_ADDRESSING_STYLE=virtual
STORAGE__PRESIGN_EXPIRES_SECONDS=900

CORS__ALLOW_ORIGIN_REGEX=^https://([a-z0-9-]+\.)?multi-agent-assistant\.pages\.dev$

# 仅回滚用：源库 DSN（迁移脚本读它）
SUPABASE_PG_HOST=aws-0-ap-southeast-1.pooler.supabase.com
SUPABASE_PG_USER=postgres.<ref>
SUPABASE_PG_PASSWORD=<pwd>
```

> ⚠️ **不要在 `.env` 的值里写正则括号而用 `source .env` 读**：`CORS__ALLOW_ORIGIN_REGEX=^https://(...)$`
> 会让 bash 报语法错误。脚本里用 `grep | cut` 取键值（见 `migrate-from-supabase.sh` 的 `get_env`）。
> docker compose 的 env_file 不受影响。

## 4. 数据迁移（Supabase → ECS）

```bash
bash deploy/ecs/migrate-from-supabase.sh          # 完整：dump → 恢复 → 校验
SKIP_DUMP=1 bash deploy/ecs/migrate-from-supabase.sh   # 复用已有 dump 只恢复
```

2026-11-10 实测结果：

| 项 | 值 |
| --- | --- |
| dump | 21 MB（`--schema=public`，PG17 客户端连 session pooler 5432） |
| 恢复后库大小 | **250 MB** |
| 行数 | user 27 / task 101 / subtask 673 / execution_log 3984 / validation_result 629 / knowledge_doc 101 / doc_chunk 28341 / vector_index 28341 / knowledge_vectors 28341 |
| alembic | `d5a8c1e3f7b2` |
| 向量 id 双向差 | **0 / 0** |

### 4.1 回填历史文件（一次性）

早期文档的 `file_path` 是**旧服务器上的本地绝对路径**，对象存储里没有对应文件，
表现为「下载 307 后直链 404」。处理方式（已完成）：

1. 在旧服务器把 `.runtime/uploads` 与容器卷 `uploads` 里的文件按
   `uploads/<user>/<file>` 补传到七牛（90 + 37 个文件，319 MB）；
2. 在 ECS 执行：
   ```sql
   UPDATE knowledge_doc
      SET file_path = regexp_replace(file_path, '^.*/uploads/', 'uploads/')
    WHERE file_path LIKE '/%';
   ```

## 5. 证书与 Cloudflare

- 证书来源：Cloudflare → SSL/TLS → Origin Server → Create Certificate
  （有效期至 2041-11-06），放 `/opt/veripaper/certs/`，`chmod 600`；
- 网关配置 `nginx-api-tls.conf` 的 **80 不做 301**，直接反代。
  这样 CF 用 Flexible 还是 Full(strict) 都不会 522，切换顺序不敏感；
- 切 Full (strict)：Dashboard → SSL/TLS → Overview → Configure → Full (strict)。
- 轮换证书：替换 `certs/*.pem` 后 `docker compose -f deploy/ecs/docker-compose.yml restart gateway`。

## 6. 已知坑（本轮全部踩过）

| 坑 | 现象 | 处理 |
| --- | --- | --- |
| `urlencode` 把空格编成 `+` | `FATAL: unrecognized configuration parameter "+statement_timeout"`，health `vector=failed` | 代码修复 `quote_via=quote`（提交 `3a0618d`） |
| pgvector 在 `extensions` schema | `operator does not exist: extensions.vector <=> extensions.vector` | `ALTER DATABASE veripaper SET search_path TO "$user", public, extensions;` 并重启 backend |
| PG17 dump 自带 `CREATE SCHEMA public;` | 恢复时报 `schema "public" already exists` | 脚本流式 `sed` 过滤该行后再灌 |
| compose 项目目录 | `POSTGRES_USER 必填`（根 `.env` 没被读到） | `deploy/ecs/.env` 软链到根 `.env` |
| 长 SSH 会话 | 60 秒左右被对端断开 | 长任务一律 `nohup ... &`，短连接轮询 |
| Windows→服务器脚本 CRLF | `sed 's/\r$//'` 经多层转义会把行尾 `r` 吃掉（`veripaper`→`veipape`） | 用 base64 传脚本，或 `tr -d '\r'` |
| 社区备份镜像无 PG17 | `pg_dump 16` 对 17 服务端版本不匹配 | 用 `postgres:17` 容器导出（脚本已内置） |

## 7. 回滚

**A. 只回滚应用（最快，1 分钟）**：数据库保持 ECS，把前端构建指回旧入口即可。

**B. 整库回滚到 Supabase**：

```bash
cd /opt/veripaper
sed -i 's#^DATABASE__URL=.*#DATABASE__URL=postgresql+asyncpg://postgres.<ref>:<pwd>@aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres?ssl=require#' .env
sed -i 's#^RAG__PGVECTOR_DSN=.*#RAG__PGVECTOR_DSN=postgresql://postgres.<ref>:<pwd>@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres?sslmode=require#' .env
# 还需要把 disable_prepared_statements 改回 true（transaction pooler）
sed -i 's#^RAG__PGVECTOR_DISABLE_PREPARED_STATEMENTS=.*#RAG__PGVECTOR_DISABLE_PREPARED_STATEMENTS=true#' .env
docker compose -f deploy/ecs/docker-compose.yml up -d backend
```

Supabase 项目保持原样（未删），迁移脚本不改源库；`backup/` 下的 dump 也可用于重建。

**C. 完全回退旧服务器**：旧机 `maa-backend` 已 `docker compose stop backend`
（容器与数据都在），`docker compose start backend` 即可恢复；前端需重新构建指向旧入口。

## 7.5 域名绑定（2026-11-10）

| 域名 | 指向 | 状态 |
| --- | --- | --- |
| `veripaper.me`（根域） | Pages 项目 `multi-agent-assistant`（CNAME → `*.pages.dev`，橙云，CF 压平根域） | 200，直接打开应用 |
| `www.veripaper.me` | 同上 + Redirect Rule `www → apex`（301） | 301 |
| `multi-agent-assistant.pages.dev` | Pages 默认域名 | 保留可用（回滚/预览） |
| `api.veripaper.me` | A → 47.253.81.6（橙云） | 200，后端 API |

后端 CORS 正则已同时放行三处：

```dotenv
CORS__ALLOW_ORIGIN_REGEX=^https://(([a-z0-9-]+\.)?multi-agent-assistant\.pages\.dev|(www\.)?veripaper\.me)$
```

实测（从 ECS 发起）：`veripaper.me` 200、`www` 301、`pages.dev` 200；
CORS 预检 `veripaper.me` / `www` / `pages.dev` 均返回对应 `allow-origin`，
`evil.example.com` 无该头。

> **关于可达性**：2026-11-10 在**某台特定机器**（带本地代理的沙箱环境）访问
> `veripaper.me` 出现 TLS 握手被重置，而同一环境访问 `api.veripaper.me` 与
> `pages.dev` 正常 —— 属该机出口/代理的 SNI 行为，**不是域名配置问题**：
> 用户侧国内网络实测可正常打开，ECS 侧亦返回 200/301。
> 若将来有人在特定网络下打不开根域，可先用 `multi-agent-assistant.pages.dev`
> 作为备用入口（CORS 已放行，功能一致）。

## 8. 每日备份（已启用，2026-11-10）

`deploy/ecs/docker-compose.yml` 里的 `db-backup` 服务：`pg_dump --schema=public | gzip`
→ 七牛 `db-backups/<UTC 时间戳>.sql.gz`，保留 7 天，每 24h 一次。

```bash
docker compose -f deploy/ecs/docker-compose.yml ps db-backup
docker logs --tail 20 vp-db-backup
docker exec -e RUN_ONCE=1 vp-db-backup /usr/local/bin/backup.sh   # 手动跑一次
```

2026-11-10 实测：dump 21 MB / 30 秒；**上传耗时约 9 分钟**（美东 → 七牛华南约 40 KB/s），
`db-backups/` 下已生成两个对象。日常备份对时延不敏感，可接受；若要更快，
可改为在**国内机器**上执行 `pg_dump`（跨公网连 ECS 需加白名单）或换就近的 R2。

## 9. 安全基线（根据 Cloudflare Security Insights 2026-11-10 处置）

| 项 | 状态 |
| --- | --- |
| Cloudflare 账号 MFA | 用户已开启 |
| Always Use HTTPS | **on**（`http://api.veripaper.me` → 301） |
| 最低 TLS 版本 | **1.2**（TLS 1.0/1.1 握手被拒） |
| HSTS | **on**，`max-age=15768000; includeSubDomains`（未开 preload，保留回退余地） |
| 未知 Host 探测 | nginx `default_server` → `444`，消除「悬空 A 记录」的判定特征 |
| SSH | **仅密钥**（`PasswordAuthentication no`、`PermitRootLogin prohibit-password`），
drop-in 在 `/etc/ssh/sshd_config.d/99-veripaper-keyonly.conf` |
| Bot Fight Mode | **不建议开**：zone 级开关对 `api.veripaper.me` 同样生效，会挑战非浏览器客户端；本项目有开放 API（api_key 程序调用），免费版无法配置例外 |
| AI Labyrinth | **不建议开**：只给 bot 注入隐藏 nofollow 链接，对 SPA + JSON API 无实质安全收益，还会增大 index.html |
| Security.txt | **已上线**：`frontend/public/.well-known/security.txt` 随 Git 构建发布；线上返回 `text/plain` 真实 RFC 9116 内容（2026-11-11 验证） |
| Turnstile | **可选未做**：可防注册/登录刷量，但需 Cloudflare widget + 前后端改造；现有保护是注册限流 5 次/3600s |

> 2026-11-11 Cloudflare Security Insights 复核：这四项均为「未启用功能」建议，不是漏洞。
> 传输与访问基线已处置（HSTS、最低 TLS 1.2、Full strict、SSH 仅密钥、未知 Host 444）。
> Security.txt 已随 Pages Git 构建上线；Bot Fight Mode / AI Labyrinth 保持关闭（理由见上）；
> Turnstile 需前后端改造，未做。发布流程已改为 Git 自动部署（§13），不再依赖 API Token 与 IP 白名单。

## 10. 尚未完成 / 后续

- 七牛 AK/SK 曾多次出现在对话与终端输出中，**建议轮换**（改 `.env` → 重启 backend 与 db-backup）；
- 真实嵌入模型：**已由 SiliconFlow BGE-M3 API 替代**（见 §12），生产不装 torch；
- `/metrics` 已在网关屏蔽；如需采集请走内网或加鉴权。

## 11. 真实 BGE 嵌入：影子表后台切换 SOP

> 当前状态：**正在实施，生产仍为已验证一致的 Mock**。目标模型是
> `BAAI/bge-large-zh`（1024 维），重排保持 Mock。详细设计与完整命令见
> `docs/superpowers/specs/2026-11-11-real-embeddings-shadow-design.md` 和
> `docs/superpowers/plans/2026-11-11-real-embeddings-shadow.md`。

### 11.1 为什么不再原地停机重建

2026-11-11 真实样本已成功写入 32/32、0 失败，但 `elapsed_ms=54693`
（含一次性模型加载）；按去加载后的约 39 秒外推，全量约需 9 小时。
未启动原地全量 BGE。Mock 回滚实测 28,341/28,341、0 失败、86.312 秒，
ID 一致性为真，公网 4/4 PASS。

因此保留 `knowledge_vectors` 承载在线 Mock，新建 `knowledge_vectors_bge`
接收真实向量。只有影子表完整且增量对账成功才短暂重启切换。

### 11.2 不变量与负载门槛

- 查询与文档始终来自同一表；原 Mock 表在真实验收后仍保留；
- 保持原 `chunk_id`，不重新解析文件；DDL 只走 Alembic；
- 构建、备份、预热、样本、全量重建与对账串行，禁止叠加实验矩阵/批量导入；
- 开始构建前根盘至少 10 GB、`MemAvailable` 至少 800 MB；
- 一次性重建固定 `--cpus=1 --memory=2500m` 与 `OMP_NUM_THREADS=1`；
- 每 2–5 分钟检查一次。若 1 分钟 load 连续三次高于 `2 × vCPU`、
  重建期 `MemAvailable < 300 MB` 或根盘余量 `< 3 GB`，停止重建容器；
  在线 Mock 不受影响，资源恢复后续跑，不并行启动第二个进程。

compose 必须有 `HF_HOME=/app/.cache`、`OMP_NUM_THREADS=1` 和
`model_cache:/app/.cache`。模型权重已实测可在新容器加载。

### 11.3 迁移与同版 Mock 上线

影子表 migration 是 `c4f2a8d7e9b1`，索引使用独立的
`ix_kvbge_embedding_hnsw`、`ix_kvbge_collection_doc`、`ix_kvbge_collection_user`。
无 pgvector 的 CI 库 no-op；downgrade 只删影子表。

旧镜像不认识新 head，因此先构建含新迁移的镜像，再以
`RAG_EMBEDDING_MOCK=1` + `RAG__PGVECTOR_TABLE=knowledge_vectors` 启动：

```bash
cd /opt/veripaper
nohup docker compose -f deploy/ecs/docker-compose.yml build \
  --build-arg INSTALL_RAG_MODELS=true backend >/tmp/vp-shadow-build.log 2>&1 &
echo $! >/tmp/vp-shadow-build.pid

# 构建成功且资源正常后：
docker compose -f deploy/ecs/docker-compose.yml up -d --no-build backend
```

新代码 healthy 后自动迁移并创建空影子表。此时给镜像打
`veripaper-backend:shadow-mock`，并验证公网 Mock 4/4 PASS。
普通回滚必须使用这个同版镜像，不能直接让旧镜像连接新 head。

### 11.4 后台全量重建

一次性容器显式覆盖影子表和真实嵌入，其他配置仍从服务器 `.env` 读取：

```bash
nohup docker run --rm --name vp-shadow-bge-reindex --cpus=1 --memory=2500m \
  --network veripaper_default --env-file /opt/veripaper/.env \
  -e RAG__PGVECTOR_TABLE=knowledge_vectors_bge -e RAG_EMBEDDING_MOCK=0 \
  -e HF_HOME=/app/.cache -e OMP_NUM_THREADS=1 \
  -v veripaper_model_cache:/app/.cache \
  --entrypoint python veripaper-backend:latest \
  -m scripts.reindex_vectors --batch-size 100 --json \
  >/tmp/vp-shadow-bge-reindex.log 2>&1 &
echo $! >/tmp/vp-shadow-bge-reindex.pid
```

开始前先 dry-run 和 32 条样本。全量结束必须 `indexed == planned`、
`failed_batches=0`、`errors=[]`。一次性容器继承 backend 镜像的 HTTP healthcheck，
但以 Python reindex 为入口时没有 HTTP 服务，因此 Docker 可能显示 `unhealthy`；
这只是探针语义不匹配，是否成功必须看批次日志与最终 JSON，不要据此重启进程。
后台期间新增/删除文档只影响 Mock 原表，下一阶段用 reconcile 补齐影子表，
不能靠数量相等判断一致。

### 11.5 增量对账与短停切换

`--reconcile` 只对 `DB IDs - store IDs` 生成嵌入，成功后删除
`store IDs - DB IDs`；dry-run 不写不删。它与 `--limit`、`--user-id` 互斥。

```bash
# 在线预对账：容器公共参数同 11.4
python -m scripts.reindex_vectors --reconcile --batch-size 100 --json
python -m scripts.check_kb_consistency --json

# 预对账成功后停止写入，再做最终对账和一致性检查
docker compose -f deploy/ecs/docker-compose.yml stop backend
```

最终对账仍须零失败、缺失/孤儿 ID 为 0。然后只删除 Redis 的
`rag:retrieval:*`（禁止 FLUSHDB），把 `.env` 改为：

```dotenv
RAG__PGVECTOR_TABLE=knowledge_vectors_bge
RAG_EMBEDDING_MOCK=0
INSTALL_RAG_MODELS=true
```

权限保持 600，再执行：

```bash
docker compose -f deploy/ecs/docker-compose.yml up -d --no-build --force-recreate backend
BASE=https://api.veripaper.me bash deploy/ecs/verify.sh
```

启动日志必须是 SentenceTransformerEmbeddingService 且 pgvector 表为影子表。
真实检索使用不复制标题的自然语言查询，目标文档进入 top 5，返回 ID 均为 UUID；
用新容器 `HF_HUB_OFFLINE=1` 实际 encode 验证缓存，而不是只看卷存在。

### 11.6 回滚

- **后台阶段**：停止 `vp-shadow-bge-reindex` 即可；在线 Mock 与原表不受影响；
- **切换后**：停止 backend，用同版代码 + Mock + 原 `knowledge_vectors`
  先跑 `--reconcile`，只补切换后新增的缺项并删除孤儿；随后将 `.env` 改回
  `RAG__PGVECTOR_TABLE=knowledge_vectors` 与 `RAG_EMBEDDING_MOCK=1`，
  用 `veripaper-backend:shadow-mock` 同版镜像启动，清理检索缓存并验收；
- 原 Mock 表未被 BGE 覆盖，回滚**不需要全量重算或恢复整库**；但切换后的新写入
  必须先经 Mock reconcile 回补，不能只看表还在就直接切换；
- 只有明确回退代码时才先用新镜像 `alembic downgrade d5a8c1e3f7b2`
  删除影子表，再恢复旧镜像。

> 预检偏差：`verify.sh` 默认 `http://127.0.0.1` 的 Host 会被
> `default_server 444` 拒绝。线上验收使用 `BASE=https://api.veripaper.me`；
> 这不是 backend 健康退化。

## 12. SiliconFlow BGE-M3 API（**已上线，2026-11-11**）

### 12.1 实测结果

| 项 | 值 |
| --- | --- |
| API 冒烟 | HTTP 200、`BAAI/bge-m3`、1024 维、14 tokens、约 1196 ms |
| 32 条样本 | 32/32、0 失败、2424 ms、实际 3,931 tokens |
| 全量重建 | **28,341/28,341、0 失败、596,775 ms（约 10 分钟）** |
| API 调用 | 443 批共 646 次调用（batch 64 拆分后）、约 224 万 tokens |
| 重试/限流 | **0 次重试、0 次 429** |
| 一致性 | 预对账 0 缺项/0 孤儿；停写最终对账同样为 0；ID 级 `consistent=true` |
| 切换后 | backend healthy、`SiliconFlowEmbeddingService`、公网 **4/4 PASS** |
| 回滚可用性 | 原 Mock 表 28,341 条 ID 级一致；回滚镜像 `shadow-mock`=`79ac91a4374c` |
| 切换后备份 | `db-backups/20261111T061422Z.sql.gz`，**135 MB**，dump 2 分 37 秒、上传约 15 分钟 |

纯语义验收：中文「把检索和生成两个阶段交替迭代……」命中 FAIR-RAG 与
MultiAgent Collaborative RAG；英文「iterative refinement where the system
decides when enough evidence has been gathered」全部命中 FAIR-RAG。
两者均返回可回溯 UUID。

### 12.2 配置与限额

```dotenv
RAG__EMBEDDING_PROVIDER=siliconflow
RAG__EMBEDDING_MODEL=BAAI/bge-m3
RAG__SILICONFLOW_API_KEY=
RAG__SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1
RAG__EMBEDDING_RPM=1600
RAG__EMBEDDING_TPM=400000
RAG__EMBEDDING_BATCH_SIZE=64
RAG__PGVECTOR_TABLE=knowledge_vectors_bge
RAG_EMBEDDING_MOCK=0
INSTALL_RAG_MODELS=false
```

1600/400000 是免费上限 2000/500000 的 80%。每次 HTTP 尝试（含 429/重试）
都进入 60 秒滑动窗口；请求前用 UTF-8 字节数+4 保守估算，成功后用
`usage.total_tokens` 修正。400/401/403/404 不重试，429/网络/5xx 最多 5 次，
Retry-After 或 1/2/4/8/16 秒退避，单次等待上限 60 秒。API 失败不得回退 Mock。

实测估算约为实际的 4 倍（32 条：估算 15,573 / 实际 3,931），限流因此偏保守；
真实 token 在响应后即时修正窗口，所以全量仍能在 10 分钟内完成且零 429。

当前限流是进程内实现，只允许一个 backend 进程，且重建与最终在线 M3 查询
不得同时运行。未来增加 worker/多个调用方时，先改 Redis 分布式限流再扩容。

### 12.3 重建与切换检查点（已全部执行）

1. 同版 Mock + 原表部署新代码并公网验收；
2. M3 两条短文本真实调用通过；
3. 清空旧影子并回查：影子 0、原 Mock 28,341；
4. dry-run、32 条 M3 样本、后台全量，全程 Mock 在线；
5. 在线预对账 → 停止 backend → 最终 reconcile + ID 一致性；
6. 只清理 `rag:retrieval:*`，切到 M3 provider + 影子表并启动；
7. 验证真实语义 top 5、UUID 回溯、公网 health/frontend、API 429/用量与主机资源。

切换后回滚仍需停写，用 Mock 对原表 reconcile 补切换后的新写入，再切回；
不全量重算、不恢复整库。回滚镜像为 `veripaper-backend:shadow-mock`
（`79ac91a4374c`），原表 `knowledge_vectors` 保持 28,341 条 Mock 向量。

### 12.4 磁盘清理（2026-11-11）

上线后根盘一度到 **81%（30G/40G）**。清理后为 **33%（12G/40G，26G 可用）**，
释放约 **18.7 GB**，清理项与保留项如下：

| 动作 | 对象 | 回收 |
| --- | --- | --- |
| `docker builder prune -af` | 87 项构建缓存 | 12.44 GB |
| `docker rmi` | `veripaper-backend:bge-real`（本地 BGE-large 实验镜像） | 3.92 GB |
| `docker rmi` | dangling `183500694951`（旧 latest 残留） | 3.92 GB |
| `docker rmi` | `veripaper-backend:mock-pre-bge`（旧回滚锚点，已被 shadow-mock 取代） | 2.14 GB |
| 清空卷内容 | `veripaper_model_cache`（API 模式不再需要模型权重） | 1.2 GB |
| `rm /root/*.bundle` | 10 个历史部署包 | 约 90 MB |
| `docker rmi` | `alpine:3.22` 辅助清卷镜像 | 12.9 MB |

清理后回归：公网 health ready、frontend 200、M3 实时调用 870 ms/1024 维、
两表各 **28,341** 条、五容器 healthy、backend `healthy`。

**保留且不要删**：`veripaper-backend:latest` 与 `shadow-mock`（同一镜像
`79ac91a4374c`，生产运行 + 回滚锚点）、`veripaper-db-backup:local`、
postgres/redis/nginx 基础镜像、`veripaper_pgdata` 卷、
`/opt/veripaper/backup/` 下两份 Supabase 迁移期 dump（各 21 MB，回滚用）。

> 卷 `veripaper_model_cache` 仍在 compose 中挂载（现在是空卷）。若彻底不再
> 需要本地模型，可从 compose 移除该挂载以消除 `external` 警告；本次只清内容，
> 未改 compose，避免影响运行中的容器。

## 13. 前端发布：Cloudflare Pages Git 自动部署（2026-11-11 起）

### 13.1 当前方式

Pages 项目 `multi-agent-assistant` 已从 **Direct Upload 改为 Git 集成**
（同一项目原地连接，域名与部署历史保留）：

| 项 | 值 |
| --- | --- |
| 仓库 | `sakura-del/Multi-agent-collaborative-automation-task-assistant` |
| 生产分支 | `main` |
| 根目录 | `frontend` |
| 构建命令 | `npm ci && npm run build` |
| 输出目录 | `dist` |
| 环境变量（生产） | `VITE_API_BASE_URL=https://api.veripaper.me/api/v1`、`VITE_WS_BASE_URL=wss://api.veripaper.me`、`NODE_VERSION=20` |
| 域名 | `veripaper.me` + `multi-agent-assistant.pages.dev` |

**发布方式：push 到 GitHub `main` 即自动构建部署。** 不再需要本机 Node、
`scripts/deploy-pages.mjs`、Cloudflare API Token 或令牌 IP 白名单。

`scripts/deploy-pages.mjs` 保留仅为应急（如需临时回到 Direct Upload 手动上传）。
Cloudflare API Token 与 `C:\Users\Lenovo\cf-secrets.env` 仅用于排查/控制台操作，
日常发布不依赖它们；动态 IP 不再影响发布。

### 13.2 必须知道的两个坑

1. **环境变量必须在构建之前保存**。若先连接 Git 触发首次构建、之后才添加变量，
   首次产物会缺少 `VITE_API_BASE_URL`，前端退化为相对路径 `/api/v1` →
   请求打到 `veripaper.me` 静态站，拿到 `text/html` 而非 JSON，前端整体不可用。
   症状：`/knowledge/search` 等接口返回 HTML 或 JSON 解析错误。
2. **Secret 与 Text 类型在 API 里都读不到明文**（`GET projects` 返回空字符串是正常的），
   所以「变量是否生效」只能通过**构建产物**验证：下载首页引用的
   `assets/index-*.js`，搜索 `api.veripaper.me`。构建正确时能匹配到
   `https://api.veripaper.me/api/v1` 与 `wss://api.veripaper.me`。

### 13.3 修复记录（2026-11-11）

首次连接后的构建（`70669cfb`，16:24 JST）产物为 `index-BiH7iure.js`，
`api.veripaper.me` 命中 **0 次** → 前端 API 走相对路径 → 实际不可用。
判断为变量在构建之后才保存。用 API 重试该部署（`a7e83ca8`）后产物为
`index-DLpds5o8.js`，`api.veripaper.me` 命中 **2 次**、`wss://` 命中 1 次，
提取到 `https://api.veripaper.me/api/v1`；同时验证：

- `https://veripaper.me/.well-known/security.txt` → `text/plain`，内容为真实
  RFC 9116 条目（此前是 SPA fallback 返回 HTML）；
- CORS 预检 `OPTIONS /api/v1/tasks`（Origin=`https://veripaper.me`）→
  `access-control-allow-origin: https://veripaper.me`；
- `https://api.veripaper.me/api/v1/health/ready` → ready。

### 13.4 换机/换网络后的发布流程

```bash
git push backup main     # backup 指向 GitHub；推送后 Cloudflare 自动构建
```

在 Cloudflare → Workers & Pages → multi-agent-assistant → 部署 里查看进度。
失败时先看构建日志的 `npm ci` / `vue-tsc` 输出，再核对上述环境变量是否仍在。

### 13.5 自动部署闭环验证（2026-11-11）

三次部署构成完整证据链：

| 部署 | 时间 (JST) | 触发 | 提交 | 结果 |
| --- | --- | --- | --- | --- |
| `70669cfb` | 16:24 | ad_hoc（连接后首次） | `b063500` | 成功但**变量未注入**，前端不可用 |
| `a7e83ca8` | 16:29 | ad_hoc（API 重试） | `b063500` | 成功，产物含正确 API 地址 |
| `cb1bbf2f` | 16:33 | **github:push** | **`26dc12a`** | **成功**，自动部署闭环验证通过 |

`cb1bbf2f` 由 push 到 `main` 自动触发，说明「push 即部署」生效。
其产物与 `a7e83ca8` 同为 `index-DLpds5o8.js`（该次仅改文档，前端源码未变，
hash 一致符合预期）。线上复核：首页 200、`security.txt` 为 `text/plain`、
`api.veripaper.me` health ready、CORS 允许 `https://veripaper.me`。

> 本节取代继续运行本地 BGE-large 的做法。生产服务器只调用 Embeddings API，
> 不进行本地模型推理。设计/完整计划见
> `docs/superpowers/specs/2026-11-11-siliconflow-bge-m3-design.md` 与
> `docs/superpowers/plans/2026-11-11-siliconflow-bge-m3.md`。

## 14. 账号与凭据运维（Issue #10，2026-11-11）

安全排查（`docs/security/2026-11-11-security-audit.md` H-1/H-2）发现：`demo` 用
仓库文档里公开的弱口令就能登录，且是 `admin`；`e2efull`/`e2ereconn` 同样已知口令有效；
库里另有 24 个历史测试账号。本节是处置与后续 SOP。

工具：`backend/scripts/account_hygiene.py`（`audit` / `rotate` / `purge`，
默认 dry-run，含硬规则与事后回查）。

### 14.1 轮换口令

```bash
scp backend/scripts/account_hygiene.py root@47.253.81.6:/root/
ssh root@47.253.81.6 'docker cp /root/account_hygiene.py vp-backend:/app/scripts/'
# 容器里写 /opt/veripaper 会 FileNotFoundError（没挂载）→ 先写 /tmp 再拷出来
ssh root@47.253.81.6 'docker exec -w /app vp-backend \
  python -m scripts.account_hygiene rotate --username demo --out-file /tmp/.demo-password'
ssh root@47.253.81.6 'docker cp vp-backend:/tmp/.demo-password /opt/veripaper/.demo-password \
  && docker exec vp-backend rm -f /tmp/.demo-password && chmod 600 /opt/veripaper/.demo-password'
```

`rotate` 同时写 `token_invalid_before = now()`，让此前签发的 access/refresh 全部失效
（否则改了口令、攻击者手上的 7 天 refresh 仍可用）。

> **坑（本会话踩过）**：首版脚本"先 `commit` 再写文件"，容器写挂载外路径失败后
> **库里的口令已经改了、新口令却没人知道**，账号直接被锁死。现版本**先落盘再改库**：
> 写不出文件就根本不动数据库。

### 14.2 清理测试账号

```bash
# 先看清单（默认 dry-run，不写库）
docker exec -w /app vp-backend python -m scripts.account_hygiene purge \
  --usernames e2efull,e2ereconn,literature-demo --apply   # 确认无误后再加 --apply
```

硬规则（任一命中即整批拒绝执行）：`demo` 受保护；任何 `admin` 拒删；
名字不匹配测试账号规律（`exitgate\d{6,}` / `literature-demo\d*` / `rh\d{6,}` / …）拒删；
请求里出现不存在的账号名也拒（拼错就应该报错，而不是静默跳过）。

**向量要单独清**：`knowledge_vectors` / `knowledge_vectors_bge` 是裸表，
没有外键指向 `knowledge_doc`，删账号只会级联删文档/分块/`vector_index`，
向量会留下来变成孤儿。脚本按 `user_id` 显式删两张表。

### 14.3 本次实测证据（2026-11-11 17:15–17:20 JST）

| 项 | 结果 |
| --- | --- |
| 备份 | `pg_dump -Fc` 140 MB → `/opt/veripaper/backup/pre-issue10-20261111-171506.dump`；账号清单 JSON 另存 `backup/issue10/users-*.txt` |
| 旧口令登录（轮换前） | **200** + `access_token` |
| 轮换 | 32 位强随机（四类字符、无易混字符），落 `/opt/veripaper/.demo-password` 600 |
| 旧口令登录（轮换后） | **401** |
| 新口令登录 | **200**；`/auth/me` → `{'username': 'demo', 'role': 'admin', 'is_active': True}` |
| 清理 | 26 个账号；级联删 15 个任务、90 篇文档、25506 个分块；**显式删向量 51012 条**（2 表 × 25506） |
| 回查 | 残留账号 **0**、全库孤儿向量 **0**；`user=1 / task=86 / knowledge_doc=11 / doc_chunk=2835 / knowledge_vectors=2835 / knowledge_vectors_bge=2835 / workspace=4 / api_key=1 / subscription=1` |
| 业务回归 | `/health/ready` → `ready`（db/redis/vector 全 ok）；`/knowledge/search` 返回 demo 真实文档分块 |
| 仓库脱敏 | 全仓库 `grep` 0 命中；`backend/tests/test_no_plaintext_credentials.py` 4 条全绿 |

### 14.4 命令行访问注意

宿主机 `curl http://127.0.0.1/...` 会**被网关的 `default_server` 直接 444 断连**
（防悬空 A 记录误报）。本机验收要带 Host 头：

```bash
curl -s http://127.0.0.1/api/v1/health/ready -H 'Host: api.veripaper.me'
```

登录这类含口令的请求不要把口令放命令行（`ps`/history 会留痕）：写进 600 的
JSON 文件用 `--data-binary @file`，用完 `shred -u`。
