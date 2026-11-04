# Supabase 迁移手册（业务库 + 向量库 pgvector）

> **状态：已切换到新加坡项目（2026-11-04）。** 线上运行在
> `aws-0-ap-southeast-1.pooler.supabase.com:6543`（项目 ref `eowgihkicleusmvvoxle`）
> + `RAG__VECTOR_BACKEND=pgvector`；旧爱尔兰项目（`fiooponfjzzaqfvicmwy`）保留待手动删除。
> 切换前的回滚点在服务器 `.env.bak-sg-20261104-103611`（回爱尔兰）与
> `.env.bak-supabase-20261103-182904`（回本地库 + chroma）。
>
> ⚠️ **换区域只带来 ~15% 改善**：实测服务器到新加坡仍是 **~480ms/次数据库往返**、
> TLS 握手 ~1.9s（到 github 也要 2s），说明瓶颈是**这台服务器本身的国际链路**，
> 不是 Supabase 地域。应用层 `/tasks` 4.2s、检索 6.2s 属于「链路+往返次数」的必然结果。
> 想继续优化只有三条路：① 后端也部署到云上近邻区域；② 业务库用本地 Postgres
> （Supabase 作备份）；③ 继续压应用层往返次数（收益有限）。
> 验收：`/health/ready` = ready（database/vector ok，无 chroma 键）；demo 检索 200 且有结果；
> id 级一致性巡检 `consistent=true, chunks=28341, vectors=28341, 缺失/孤儿=0`。
> **回滚**：服务器 `~/projects/multi-agent-assistant/.env.bak-supabase-20261103-182904`
> （切回 chroma + 本地库，覆盖回 `.env` 并 `docker compose up -d backend`）；
> Chroma 容器/卷与本地 postgres 均未删，dump 备份在 `~/maa-final-20261103.dump`。

> 对应代码：`backend/app/knowledge/services/vector_backends/`（新后端）、
> 迁移 `d5a8c1e3f7b2`（`knowledge_vectors` 表）、
> 脚本 `backend/scripts/migrate_vectors_to_pgvector.py`。
>
> **原则：分阶段、每步可回退、源数据不销毁。**

---

## 0. 一句话

业务库换 `DATABASE__URL`，向量库换 `RAG__VECTOR_BACKEND=pgvector`；
两者都有独立回滚开关。Chroma 容器与 `maa-chroma-data` 卷、本地 postgres 库
在验收期（建议 30 天）内**都不删**。

---

## 1. 容量闸门（先做，别等写满才发现）

| 项 | 估算 | 说明 |
| --- | --- | --- |
| 裸向量 | 28344 × 1024 × 4B ≈ **116 MB** | bge-large-zh 1024 维，float32 |
| HNSW 索引 | 约 1.5–2× 向量体积 | m=16 / ef_construction=64 |
| 向量表合计 | **约 250–350 MB** | 含正文与元数据 |
| Supabase 免费档 | **500 MB** | 业务库与向量库**同库**时共用这 500 MB |

先量本地实际占用来判断装不装得下：

```bash
docker compose exec -T postgres psql -U maa -d multi_agent_assistant -c \
  "SELECT pg_size_pretty(pg_database_size(current_database())) AS db_size;"
docker compose exec -T postgres psql -U maa -d multi_agent_assistant -c \
  "SELECT pg_size_pretty(pg_total_relation_size('doc_chunk')) AS chunk_size;"
```

决策：
- 装得下 → 继续；
- 不够 → 升级档位，或把向量降精度为 `halfvec`（需要改迁移表的列类型，属于新任务），
  或向量库留在本地库（`RAG__PGVECTOR_DSN` 指向本地 pgvector，业务库仍迁 Supabase）。

---

## 2. 阶段 0：代码上线，行为不变

`RAG__VECTOR_BACKEND` **保持 `chroma`**，`DATABASE__URL` **保持本地**。

```bash
# 门禁（本地跑，逐条看 exit code）
cd backend
python -m black app tests evaluation
python -m ruff check --fix app tests evaluation
python -m mypy app evaluation
python -m pytest tests -q --cov=app --cov-fail-under=80
```

验证：现有知识库上传/检索/一致性巡检结果与迁移前一致。

**回滚**：`git revert`，零数据影响。

---

## 3. 阶段 1：业务库迁 Supabase

### 3.1 准备连接串（只进 `.env`，不要贴进文档/提交）

Supabase 控制台 → Project Settings → Database → Connection string：

