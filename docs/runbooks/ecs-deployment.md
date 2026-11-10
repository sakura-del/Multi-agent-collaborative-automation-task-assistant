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
| Bot Fight Mode | **未开**：会对 API 客户端发 JS 挑战，纯 API 域名开启风险大于收益 |
| Security.txt / AI Labyrinth / Turnstile | 未做，低优先级 |

## 10. 尚未完成 / 后续

- 七牛 AK/SK 曾多次出现在对话与终端输出中，**建议轮换**（改 `.env` → 重启 backend 与 db-backup）；
- 真实嵌入模型（`INSTALL_RAG_MODELS=true`）未在该镜像启用，检索质量仍是 Mock 嵌入；
- `/metrics` 已在网关屏蔽；如需采集请走内网或加鉴权。
