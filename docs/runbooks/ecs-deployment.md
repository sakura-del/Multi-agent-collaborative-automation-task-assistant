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
| `/opt/veripaper/backup/` | dump 归档（`pre-issue10-*.dump` 为最新全量；`supabase-*.sql.gz` 为迁移期存档）。**Supabase 已于 2026-11-12 下线，这里是唯一的整库重建来源** |
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

# 历史遗留：迁移源库 DSN（`migrate-from-supabase.sh` 读它）。源库已删除，这些值已失效
SUPABASE_PG_HOST=aws-0-ap-southeast-1.pooler.supabase.com
SUPABASE_PG_USER=postgres.<ref>
SUPABASE_PG_PASSWORD=<pwd>
```

> ⚠️ **不要在 `.env` 的值里写正则括号而用 `source .env` 读**：`CORS__ALLOW_ORIGIN_REGEX=^https://(...)$`
> 会让 bash 报语法错误。脚本里用 `grep | cut` 取键值（见 `migrate-from-supabase.sh` 的 `get_env`）。
> docker compose 的 env_file 不受影响。

## 4. 数据迁移（Supabase → ECS）—— 已于 2026-11-11 完成，源库 2026-11-12 已下线

> **本节为历史记录。** Supabase 项目已删除，`migrate-from-supabase.sh` 现在跑不通（源库不存在），
> `.env` 里的 `SUPABASE_PG_*` 也已成废值。留档目的：知道当初怎么迁的、迁完对过哪些数。

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

**B. 整库回滚到 Supabase —— 已于 2026-11-12 关闭，不要再执行**：

Supabase 项目已删除，下面这段只留作历史记录。**回滚路径已关闭**：`aws-0-ap-southeast-1.pooler.supabase.com`
不再可解析，改了 DSN 只会让 backend 起不来。

```bash
# 历史命令（已失效，勿执行）
sed -i 's#^DATABASE__URL=.*#DATABASE__URL=postgresql+asyncpg://postgres.<ref>:<pwd>@aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres?ssl=require#' .env
sed -i 's#^RAG__PGVECTOR_DSN=.*#RAG__PGVECTOR_DSN=postgresql://postgres.<ref>:<pwd>@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres?sslmode=require#' .env
```

**现在唯一的重建路径**（Supabase 没了，只剩本地 dump）：

```bash
cd /opt/veripaper
# 1) 先停写（backend 与每日备份都占着连接，不先停 DROP DATABASE 会失败）
docker compose -f deploy/ecs/docker-compose.yml stop backend db-backup
# 2) 重建空库
docker compose -f deploy/ecs/docker-compose.yml exec -T postgres \
  psql -U veripaper -d postgres -c 'DROP DATABASE IF EXISTS veripaper;' \
  -c 'CREATE DATABASE veripaper OWNER veripaper;'
# 3) 恢复（用容器自带的 pg_restore 17.11，宿主机客户端版本可能低于服务端）
docker compose -f deploy/ecs/docker-compose.yml exec -T postgres \
  pg_restore -U veripaper -d veripaper --no-owner --clean --if-exists \
  < backup/pre-issue10-20261111-171506.dump
# 4) 起 backend 并复验
docker compose -f deploy/ecs/docker-compose.yml up -d backend db-backup
curl -s https://api.veripaper.me/api/v1/health/ready
```

> ⚠️ dump 是 **2026-11-11 17:15** 的快照，之后注册的账号、新增的任务/文档都不在里面。
> 日常恢复优先用七牛 `db-backups/` 前缀下的**每日备份**（`vp-db-backup` 容器每天 1 次、保留 7 天），
> 它比这份 dump 新。

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
| fail2ban | **已启用（2026-11-12）**：`bantime=1h`、`findtime=10m`、`maxretry=5`，
`ignoreip` 仅含本机与运维出口 `13.125.5.87`。封禁走 **nftables**
（`table inet f2b-table`），查 `iptables -L f2b-sshd` 会显示无此链，属正常。
详见 [2026-11-12 告警排查](../security/2026-11-12-ssh-alert-investigation.md) |
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
`/opt/veripaper/backup/` 下的 `pre-issue10-20261111-171506.dump`（140 MB 全量）与
两份 Supabase 迁移期 dump（各 21 MB）。**Supabase 已下线，这份 140 MB dump 是最后的本地兜底**。

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

## 15. refresh 令牌轮换吊销（Issue #11 M-1，2026-11-12）

安全排查 M-1 实测：用 refresh_token 换发新令牌后，**同一个 refresh 还能再用一次**
（返回 200）。令牌一旦泄露就踢不掉，攻击者可在 7 天内持续换 access。

### 15.1 设计

| 决策 | 理由 |
| --- | --- |
| refresh 带 `jti`，access 不带 | 只有 refresh 参与轮换；给 access 也加 jti 会让人误以为 access 可吊销 |
| 按 `jti` 吊销，不按用户 | 按用户吊销（改 `token_invalid_before`）会把同一个人其他设备一起踢下线 |
| 落库 `revoked_refresh_token`，不用 Redis | Redis 重启/清空会丢黑名单，而令牌还有 7 天寿命；表跟着库一起备份 |
| `expires_at` = 令牌自身 `exp`，每次轮换顺手 `purge_expired()` | 表不会无限增长 |
| 抢占式登记（`ON CONFLICT DO NOTHING` + rowcount） | 并发重放时由数据库裁决谁是真轮换，避免同一令牌换两次 |
| **60 秒宽限期**（`JWT_REFRESH_REUSE_GRACE_SECONDS`） | 浏览器两个标签页会同时刷新，一律「重放即家族作废」会把用户每小时踢下线一次 |