- **优先 Pooler**（`aws-0-<region>.pooler.supabase.com`，端口 **6543** = transaction 模式）：
  支持 IPv4；直连 `db.<ref>.supabase.co:5432` 在新项目可能只有 IPv6。
- 用户名形如 `postgres.<project-ref>`，**不是** `postgres`。

`.env` 里写（示例，占位符别照抄）：

```dotenv
DATABASE__URL=postgresql+asyncpg://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres?sslmode=require
DATABASE__DISABLE_PREPARED_STATEMENTS=true
```

> ⚠️ transaction pooler 不支持 prepared statement。只关 asyncpg 一层不够，
> 本项目已同时关闭 SQLAlchemy 方言那层（`app/db/engine_options.py`）。
> 症状：复用连接后报 `prepared statement "__asyncpg_stmt_1__" does not exist`。

### 3.2 dump → restore

```bash
# 本地（服务器）导出，确认 revision
docker compose exec -T postgres pg_dump -U maa -d multi_agent_assistant \
  --no-owner --no-privileges -Fc -f /tmp/maa.dump
docker compose cp postgres:/tmp/maa.dump ./maa.dump

# 导入 Supabase（本机需要 psql；也可用 Supabase SQL Editor 分批执行纯 SQL dump）
pg_restore --no-owner --no-privileges --clean --if-exists \
  -d "postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres" maa.dump
```

### 3.3 迁移版本对齐

```bash
cd backend
DATABASE__URL="postgresql+asyncpg://postgres.<ref>:...@...pooler.supabase.com:6543/postgres?sslmode=require" \
DATABASE__DISABLE_PREPARED_STATEMENTS=true \
python -m alembic current      # 期望 b7d4e9f1c6a2 或 d5a8c1e3f7b2
python -m alembic upgrade head # 建 knowledge_vectors + HNSW
```

`d5a8c1e3f7b2` 会：解析 `pg_extension` 里 `vector` 的 schema（Supabase 通常是
`extensions`）→ 建表（`PRIMARY KEY (collection, id)`）→ 建 HNSW 与两个 btree 索引。
**downgrade 只删表，不删扩展。**

### 3.4 验收

```sql
-- 逐表行数比对（本地 vs Supabase）
SELECT relname, n_live_tup FROM pg_stat_user_tables ORDER BY relname;
```
```bash
curl -s http://127.0.0.1:48080/api/v1/health/ready
# 期望 checks.database = ok
```

**回滚**：`DATABASE__URL` 改回本地 postgres → `docker compose up -d backend`。
本地库全程不动，Supabase 是新增副本。

---

## 4. 阶段 2：向量数据搬运（业务库可以仍在本地）

### 4.1 切换后端（先不切业务库）

```dotenv
RAG__VECTOR_BACKEND=pgvector
# 留空 = 复用 DATABASE__URL；只迁向量库时显式指向目标库
RAG__PGVECTOR_DSN=postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres?sslmode=require
RAG__PGVECTOR_DISABLE_PREPARED_STATEMENTS=true
```

> 向量后端用的是**同步 psycopg**，DSN 里不要带 `+asyncpg`（脚本会自动去掉）。

### 4.2 干跑 → 搬运 → 校验

```bash
docker compose exec -T backend python -m scripts.migrate_vectors_to_pgvector --dry-run
docker compose exec -T backend python -m scripts.migrate_vectors_to_pgvector --json
# 中断后续跑：
docker compose exec -T backend python -m scripts.migrate_vectors_to_pgvector --start-offset 5000
# 只复核：
docker compose exec -T backend python -m scripts.migrate_vectors_to_pgvector --verify-only --json
```

校验三项（任一不过脚本 exit 1）：
1. 行数（Chroma count == pgvector count）；
2. id 集合（缺失/孤儿各取 20 条样本）；
3. 样例检索 top-k 重叠率 ≥ 0.8。

> 第 3 项是唯一能抓住「向量搬错列/维度错位」的检查——数量与 id 全对、
> 但向量内容串了，前两项都发现不了。

### 4.3 一致性巡检

```bash
docker compose exec -T backend python -m scripts.check_kb_consistency
```
`vector_index.chroma_id` 与 pgvector 的 `id` 是同一批值（迁移保 id），集合交集应为全量。

**回滚**：`RAG__VECTOR_BACKEND=chroma` → 重启 backend。Chroma 数据一直在卷里。

---

## 5. 阶段 3：业务库切换（维护窗口）

顺序很重要：**先停写，再增量 dump，最后切 DSN**。

