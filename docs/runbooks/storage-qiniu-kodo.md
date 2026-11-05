# 对象存储（七牛云 Kodo）与 Supabase 每日备份

> 状态（2026-11-05）：**代码已就绪，默认仍是 `local`**——没有填入真实 AK/SK，
> 线上行为与改造前完全一致。填 `.env` 并重启即切换，无需重建镜像。

## 1. 双模式设计

| 模式 | `knowledge_doc.file_path` | 解析 | 下载 |
| --- | --- | --- | --- |
| `local`（默认） | 本地绝对路径 `/app/data/uploads/<user>/<uuid>.pdf` | 直接读 | `FileResponse` 流式 |
| `s3` | 对象键 `uploads/<user>/<uuid>.pdf` | 先下载到临时文件，解析完删除 | **307 跳转**预签名直链 |

**安全校验与后端无关**：扩展名白名单、30 MB 流式限流、PDF/DOCX/TXT 文件签名
全部在写临时文件阶段完成，**校验通过才上传**——坏文件不会在桶里留下垃圾对象。

## 2. 配置

`config/default.yaml` 已有 `storage` 段（默认 local、空凭据）；实际值写 `.env`（600）：

```dotenv
STORAGE__BACKEND=s3
STORAGE__S3_ENDPOINT=https://s3-cn-east-1.qiniucs.com
STORAGE__S3_REGION=cn-east-1
STORAGE__S3_BUCKET=veripaper-storage
STORAGE__S3_ACCESS_KEY_ID=<七牛 AK>
STORAGE__S3_SECRET_ACCESS_KEY=<七牛 SK>
STORAGE__S3_PREFIX=uploads
STORAGE__S3_ADDRESSING_STYLE=virtual
STORAGE__PRESIGN_EXPIRES_SECONDS=900
```

| 区域 | Endpoint |
| --- | --- |
| 华东-浙江 | `https://s3-cn-east-1.qiniucs.com` |
| 华北-河北 | `https://s3-cn-north-1.qiniucs.com` |
| 华南-广东 | `https://s3-cn-south-1.qiniucs.com` |

> **两个必须**（否则 `SignatureDoesNotMatch`）：`s3v4` 签名 + **virtual-hosted**
> 寻址（`bucket.endpoint/key`）。代码已内置，改 `STORAGE__S3_ADDRESSING_STYLE=path`
> 会直接失败。

> ⚠️ **compose 的 `environment` 是白名单**：`.env` 里配了 `STORAGE__*`，还必须
> 在 `docker-compose.yml` 的 backend 段里列出（已加），否则容器里读不到、静默回落 local。

## 3. 启用与验证

```bash
# 1) 改 .env 后重启（不需要重建镜像：boto3 已在镜像依赖里）
docker compose up -d backend

# 2) 确认容器读到了配置
docker compose exec -T backend python -c \
  "from app.core.config import settings; print(settings.storage.backend, settings.storage.s3_bucket)"

# 3) 浏览器上传一份 PDF → 七牛控制台应出现 uploads/<user_id>/<uuid>.pdf
# 4) 任务里点下载（或直接调接口）→ 应 307 跳到带签名的直链
curl -i http://127.0.0.1:48080/api/v1/knowledge/documents/<doc_id>/download \
  -H "Authorization: Bearer <token>" | head -n 5
```

## 4. Supabase 每日备份（profiles=prod）

```bash
# 首次构建（约 100MB：postgres:17 + awscli）
docker compose --profile prod build db-backup
# 启动（开源用户不加 --profile prod 时完全不受影响）
docker compose --profile prod up -d db-backup

# 立即手动跑一次，不用等次日
docker exec -it veripaper-db-backup sh -c 'RUN_ONCE=1 /usr/local/bin/backup.sh'
# 七牛控制台应出现 db-backups/<ts>.sql.gz
```

`.env` 需要：

```dotenv
SUPABASE_PG_HOST=aws-0-ap-southeast-1.pooler.supabase.com
SUPABASE_PG_PORT=5432          # session pooler！6543 的 transaction pooler 不适合 pg_dump
SUPABASE_PG_DB=postgres
SUPABASE_PG_USER=postgres.<project-ref>
SUPABASE_PG_PASSWORD=<密码>
BACKUP_KEEP_DAYS=7
```

**为什么不用 `eeshugerman/postgres-backup-s3`**：该镜像在 Docker Hub 上只有
≤16 的 tag（实测 16/15/14/…），而 Supabase 是 **PostgreSQL 17.11**，
`pg_dump 16` 会直接报 `server version mismatch`。自建镜像用官方 `postgres:17`，
版本天然匹配。

### 恢复注意

备份包含 `knowledge_vectors`（`vector` 类型），恢复到**新库**前必须先执行：

```sql
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions;
```

否则 `CREATE TABLE ... embedding extensions.vector(1024)` 会因类型不存在失败。

## 5. 回滚

```bash
# 切回本地磁盘（历史 S3 对象保留，不删）
STORAGE__BACKEND=local  # 改 .env → docker compose up -d backend
```

## 6. 已知边界

| 场景 | 行为 |
| --- | --- |
| 迁移前上传的旧文档（`file_path` 是本地绝对路径） | `localize` 先判本地存在，能正常解析与下载 |
| 切到 S3 后删除旧文档 | 存储层按 S3 分派，**删不到本地旧文件**（记录会被删，文件残留在磁盘）——需要回收时写一次性脚本 |
| 容器内 `/app/data/uploads` 体积 | S3 模式仍会保留历史本地文件；新上传不再占用 |
| 预签名有效期 | 默认 900s，上限 604800s（7 天） |