处置分两档：

- 宽限期内重放 → **401**（`reason=refresh_reuse_grace`），只拒这次，**不作废家族**；
- 超宽限期重放 → **401**（`reason=refresh_reuse`），`token_invalid_before = now`，
  该用户全部 access/refresh 立即失效（认定泄露）。

修复前签发的 refresh **没有 `jti`**，无法登记吊销 → 一律 401，用户需重新登录。
`0` 表示关闭宽限（最严格）。

### 15.2 迁移与部署

迁移 `a7d3e9b6c1f4`（`down_revision = c4f2a8d7e9b1`）建 `revoked_refresh_token`
+ `user_id` / `expires_at` 两个索引。容器 `entrypoint.sh` 启动即 `alembic upgrade head`，
所以 `docker compose up -d backend` 之后 schema 已就绪，无需人工步骤。

```bash
cd /opt/veripaper/deploy/ecs && docker compose build backend && docker compose up -d backend
docker exec vp-backend alembic current          # 期望 a7d3e9b6c1f4 (head)
```

### 15.3 本次实测证据（2026-11-12 11:35 JST）

| 步骤 | 结果 |
| --- | --- |
| 部署前迁移版本 | `c4f2a8d7e9b1 (head)` |
| 部署后迁移版本 | **`a7d3e9b6c1f4 (head)`**（entrypoint 自动升级成功） |
| 表与索引 | `revoked_refresh_token` + `revoked_refresh_token_pkey` / `ix_..._user_id` / `ix_..._expires_at` |
| ① 登录 | **200**，`expires_in=3600`（与配置一致） |
| ② 首次刷新 | **200**，新 refresh 与原不同 |
| ③ 旧 refresh 立即重放 | **401** `refresh_reuse_grace`（宽限期内，只拒这次） |
| ④ 宽限期内新 access | **200** `demo / admin`（**没有**被自己踢下线） |
| ⑤ 新 refresh 继续轮换 | **200** |
| ⑥ 等 65 秒后重放旧 refresh | **401** `refresh_reuse`（超宽限期） |
| ⑦ 家族作废：新 access | **401** `UNAUTHORIZED` |
| ⑧ 家族作废：新 refresh | **401** |
| ⑨ `user.token_invalid_before` | 已写入 `2026-11-12 02:35:03`（UTC） |

> 副作用：验证 ⑦⑧ 必须触发一次真实家族作废，`demo` 的旧会话因此失效——
> 用口令重新登录即可，数据无损。

### 15.4 排查命令

```bash
# 某用户当前有多少条吊销记录（正常情况下 ≈ 近期轮换次数，7 天后自动清）
docker exec vp-postgres psql -U veripaper -d veripaper -c \
  "select user_id, count(*), max(created_at) from revoked_refresh_token group by 1"

# 是否被家族作废过
docker exec vp-postgres psql -U veripaper -d veripaper -c \
  "select username, token_invalid_before from \"user\""

# 日志里的两类事件
docker logs vp-backend --since 1h 2>&1 | grep -E "refresh_token_(rotated|reuse)"
```

## 16. 网关与站点安全响应头（Issue #11 M-4，2026-11-12）

安全排查实测：`api.veripaper.me` 只有 HSTS（Cloudflare 加）与 `X-Content-Type-Options`，
**缺** `X-Frame-Options` / CSP / `Referrer-Policy` / `Permissions-Policy` / COOP。
影响：API 响应可被任意站点嵌进 iframe（点击劫持）；XSS 之后也没有「能加载什么」的约束。

### 16.1 网关（`deploy/ecs/nginx-api-tls.conf`）

80 与 443 两个反代 server 块各加 6 个头，全部 `always`（4xx/5xx 也要带——
探测与攻击最常看到的正是错误响应）：

```
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: no-referrer
Permissions-Policy: geolocation=(), microphone=(), camera=(), payment=(), usb=(), interest-cohort=()
Cross-Origin-Opener-Policy: same-origin
Content-Security-Policy: $csp_policy
```

CSP 用 `map $uri $csp_policy` **分流**，而不是在 location 里覆盖：

- API 默认 `default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'`；
- `/docs` 与 `/redoc` 放宽（jsdelivr + `'unsafe-inline'` + Cloudflare 探针），否则 Swagger UI 白屏。

> 为什么必须用 map：nginx 的 `add_header` **一旦在子级出现，父级全部不再继承**。
> 想给 `/docs` 换一套策略，若靠 location 覆盖，6 个头要在每个 location 重复一遍，
> 漏一处就静默少一层防护，而且没有任何报错。

**刻意不加 `Cross-Origin-Resource-Policy: same-origin`**：这个 API 本来就要被
`veripaper.me` 跨源调用，写成 same-origin 会把产品自己的前端挡掉。

### 16.2 站点（`frontend/public/_headers`）