```bash
docker compose stop backend nginx          # 停写（保留 postgres/redis/chroma）
# 增量 dump（阶段 1 之后的新数据）
docker compose exec -T postgres pg_dump -U maa -d multi_agent_assistant \
  --no-owner --no-privileges -Fc -f /tmp/maa-incr.dump
# 恢复（--clean 会重建对象；确认已备份再执行）
pg_restore --no-owner --no-privileges --clean --if-exists -d "<supabase dsn>" maa-incr.dump
```

改 `.env` 后启动：

```bash
docker compose up -d backend nginx
curl -s http://127.0.0.1:48080/api/v1/health/ready
# {"status":"ready","checks":{"database":"ok","redis":"ok","vector":"ok"}}
```

冒烟（真实浏览器）：登录 → 知识库检索 → 任务创建 → 综述生成 → 引用回溯。

**回滚**：`DATABASE__URL` 切回本地 → `docker compose up -d backend nginx`。
若期间已在 Supabase 产生新写入，反向 dump 回本地后再切（不要直接丢弃）。

---

## 6. 观察期与清理

- 观察 7 天：错误率、检索命中质量、`/metrics` 中检索延迟；
- 30 天内保留：Chroma 容器与卷、本地 postgres 库；
- 确认无回滚需求后，再：
  - 删 `chroma` 服务与 `maa-chroma-data` 卷；
  - 关闭 `RAG__CHROMA_*` 配置；
  - 把 `RAG__VECTOR_BACKEND` 默认值改为 `pgvector`（单独提交）。

---

## 7. 常见故障

| 症状 | 原因 | 处理 |
| --- | --- | --- |
| `prepared statement "__asyncpg_stmt_N__" does not exist` | pooler transaction 模式 + 未关语句缓存 | `DATABASE__DISABLE_PREPARED_STATEMENTS=true`（向量侧 `RAG__PGVECTOR_DISABLE_PREPARED_STATEMENTS=true`） |
| `pgvector 扩展未安装` | 目标库没建扩展 | `CREATE EXTENSION IF NOT EXISTS vector`（Supabase 在 `extensions` schema） |
| `向量表 'knowledge_vectors' 不存在` | 没跑迁移 | `alembic upgrade head` |
| `向量维度不一致：表是 1024 维，模型是 64 维` | 用了 Mock 嵌入连真表 | 生产用真实 BGE（`RAG_EMBEDDING_MOCK=0`），或另建 64 维测试表 |
| 健康检查 `vector: failed` 但 `database: ok` | `RAG__PGVECTOR_DSN` 指向了别的库/密码错 | 看日志 `pgvector_pool_created` 的 host/port/dbname（密码已脱敏） |
| Supabase 突然连不上 | 免费档闲置自动 pause | 控制台恢复项目；演示前预热 |
| 检索变慢 | HNSW 索引缺失（批量导入前被删） | `REINDEX INDEX ix_kv_embedding_hnsw` |
| 检索慢但索引存在（计划里是 Sort） | 走了 generic plan：`ORDER BY embedding <=> $1` 的距离依赖参数值 | 确认 `RAG__PGVECTOR_DISABLE_PREPARED_STATEMENTS=true`（默认已开） |
| **页面加载慢甚至前端超时（15s）** | **N+1 在远程库上爆炸**：SQL 服务端只花几十 ms，但每条 SQL 有 ~300ms 往返；旧任务列表对每任务补 2 条查询，20 任务 ≈ 90 条 ≈ 34s（2026-11-04 实测） | 已修：`list_tasks` 单条汇总查询（`79d1b60`，整页 1 条 SELECT，实测 5.1s）；**根治是换到低延迟区域**（Singapore），换区域后同样的代码 < 1s |
| 列表页仍慢但 SQL 只有 ~16 条 | 其中 12 条是 BEGIN/ROLLBACK（一次请求多个短会话）+ 每条 ~300ms 往返 | 应用层已到合理下限；换区域或升级套餐（同 region）才能继续压 |

---

## 8. 实测记录（2026-11-03，Supabase 真机）

