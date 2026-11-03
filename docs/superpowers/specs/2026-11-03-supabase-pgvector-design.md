# 设计：向量库迁移到 Supabase PostgreSQL + pgvector

> 日期：2026-11-03 ｜ 状态：已实现（待真机验收）
> 迁移手册：[docs/runbooks/supabase-migration.md](../../runbooks/supabase-migration.md)

## 1. 问题与约束

向量库原实现是 Chroma（`chromadb==1.5.9` + compose 独立服务）。
目标是迁到 Supabase PostgreSQL + pgvector，且：

1. **接口零变更**：`VectorStore` 的同步方法签名、返回值、`score = 1 - cosine_distance`
   语义不能变——它被 `IndexManager`、`HybridRetriever`、`DocumentService`、
   `consistency`、`reindex`、`health` 六处直接依赖；
2. **离线测试不受影响**：2837 个单测跑在 SQLite + 内存向量库上，不能因为
   迁移把「本机没装 docker」变成红；
3. **可回滚**：出问题改一个环境变量切回 Chroma；
4. **凭证卫生**：DSN 只进 `.env`，日志必须脱敏。

## 2. 结构：门面 + 协议 + 双后端

```
VectorStore（门面，同步接口 + 得分换算 + 日志）
├── ChromaVectorBackend（原 233 行逻辑原样搬移，保留回滚/离线测试）
└── PgVectorBackend（psycopg3 同步池 + 原生 SQL）
```

后端选择优先级（高 → 低）：

1. 显式参数 `backend=`；
2. `persist_directory is not None` → `chroma`；
3. 配置 `RAG__VECTOR_BACKEND`（默认 `chroma`）；
4. 兜底 `chroma`。

**第 2 条必须在第 3 条之前**：既有测试全部用 `persist_directory=""` 走 Chroma
内存模式；若让配置优先，部署环境一配 `pgvector`，所有离线单测都会去连外部数据库。
默认值保持 `chroma` 也是有意为之——升级本身不改行为，切换/回滚都是显式动作。

## 3. 关键取舍

### 3.1 同步 psycopg3，而不是 asyncpg

改 async 需要动 `IndexManager` / `HybridRetriever` / `DocumentService` / API 四层，
违反「接口不变」。代价是查询期间阻塞事件循环——与迁移前的同步 Chroma HTTP 调用**一致**，
用 `connect_timeout=5s`、`statement_timeout=15s`、池 `timeout=5s` 封顶。
异步化列为后续独立任务，不在本次范围。

### 3.2 原生 SQL，不装 `pgvector` Python 包

- 不把 `vector` 类型塞进 `Base.metadata`：会让 `create_all` 与 autogenerate 抓瞎；
- 向量以文本字面量（`'[0.1,0.2,…]'`）作为**参数**传入并 `::"schema"."vector"` 转换，
  不需要逐连接注册类型适配器（pgbouncer 连接复用下更稳）。

### 3.3 单表 + `collection` 列，主键 `(collection, id)`

`clear()` 的语义是「只清当前 collection」。若用单列主键，多知识库命名空间
（测试里每个用例一个 collection）会互相覆盖。`reset()` 实现为
`DELETE ... WHERE collection = %s` 而不是 DROP/CREATE：
应用账号不需要 DDL 权限，也不会把 HNSW 索引一起丢掉重建。

### 3.4 DDL 只由 alembic 执行

`RAG__PGVECTOR_AUTO_MIGRATE` 默认 false，只给集成测试/临时库用。
生产走 `alembic upgrade head`（`d5a8c1e3f7b2`）。

### 3.5 维度冲突用「后端隔离」解决

测试用 `MockEmbeddingService(dimension=64)`，生产用 bge-large-zh（1024）。
两者走不同后端（Chroma 内存 / pgvector 真表），互不干扰。
pgvector 侧启动时读 `pg_attribute.atttypmod - 4` 校验维度，
不匹配直接抛 `VectorDimensionMismatchError`（而不是让 SQL 层抛
`expected 1024 dimensions` 让人猜）。

### 3.6 `remove_doc` 返回精确行数

Chroma 的 delete 不返回数量，历史实现用 before/after count 差值估算。
pgvector 用 `cursor.rowcount` 拿精确值——这是**改善**，不改变语义。

## 4. 索引选择：HNSW 而非 IVFFlat

| | HNSW | IVFFlat |
| --- | --- | --- |
| 训练 | 无需 | 需要数据分布（`lists` 要调） |
| 增量写入 | 友好 | 依赖聚类质量 |
| 2.8 万行 | 推荐 | 可用但不划算 |
| 内存 | 较高（约 1.5–2× 向量体积） | 较低 |

与检索一致用 `vector_cosine_ops`（`<=>` 余弦距离）。
索引与 opclass 一律从 `pg_extension` 解析 schema 后**限定引用**
（Supabase 默认装在 `extensions`，不依赖 `search_path`）。

## 5. 安全与异常

- `where` 过滤走**白名单**（`doc_id/user_id/doc_source/chunk_index` +
  `$eq/$ne/$in/$nin`），未知键/算子直接 `ValueError`——静默忽略会让
  `user_id` 过滤失效，等于跨用户串数据；
- 过滤值全部参数化（有注入用例锁住）；
- 异常分三类：`VectorStoreConfigError`（配置/表/维度）、
  `VectorStoreUnavailableError`（连接/超时 → API 503）、`VectorStoreError`（其余）；
- 日志只输出 host/port/dbname 与后端名，异常消息过 `redact_dsn()`。

## 6. 被否决的方案

| 方案 | 否决原因 |
| --- | --- |
| 直接用 asyncpg + 全链路 async | 要改四层调用方，超出「接口不变」约束，风险远大于收益 |
| 用 `pgvector` Python 包 + ORM 映射 | 干扰 `create_all`/autogenerate；逐连接注册类型在 pgbouncer 下更脆弱 |
| 迁移即重算 embedding | 烧模型额度与时间，且无法区分「迁移问题」与「模型差异」；原样搬可逐条核对 |
| 直接删掉 Chroma | 失去分钟级回滚；验收期内必须保留 |
| 每知识库一张表 | DDL 随业务增长（测试每用例一个 collection），应用需要建表权限 |
| 测试也连真 pgvector | 2837 个测试会硬依赖外部服务，CI/离线开发直接崩 |

## 7. 遗留与后续

- **真机验收部分完成**：2026-11-03 在 Supabase 真库建好 `knowledge_vectors` + HNSW，
  并实测索引、余弦距离、多用户过滤（记录见迁移手册 §8）；
  **存量数据搬运与服务端 DSN 配置仍未做**（本地无 docker daemon，集成工具不能跑 alembic/dump）。
  集成测试用 `TEST_PGVECTOR_DSN` 开关，本地默认 skip。
- 同步驱动阻塞事件循环：后续可把 `VectorStore` 的调用点包进线程池
  （`anyio.to_thread`），或在异步层新增 async 门面。
- 向量表不进 ORM：`knowledge_vectors` 的统计/运维查询用原生 SQL。
- `vector_index.chroma_id` 列名保留（改名会牵动迁移与全部回读逻辑），
  语义已是「向量库 id」。