Cloudflare Pages 读取构建产物根目录的 `_headers`，随代码版本管理（可 review、可回滚）：
`nosniff` / `X-Frame-Options: DENY` / `Referrer-Policy: strict-origin-when-cross-origin` /
`Permissions-Policy`。

**暂不下发 CSP**：SPA 的 CSP 会真实影响渲染（Vite 产物的内联样式、库的 style 属性、
`connect-src` 要同时覆盖根域、`api.veripaper.me` 与 `wss`），打崩的表现是用户白屏且不报错。
这一条单独做，必须先在真实浏览器里回归（下一步，见 §16.5）。

### 16.3 ⚠️ 部署坑：单文件 bind mount 不会跟着 git 更新

`docker-compose` 把 `./nginx-api-tls.conf` **单文件**挂进容器。`git merge` 是「写新文件 + rename」，
inode 变了，而容器仍绑在旧 inode 上：

- `nginx -t` 说配置 OK；
- `nginx -s reload` 也"成功"；
- **但容器里读到的还是旧配置**——头一个都没出现，且没有任何报错。

判据与解法：

```bash
docker exec vp-gateway grep -c add_header /etc/nginx/conf.d/default.conf   # 0 => 还是旧文件
cd /opt/veripaper/deploy/ecs && docker compose up -d --force-recreate gateway
```

（如果改成挂目录 `./nginx/:/etc/nginx/conf.d/` 就不会有这个坑；当前保持单文件是为了显式。）

### 16.4 本次实测证据（2026-11-12 12:05 JST）

| 检查点 | 结果 |
| --- | --- |
| `nginx -t`（重建前） | syntax ok / test successful |
| 容器内 `grep -c add_header` | 重建前 **0**（踩坑），`--force-recreate` 后 **14** |
| 源站 443 `GET /api/v1/health/ready` | 200 + **6 个头齐全** |
| 源站 80 同路径 | 405（HEAD 不允许）**但头仍在** → `always` 生效 |
| 源站 404 `/api/v1/does-not-exist` | 404 + `x-frame-options: DENY` → `always` 生效 |
| 源站 `/docs` | 200 + **放宽版 CSP**（含 jsdelivr 与 CF 探针） |
| 公网 `https://api.veripaper.me` | 200 + 6 个头 + HSTS |
| CORS 预检 `OPTIONS /tasks`（Origin=veripaper.me） | 200 + `access-control-allow-origin` 命中（未被新头影响） |
| 真实浏览器 `https://api.veripaper.me/docs` | 标题 `VeriPaper - Swagger UI`、**72 个操作**、CSP 错误 **0**、失败请求 **0** |
| 真实浏览器 `https://veripaper.me` | 标题 VeriPaper、CSP 错误 **0** |
| 真实浏览器登录冒烟 | 登录成功 → 跳 `/tasks`、页面异常 **0** |
| 站点（Pages 自动部署后） | `x-frame-options: DENY` / `referrer-policy: strict-origin-when-cross-origin` / `permissions-policy` / `nosniff` 均已生效 |

### 16.5 仍然待办：站点 CSP

需要先用真实浏览器把以下内容列全再下发，否则白屏风险不可接受：

1. `frontend/dist/index.html` 是否有内联 `<script>`（有则要 hash 或 nonce）；
2. 第三方资源：Cloudflare Insights 探针、`cdn.jsdelivr.net`（仅 docs）、字体；
3. `connect-src` 必须覆盖 `https://api.veripaper.me` 与 `wss://api.veripaper.me`；
4. `style-src` 需要 `'unsafe-inline'`（Vue/Tailwind 运行时与 `motion` 会写 style 属性）；
5. 预览域 `*.multi-agent-assistant.pages.dev` 是否也要放行（若要求预览可用）。

下发方式：在 `frontend/public/_headers` 的 `/*` 规则里加一行 `Content-Security-Policy:`，
然后在临时预览域先验证（Pages 每次 push 都有预览 URL），确认无白屏、无 CSP 报错再合并到生产。

## 17. 真实客户端 IP 与登录失败退避（Issue #11 M-2 / M-3，2026-11-12）

### 17.1 问题

| 项 | 实测现象 |
| --- | --- |
| M-2 | `request.client.host` 恒为 nginx 容器内网 IP，Redis key 是 `ratelimit:3600:auth_register:ip:172.18.0.5`。注册 5 次/小时成了**全局限额**：任意客户端刷满，所有真实用户 1 小时内无法注册 |
| M-3 | 登录限流 10 次/60 秒：**10 次失败都不触发 429**，第 11 次才限；且没有任何账号级计数，换 IP 就能继续试同一个账号 |

### 17.2 M-2 的两半，缺一不可

```nginx
# 网关：只对 Cloudflare 网段采信 CF-Connecting-IP（22 个 v4/v6 段）
set_real_ip_from 173.245.48.0/20;
...
real_ip_header CF-Connecting-IP;
real_ip_recursive on;
# 每个反代块都用 $remote_addr 覆盖写 X-Real-IP（客户端自带的同名头会被冲掉）
proxy_set_header X-Real-IP $remote_addr;
```