| 项 | 实测值 |
| --- | --- |
| PostgreSQL | 17.11（aarch64） |
| pgvector | 0.8.2，装在 `extensions` schema |
| **切换后库大小** | **253 MB**（业务库 54 MB + 向量表 200 MB），500 MB 档还剩约 247 MB |
| **业务库** | user 27 / task 114 / doc_chunk 28341，与本地一致；`alembic` = `d5a8c1e3f7b2` |
| **一致性巡检** | `consistent=true`，28341/28341，缺失 0 / 孤儿 0，ids_verified=true |
| 表与索引 | `knowledge_vectors` + `ix_kv_embedding_hnsw` + `ix_kv_collection_doc/user` 均创建成功 |
| **新加坡项目实测** | 迁移后 `consistent=true`（28341/28341，缺失/孤儿 0），库 253 MB；`/tasks` **4.2s**、检索 **6.2s**、登录 2.2s（旧爱尔兰：5.0s / 6s+ / 2.4s） |
| **链路实测** | 服务器→新加坡 pooler：建连 1.7s、**每查询 0.48s**；HTTPS TLS 握手 1.9s（到 github 也 2.0s）→ 瓶颈是服务器国际出口，不是地域 |
| 维度探测 | `embedding vector(1024)` → `atttypmod = 1024` |
| 余弦检索 | 同向 distance 0（score 1.0）、正交 distance 1（score 0.0） |
| 多用户隔离 | `WHERE user_id = 'u1'` 在截断前生效，`u2` 的行不出现 |
| HNSW | 常量查询向量 → `Index Scan using ix_kv_embedding_hnsw` |

只有真库才会暴露的坑（均已修并加回归测试）：

1. **`atttypmod - 4` 是错的**：pgvector 的 typmod **直接就是维度**（不像 varchar 加
   `VARHDRSZ(4)`）。按 varchar 习惯写 `- 4` 会把 1024 读成 1020，随后每次写入/检索
   都被自己的维度校验拒绝。
2. **通用计划用不上 HNSW**：`ORDER BY embedding <=> $1` 的距离依赖参数值，服务端一旦
   走 generic plan 就退化成 `Seq Scan + Sort`（实测：常量字面量走 Index Scan，
   非恒定表达式走 Sort）。所以 `prepare_threshold=None` 默认开。
3. **Supabase pooler 会话 `search_path` 是空的**：`to_regclass('knowledge_vectors')`
   返回 NULL → 明明表在 public 里却报「表不存在」。表名必须**限定 schema**
   （`RAG__PGVECTOR_TABLE=other.tbl` 可覆盖）。
4. **`{cast}` 不能顶替占位符**：`VALUES (%s, %s, ::vector, ...)` 占位符比参数少 1，
   真机报 `the query has 10 placeholders but 11 parameters were passed`；
   必须写 `%s::vector`。
5. **chromadb 的 get() 返回 numpy 数组**：`payload.get("embeddings") or []` 会抛
   `truth value of an array ... is ambiguous`（校验阶段崩）。
6. **`.env`/脚本的 CRLF**：Windows 侧写入后 `postgres\r` 让连接串的 dbname 变成
   `postgres\r`（报 `database postgres does not exist`），密码同理。上传后先
   `sed -i 's/\r$//'`。
7. **asyncpg 两个缓存参数的传法（SQLAlchemy 2.0.51 实测）**：
   - 方言层 `prepared_statement_cache_size=0` **只能放 URL 查询串**（engine kwarg 被
     `create_engine` 拒收）；
   - asyncpg 层 `statement_cache_size=0` **只能放 `connect_args` 且必须是 int**
     （URL 查询串会以字符串传入 → `TypeError: '<' not supported ...`）；
   - 只关任何一层都不够（`does not exist` / `already exists` 两种都出现过）。
8. **网络质量**：国内服务器 → Supabase eu-west-1 单次建连 **6~8s**，长连接会被
   `SSL SYSCALL error: EOF detected` 断开：
   - 连接/池超时给到 15s+；
   - `pg_restore` **不要用 `--single-transaction`**（长事务必断），逐语句提交 +
     `PGKEEPALIVES=1 PGKEEPALIVESIDLE=30`，失败就重跑（`--clean` 幂等）；
   - 2.8 万条向量导入约 5 分钟，最后一批附近断连属正常，重跑补齐即可。

---

## 9. 单实例统一管理验收（2026-11-04）

**结论：PASS（有条件）**。同一 Supabase 实例内，13 张核心表 + 向量表可统一管理、一致且可运行。

### 9.1 两个脚本的运行结果

| 脚本 | 关键输出 | 结论 |
| --- | --- | --- |
| `reindex_vectors.py --dry-run` | `planned=28341` | 计划数量与 `doc_chunk`（ready）一致 |
| `reindex_vectors.py --limit 200` | `indexed=200 failed_batches=0 elapsed_ms=6906` | 小样本演练通过，一致性仍 true |
| `reindex_vectors.py`（全量） | `planned=28341 indexed=28341 failed_batches=0 elapsed_ms=285204`（57 批，4 分 45 秒） | **重建成功** |
| `check_kb_consistency.py --json` | `consistent=true, expected=28341, actual=28341, missing=0, orphan=0, ids_verified=true` | **一致** |

**重建前后检索等价性**：同一组 5 条查询的 top-10 命中 id **完全一致（重叠率 1.000）**，
证明「重写向量」没有改变检索语义（Mock 嵌入下应为确定性结果）。

### 9.2 13 张核心表的核验（口径：`dev-plan-v2.md:165`）

| 表 | 行数 | 表 | 行数 |
| --- | --- | --- | --- |
| user | 27 | knowledge_doc | 101 |
| task | 101 | doc_chunk | 28341 |
| subtask | 673 | vector_index | 28341 |
| task_dag | 95 | agent | 0 |
| execution_log | 3984 | agent_interaction | 0 |
| validation_result | 629 | human_feedback | 0 |
| failure_case | 0 | | |

- **外键孤儿检查 8 项全为 0**：subtask→task、task_dag→task、execution_log→task、
  validation_result→subtask、doc_chunk→knowledge_doc、vector_index→doc_chunk、
  agent_interaction→task、task→user。
- **同实例取证**：`current_database()=postgres`，pgvector 扩展在 `extensions` schema；
  业务连接与向量连接指向同一 host/库（`.env` 两个 DSN 同源）。
- **关系型 ↔ 向量 id 集合双向差为 0**：`vector_index.chunk_id`（uuid）与
  `knowledge_vectors.id`（text，`::text` 后比对）完全对齐。
- **正文抽样 20 条**：`doc_chunk.content` 与 `knowledge_vectors.document` 零不一致。

### 9.3 期间发现的问题与处置

| 问题 | 事实 | 处置 |
| --- | --- | --- |
| reindex 逐条 UPDATE | 28341 × 0.48s ≈ **3.8 小时**，事实上不可执行 | 改每批一条批量 UPDATE（57 次），实测 4 分 45 秒（`996fb4a`） |
| **免费档容量超限** | 反复全量 upsert 后库涨到 **528 MB / 500 MB（114%）**：`knowledge_vectors` TOAST 297 MB、索引 120 MB（物理膨胀，死元组已清但不缩文件） | `TRUNCATE` 后从 Chroma 重导入（200s），库回到 **262 MB（52%）**；索引/TOAST 均回到正常体积 |
| 重复启动多个 reindex | 两次 `exec -d` 叠加，同一批行互相抢锁、放大膨胀 | 改为「先数 `/proc` 再启动」，并记录「进程检测不要用会被自身命令匹配的关键字」 |
| 长事务会丢进度 | `vector_index` 的批量 UPDATE 只在**全部批次结束后**才 commit | 本次无实际损失（值本就等于 chunk_id）；如需断点续跑应改为每批提交 |

### 9.4 免费实例限制结论

- **存储 500 MB 是硬约束**：一次全量 upsert 会让向量表临时膨胀 2~2.6 倍
  （本次 200 MB → 466 MB）。**不要反复跑全量 reindex**；要跑就先 `TRUNCATE` 或接受临时超限。
- 超出配额时 Supabase 只告警不立即封库（本次超限期间读写仍正常），但会限制新建项目。
- 闲置会自动 pause（演示前预热）；连接走 transaction pooler 且必须关闭两层语句缓存。

---

## 10. 关键变量速查

| 变量 | 默认 | 作用 |
| --- | --- | --- |
| `RAG__VECTOR_BACKEND` | `chroma` | `chroma` / `pgvector`（回滚开关） |
| `RAG__PGVECTOR_DSN` | 空 | 空 = 复用 `DATABASE__URL` |
| `RAG__PGVECTOR_TABLE` | `knowledge_vectors` | 目标表 |
| `RAG__PGVECTOR_INDEX` | `hnsw` | `hnsw` / `ivfflat` |
| `RAG__PGVECTOR_POOL_MAX_SIZE` | 5 | 同步池上限（按并发检索量给） |
| `RAG__PGVECTOR_STATEMENT_TIMEOUT_MS` | 15000 | 慢查询兜底 |
| `RAG__PGVECTOR_DISABLE_PREPARED_STATEMENTS` | false | pooler 场景置 true |
| `DATABASE__SSL_REQUIRE` | false | DSN 未写 `sslmode` 时用它 |
| `DATABASE__DISABLE_PREPARED_STATEMENTS` | false | 业务库 pooler 场景置 true |