```python
# 应用：只读 X-Real-IP，且必须能解析成合法 IP，否则退回 socket 地址；永不读 X-Forwarded-For
gateway_ip = _normalize_ip(request.headers.get("x-real-ip"))
```

> **为什么不能只开 `TRUST_PROXY`**：原实现取 `X-Forwarded-For` 的**第一段**，而 nginx 的
> `$proxy_add_x_forwarded_for` 是「客户端自带的 XFF + 真实地址」拼接——第一段正是客户端自己写的。
> 只开开关不锁源站，任何人加一个 `X-Forwarded-For: 1.1.1.1` 就换了一个限流桶。
> 实测反证：改前用两个不同 XFF 打同一路径，Redis 里出现 `ip:1.1.1.1` 与 `ip:2.2.2.2` 两个桶。

### 17.3 M-3 的两层

1. **IP 维度**（中间件）：`auth_login` 5 次/60 秒（原 10 次）；
2. **账号维度**（`app/core/login_throttle.py`）：连续失败 10 次 → 锁 900 秒，
   期间**密码正确也返回 429**（`reason=account_locked`）——否则爆破者猜中就登录，退避等于没做。

- key 用**规范化账号名**（去空格 + 小写）的 sha256 前 32 位：Redis 里不出现明文用户名，
  且 `Demo` / `demo` 共用一份计数；
- 登录成功清零；组件异常 fail-open（Redis 挂了不能把所有人挡在门外）；
- 配置：`RATE_LIMIT__LOGIN_FAILURE_LIMIT` / `RATE_LIMIT__LOGIN_FAILURE_WINDOW_SECONDS`。

### 17.4 本次实测证据（2026-11-12 13:05 JST）

| 检查点 | 结果 |
| --- | --- |
| 容器内 `set_real_ip_from` 条数 | 23（22 个网段 + 注释示例） |
| `RATE_LIMIT__TRUST_PROXY` | `true`（写入 `/opt/veripaper/.env`） |
| 源站直连的限流 key | `ratelimit:60:auth_login:ip:172.18.0.1`（宿主机到容器的桥接地址） |
| **公网客户端的限流 key** | **`ratelimit:60:auth_login:ip:13.125.5.87`** —— 与 `cdn-cgi/trace` 返回的本机公网 IP 一致；改前是 `172.18.0.5` |
| 伪造 `X-Forwarded-For: 1.2.3.4` + 源站直连伪造 `X-Real-IP` | **没有产生新桶**，仍落回原桶；连打 5 次后第 5 次即 429 |
| 429 响应 | `Retry-After: 60`，`details.limit=5 / window_seconds=60` |
| 账号级锁定 | 连续失败达 10 次后，**密码正确也 429** `reason=account_locked`，`retry_after=647` |
| 清理 | 探针账号已删除（回查 `probe%` = 0，库内仅 `demo`）；`ratelimit:*` 键已清空后重新生成（含真实 IP） |
| 回归 | 公网 `demo` 登录 200、`/health/ready` ready、前端 200 |

> ⚠️ 两层限流会互相遮挡：从单个 IP 攒满「账号 10 次失败」时，**IP 的 5 次/分先返回 429**，
> 看到的 `reason` 是路由限流而不是 `account_locked`。验收时要按「5 次 + 等 61 秒」分轮打，
> 或直接看 Redis 里 `ratelimit:login_fail:*` 的计数。

### 17.5 常用排查

```bash
# 谁被限流了（key 里就是真实客户端 IP）
docker exec vp-redis redis-cli --scan --pattern 'ratelimit:*'
docker exec vp-redis redis-cli get 'ratelimit:60:auth_login:ip:<IP>'
docker exec vp-redis redis-cli ttl 'ratelimit:60:auth_login:ip:<IP>'

# 某账号连续失败次数 / 剩余锁定
docker exec vp-redis redis-cli --scan --pattern 'ratelimit:login_fail:*'

# 后端日志（账号名只记哈希前缀）
docker logs vp-backend --since 30m 2>&1 | grep -E "login_(failed|locked|throttle)"
```

> Cloudflare 网段变更时同步 `deploy/ecs/nginx-api-tls.conf` 的 `set_real_ip_from`；
> 漏更新只会让新网段来的请求被当成 CF 节点地址（限流精度下降），不会导致不可用。

### 16.6 站点 CSP 上线（2026-11-12 补完）

`frontend/public/_headers` 的 `/*` 规则里已加 `Content-Security-Policy`。
策略逐条对应构建产物，改之前必须先读 `_headers` 里的注释：

```
default-src 'self';
script-src 'self' https://static.cloudflareinsights.com;
style-src 'self' 'unsafe-inline';
img-src 'self' data: blob:; font-src 'self' data:;
connect-src 'self' https://api.veripaper.me wss://api.veripaper.me https://cloudflareinsights.com;
object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'
```

关键点：

- **`script-src` 不给 `'unsafe-inline'`**：实测 `dist/index.html` 只有一个外链 module 脚本，
  没有内联 `<script>`，没必要开口子；
- **必须放行 `static.cloudflareinsights.com`**：Cloudflare 会往 HTML 注入 Web Analytics 探针，
  不放行就是控制台一片 `Refused to load`（docs 页踩过同一个坑）；
- **`style-src` 必须给 `'unsafe-inline'`**：Vue/Tailwind 运行时与 motion 会写 style 属性，
  CSP3 下 style 属性受 `style-src` 管辖，不给就是满屏裸样式；
- **`connect-src` 写死 API 域名**：前端用绝对地址 `https://api.veripaper.me`，且预览域
  (`*.pages.dev`) 调同一套 API，写 `'self'` 不够。

验证方法（**上线前必须做**，白屏不报错是这类改动最大的坑）：

```bash
cd frontend && npx vite build      # 产物根目录会带上 _headers
# 用 dist/_headers 里的头起一个本地静态服务，再用真实浏览器断言：
#   - #app 有内容、登录表单可见、按钮背景色不是 transparent（说明 CSS 生效）
#   - securitypolicyviolation 事件数为 0
```

本次实测（2026-11-12 13:00 JST）：`appHtmlLength=7308`、`formVisible=true`、
`buttonBg=oklch(0.44 0.065 180)`（样式生效）、**violations=[]、CSP 控制台消息 0**。
Push 后 Pages 自动重建，公网复核见 §16.7。

### 16.7 站点 CSP 上线验证（2026-11-12 14:05 JST）

| 检查点 | 结果 |
| --- | --- |
| 公网响应头 `veripaper.me` | `content-security-policy` 已出现（Pages 自动重建完成） |
| 登录页渲染 | `#account` 可见 |
| 登录流程 | 真实浏览器登录成功 → 跳 `/tasks` |
| **CSP 违规事件** | 登录前后均 **0 条**（`securitypolicyviolation` 监听） |
| 控制台 CSP 报错 | **0** |

> 若后续新增第三方资源（埋点、字体、CDN），先在这个页面加域名再发版——
> 被 CSP 拦掉的资源不会报 500，只会静默消失。

## 18. 依赖瘦身：chromadb 移出生产镜像（Issue #11 M-5，2026-11-12）

### 18.1 改了什么

`chromadb==1.5.9` 从主依赖移到可选 `[chroma]`，生产镜像不再安装它。
完整理由与残留风险见 [dependency-risk.md](../security/dependency-risk.md)。

### 18.2 实测

| 检查点 | 结果 |
| --- | --- |
| 镜像大小 | **2.14 GB → 1.78 GB**（省 360 MB），导出层 513 MB → 450 MB |
| `docker exec vp-backend python -c "import chromadb"` | **ModuleNotFoundError**（符合预期） |
| `/health/ready` | `ready`，`checks.vector=ok`（走 pgvector 探针） |
| 真实检索 | `/knowledge/search` 命中 3 条，返回真实 chunk id → **RAG 未被依赖移除影响** |
| 容器状态 | healthy，五容器正常 |

### 18.3 回滚 / 本地开发需要 chroma 时

```bash
pip install -e ".[chroma]"          # 本地
docker build --build-arg ...        # 或在 Dockerfile 里改成 pip install ".[chroma]"
```

注意版本必须与 `docker-compose.yml` 的 chromadb 镜像一致（`chromadb/chroma:1.5.9`）。

## 19. B 组运维：SSH 加固 / 密钥备份清理 / 系统包 / 源站只允许 Cloudflare 回源（2026-11-12）

一次会话内完成四项「零散收尾」。共同原则：**改动前先证明现状、改动中挂自动回滚、改动后用外部视角验证**。

### 19.1 SSH 加固

新增 `/etc/ssh/sshd_config.d/98-veripaper-hardening.conf`（认证方式仍在 `99-veripaper-keyonly.conf`）：

| 项 | 改前 | 改后 | 理由 |
| --- | --- | --- | --- |
| `X11Forwarding` | yes | **no** | 服务器没有图形程序，开着只是多一条可被滥用的通道 |
| `MaxAuthTries` | 6 | **3** | 密钥登录一次就过，6 次只够爆破者多试几轮 |
| `LoginGraceTime` | 120 | **30** | 压缩未认证连接的占用窗口（slowloris） |
| `ClientAliveInterval` / `CountMax` | 0 / 3（永不主动断） | **300 / 2** | 空闲 10 分钟断开 |

> **为什么 drop-in 能覆盖主配置**：sshd 对同一关键字取**第一次**出现的值，而 `sshd_config`
> 顶部就 `Include /etc/ssh/sshd_config.d/*.conf`。所以目录里的值总是赢——这也是
> `99-veripaper-keyonly.conf` 的 `PasswordAuthentication no` 能压过主配置 `yes` 的原因。

**回滚保险**（改 sshd 必须做，否则一旦连不上就再也进不去）：落盘后先 `sshd -t`，再挂一个
5 分钟定时任务——期间若没删标记文件，自动删掉 drop-in 并 reload。

实测：新会话登录成功；带 `DISPLAY` 强制请求 X11 转发返回
`Warning: untrusted X11 forwarding setup failed`，远端 `DISPLAY=none` → 确认 X11 已关。

### 19.2 清理明文密钥备份

`.env.pre-bge`（11-11 11:09）、`.env.pre-m3-cutover`（11-11 14:11）两份 600 权限的明文密钥，
对应回滚窗口早已过去（pgvector 已稳定运行一天以上）。

删除前做了**键名集合比对**（不打印值）：两份备份里没有任何一个键是现役 `.env` 缺失的，
反向只有现役多出 `RATE_LIMIT__TRUST_PROXY`（今天新加）→ 确认删除不丢配置。
之后 `shred -u -z` 覆盖再 unlink。现役只剩 `.env` 与 `.env.example`。

> 局限：ext4 日志与快照仍可能留有旧块，`shred` 不是绝对保证。真正要杜绝，密钥本身要换。

### 19.3 系统包升级

`apt-get update` 后 15 个可升级，升级 11 个（含 `docker-compose-plugin 5.7.0→5.7.1`、
`libglib`、`libsqlite3`、`tmux`），**无内核更新，不需要重启**（`/var/run/reboot-required` 不存在）。

保留 4 个，且**都不是安全更新**（全部来自 `noble-updates`，非 `noble-security`；
`apt-check` 报 `0 updates can be applied immediately`）：

| 包 | 原因 | 处置 |
| --- | --- | --- |
| `snapd`、`distro-info`、`python3-distro-info` | Ubuntu **分阶段推送（phasing）**，apt 按设计等推送轮次 | 不动，到期自然装 |
| `cloud-init` 23.2.2 → 26.2 | 大版本跳跃（跨 3 年），只在开机时跑 | 评估后跳过：零安全收益，却影响重启行为 |

升级后复核：`docker compose version` = v5.7.1、五容器 healthy、`/health/ready` 200、无 failed 单元。

### 19.4 源站只允许 Cloudflare 回源（M-2 真正落地）

**问题**：`DOCKER-USER` 链是空的，源站对全世界开放。任何人知道 `47.253.81.6` 就能绕过
Cloudflare——WAF、DDoS 清洗、Bot 规则、以及 11-12 刚做的「按真实 IP 限流」全部失效。

**为什么开 UFW 解决不了**：docker 发布端口走 nat 表的 DNAT + `DOCKER` 链，**不经过 UFW 的 INPUT**。
UFW 只保护宿主机自己的 socket。真正的位置是 docker 官方留给用户的 `DOCKER-USER` 钩子
（在 `FORWARD` 里、docker 自己的规则之前求值）。

**实现**（都进仓库，可 review、可回滚）：

| 文件 | 作用 |
| --- | --- |
| `deploy/ecs/firewall-cloudflare-only.sh` | `apply` / `refresh` / `status` / `revert` 四个子命令，幂等 |
| `deploy/ecs/cloudflare-ranges.txt` | 22 个官方网段（与 nginx 的 `set_real_ip_from` 逐条一致） |
| `deploy/ecs/veripaper-firewall.service` | `After`/`PartOf=docker.service`：docker 重启会重建空链，必须跟着重放 |
| `deploy/ecs/veripaper-firewall.timer` | 每周一 04:30 重新下载网段并重放（CF 增网段而漏更新 → 部分用户间歇 522） |
| `deploy/ecs/veripaper-firewall-watchdog.{service,timer}` | **每 15 分钟**检查规则是否还在；缺失才重建（正常时零改动，不制造 flush 窗口） |

规则形态（关键在于 `-i eth0`）：

```
-A DOCKER-USER -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
-A DOCKER-USER -i eth0 -s <CF 网段> -p tcp -m multiport --dports 80,443 -j RETURN   × 22
-A DOCKER-USER -i eth0 -p tcp -m multiport --dports 80,443 -j DROP
-A DOCKER-USER -j RETURN
```

同时启用 UFW 作为**宿主机层面**的纵深防御：`default deny incoming`，只放行 22/80/443
（数据库/Redis 本就不发布端口，其余端口现在也进不来）。

**为什么要看门狗**：`PartOf=docker.service` 只是「docker 重启时顺手重放」，一旦重放失败，
表现是**源站重新对全世界开放且没有任何报错**——没有任何告警会响。所以再加一层分钟级巡检：
规则在就什么都不做（连 flush 都不做，避免瞬时 fail-open），规则没了就重建。

### 19.5 ⚠️ 两个坑（都是我自己踩的，都写进 AGENTS 铁律）

**坑 1：`DOCKER-USER` 是双向的，只按端口写 DROP 会掐掉容器出网。**
第一版规则是 `-p tcp --dports 80,443 -j DROP`，没限定入接口。结果容器调用外部 HTTPS 直接
`urllib.error.URLError: <urlopen error [Errno 101] Network is unreachable>` —— 因为容器出网
的包同样经过 `FORWARD → DOCKER-USER`，源地址是 `172.18.0.x`（不在 CF 网段）→ 命中 DROP。
这种故障从外面看**完全正常**（网站能开、健康检查 200），只有真正调模型/外部 API 时才报错。
修法：所有限制规则加 `-i <公网网卡>`（由默认路由推导，本机 `eth0`），并在脚本里自检
「DROP 规则必须带 `-i`」，缺了直接拒绝生效。

**坑 2：本机有透明代理，`--resolve` 直连测试是假的。**
改前用 `curl --resolve api.veripaper.me:443:47.253.81.6` 测「源站是否能被直连」，
拿到 200 —— 但响应头里 `Server: cloudflare` + `CF-RAY` 说明请求根本是经 Cloudflare 回来的：
环境里有 `http_proxy=http://127.0.0.1:xxxxx`（且 SDK 层还有透明代理），`--resolve` 被忽略。
**判据：直连测试的响应里出现 `CF-RAY` 或 `Server: cloudflare`，就说明没直连。**
改用两种不受本机代理影响的办法：① 服务器自身打自己的公网 IP（hairpin）；② 第三方节点探测。

### 19.6 本次实测证据（2026-11-12 14:20 JST）

| 检查点 | 结果 |
| --- | --- |
| SSH 新会话 | 登录成功（改配置后多次复连均正常） |
| X11 转发 | 请求被拒（`xauth key data not generated`），远端 `DISPLAY=none` |
| `sshd -T` | `x11forwarding no` / `maxauthtries 3` / `logingracetime 30` / `clientaliveinterval 300` |
| `.env*` | 只剩 `.env`、`.env.example`（键名比对确认无唯一键丢失） |
| 系统包 | 升 11 个，`docker compose` v5.7.1，无 `reboot-required`，无 failed 单元 |
| **源站 hairpin 直连** | **失败**（`000`），DROP 计数 0 → 7（SYN 被丢弃） |
| **第三方节点探测 443** | **0 / 4 节点可连** |
| 第三方节点探测 22（对照） | 3 / 4 节点可连（证明探测本身有效） |
| 经 Cloudflare | `api` 200、站点 200 |
| 回环自检 | `health/ready` 200（不受 `-i eth0` 影响） |
| 容器出网（修复后） | 200（此前为 `Network is unreachable`） |
| 容器互通 | backend → postgres:5432 可连 |
| 影响面 | 出网中断窗口约 3 分钟，后端日志 0 条网络错误、0 条 5xx |
| 看门狗·正常态 | 手工触发 `watch`：**退出码 0、无输出、规则行数 20 → 20（未改动）** |
| 看门狗·自愈 | 手工 `iptables -F DOCKER-USER` 模拟 docker 重启 → 再次触发 `watch`：**DROP 规则与 15 条 v4 放行全部重建，DROP 带 `-i eth0`** |
| 定时器 | `veripaper-firewall-watchdog.timer` 下一次 15:00（15 分钟粒度）；`veripaper-firewall.timer` 下一次 11-16 04:30 |
| 部署一致性 | 服务器 `/usr/local/sbin/veripaper-firewall.sh` 与仓库文件 sha256 完全相同（`63394a03…`） |

### 19.7 排查命令

```bash
# 规则是否还在（docker 重启后若 unit 没重放，这里会变空）
/usr/local/sbin/veripaper-firewall.sh status
iptables -L DOCKER-USER -v -n | head -8      # 看 DROP 的计数是否在涨

# 强制重放 / 临时放开（应急）
systemctl restart veripaper-firewall.service
/usr/local/sbin/veripaper-firewall.sh revert   # 去掉限制，恢复允许直连

# 网段与 nginx 是否一致（不一致会导致限流按错的 IP 计数）
grep -c set_real_ip_from deploy/ecs/nginx-api-tls.conf
grep -c . /etc/veripaper/cloudflare-ranges.txt

# 定时器（两个：看门狗 15 分钟 / 网段刷新每周）
systemctl list-timers 'veripaper-firewall*'
systemctl start veripaper-firewall-watchdog.service   # 手工巡检一次（正常时无输出）
journalctl -u veripaper-firewall-watchdog.service -n 20 --no-pager
```

## 20. 监控告警：站内自检 + 站外探测（Issue #11 C 组，2026-11-12）

此前只有 `/health/ready` 一个端点，**没有任何采集器与通知渠道**：服务挂了没人知道，
磁盘满了没人知道，备份连续失败也没人知道。

### 20.1 为什么必须是两层

| 层 | 位置 | 覆盖的故障 | 覆盖不了 |
| --- | --- | --- | --- |
| 站内自检 | 服务器 systemd timer（每 2 分钟） | 业务 5xx、磁盘、容器、备份、依赖不健康 | 整机宕机、断网、安全组改错——**它自己也发不出通知** |
| 站外探测 | GitHub Actions（每 5 分钟） | 整机/网络/源站不可达 | 单机内部指标（磁盘、备份、容器） |

单靠任何一层都有系统性盲区。

### 20.2 站内自检（`ops/watchdog.py`）

安装位置 `/usr/local/lib/veripaper-ops/`（**保留原文件名**：`probe_public.py` / `notify.py`
要 `from watchdog import send_wecom`，改名会直接 ImportError——本次踩过）。

| 检查项 | 判据 | 严重度 |
| --- | --- | --- |
| `api_health` | 回环 `/api/v1/health/ready` 非 200，或 `checks` 有非 ok | 红 |
| `error_rate` | 窗口内 5xx 占比 ≥ 20% 且请求数 ≥ 20 | 红 |
| `disk` | `/` ≥ 85% / ≥ 92% | 橙 / 红 |
| `containers` | 有容器非 `Up` 或含 `unhealthy` | 红 |
| `backup` | 最近一次成功备份 > 30 小时（备份是每日） | 橙 |

四个设计决定（都有具体理由，不是偏好）：

1. **只用标准库**：检查器必须活在被检查对象之外，且不能因依赖升级而静默失效。
   用系统 `/usr/bin/python3`，不碰项目虚拟环境。
2. **容器 IP 运行时解析**（`docker inspect`）：写死会在容器重启后让「5xx 比例」这项
   **永久静默**——静默的检查比没有检查更危险。
3. **5xx 做差时处理计数归零**：后端重启会让累计计数器变小，直接相减会得到负数占比。
4. **状态落盘去重**：同一故障只推一次，持续 180 分钟再提醒，恢复时推「已恢复」。

未配置 webhook 时写 journal 并返回 0——不能因为「还没配通知」让 systemd 每分钟报一次 failed。

### 20.3 站外探测（`.github/workflows/uptime.yml`）

每 5 分钟从 GitHub 机房探 `api.veripaper.me/api/v1/health/ready` 与 `veripaper.me`：

- **必须解析 JSON 且 `status=ready`**：Cloudflare 的错误页/挑战页也是 HTTP 响应，
  只看状态码会把「源站挂了被 CF 挡成 502」误判成正常，也会把挑战页当成故障；
- **连续 3 次（间隔 20 秒）失败才报**：避开部署瞬间与边缘抖动；
- 失败 → 企业微信 + 自动开 Issue（label `uptime-alert`）；恢复 → 评论并关闭该 Issue + 恢复通知。
  Issue 状态本身就是去重手段（不需要额外存储）。

`workflow_dispatch` 带 `api_url` / `site_url` 两个可选输入，用来**真实演练故障路径**——
这是本次验证「失败→开 Issue→恢复→关 Issue」闭环的办法，平时留空。

### 20.4 启用通知（还差你两步）

代码已上线，但**通知渠道还没填**（当前只在 journal 里记录）。两步，都在你手上：

```bash
# 1) 服务器：填企业微信群机器人地址（群设置 → 群机器人 → 添加）
ssh root@47.253.81.6
sudo vi /etc/veripaper/notify.env        # WECOM_WEBHOOK_URL=https://qyapi.weixin.qq.com/...
sudo systemctl restart veripaper-watchdog.timer

# 2) 仓库 secret：让站外探测也能推送
gh secret set WECOM_WEBHOOK_URL --repo sakura-del/Multi-agent-collaborative-automation-task-assistant

# 验证：故意让探测失败一次，应当收到群消息 + 自动 Issue
gh workflow run uptime.yml -f api_url="https://api.veripaper.me/api/v1/health/nope"
```

### 20.5 本次实测证据（2026-11-12 15:00 JST）

| 检查点 | 结果 |
| --- | --- |
| 站内·真实一轮 | 退出 0，`state.json` 记录 `all=14 / 5xx=0 / 4xx=3`（读到真实计数） |
| 站内·日报渲染 | 正常输出「累计请求 14，其中 5xx 0。磁盘 50%（剩 17.8 GB）」 |
| 站内·定时器 | `veripaper-watchdog.timer` 每 2 分钟（下一次 14:57）；`-digest.timer` 次日 09:00 |
| 站内·单元执行 | `systemctl start` → `Result=success` |
| 站内·告警链路 | 指向死端口 → 收到 1 条（220 字节）；**同一故障再跑不发**；恢复后收到 1 条（90 字节） |
| 站外·正常 | `workflow_dispatch` 成功（11s）：`API 正常 / 站点 正常`，未建 Issue |
| 站外·故障 | 用 `-f api_url=.../health/nope` 触发，50s 后自动创建 **Issue #13** |
| 站外·恢复 | 再跑一次正常探测 → Issue #13 自动评论「探测已恢复正常」并**关闭** |
| 配置权限 | `/etc/veripaper/notify.env` = `600 root:root`，模板在仓库 `ops/notify.env.example` |

### 20.6 排查命令

```bash
# 站内：最近几轮发生了什么
journalctl -u veripaper-watchdog.service -n 30 --no-pager
systemctl list-timers 'veripaper-watchdog*'
cat /var/lib/veripaper-watchdog/state.json          # firing 里就是当前未恢复项

# 手工跑一轮 / 预览日报（不发网络请求）
/usr/bin/python3 /usr/local/lib/veripaper-ops/watchdog.py --dry-run
/usr/bin/python3 /usr/local/lib/veripaper-ops/watchdog.py --digest --dry-run

# 站内：临时把某个阈值调低看会不会触发（不落盘，只影响这一轮）
WATCHDOG_DISK_WARN_PCT=1 /usr/bin/python3 /usr/local/lib/veripaper-ops/watchdog.py --dry-run

# 站外：手工触发 / 看历史
gh workflow run uptime.yml
gh run list --workflow=uptime.yml --limit 5
```

### 20.7 已知局限（写出来，避免误以为有 SLA）

- **GitHub cron 有 5~15 分钟不定延迟**，这是分钟级可用性监控，不是秒级 SLA 监控；
- **公开仓库的 schedule 在闲置 60 天后会被自动停用**，长期无提交时需手动触发一次；
- **没有长期指标存储**：`/metrics` 存在但没有 Prometheus 抓取，只能看"当下"，
  查不了"上周三 14:00 的 5xx 是多少"。真要做趋势，再上 Prometheus；
- 站内自检的 5xx 判断依赖后端进程存活（进程死了这一项拿不到数据，由 `api_health` 兜）。
