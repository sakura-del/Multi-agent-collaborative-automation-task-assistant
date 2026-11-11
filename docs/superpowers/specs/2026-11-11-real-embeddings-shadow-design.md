# ECS 真实嵌入影子表切换设计

> 日期：2026-11-11
> 状态：用户已选择影子表后台重建，本会话串行实施
> 前置设计：`2026-11-11-real-embeddings-design.md`（原地维护方案，因实测耗时不再采用）

## 1. 实测依据与目标

原地方案已完成真实镜像构建、模型预热与 32 条生产样本：

- `BAAI/bge-large-zh` 实际生成 1024 维有效向量；
- 样本 `planned=indexed=32`、`failed_batches=0`、`elapsed_ms=54693`；
- 去掉一次性加载后，按 28,341 条粗略外推约 9 小时，不能让已上线 API 停机数小时；
- 未启动全量 BGE。Mock 已全量恢复（28,341/28,341、0 失败、86.312 秒），
  ID 一致性为真，公网验收 4/4 PASS。

新目标：旧 `knowledge_vectors` 继续承载 Mock 在线检索；真实向量写入独立
`knowledge_vectors_bge`。后台全量完成后做增量对账，短暂重启切到真实表，
普通回滚只切配置，不恢复整库、不重新计算 Mock 向量。

## 2. 不变量

- 任意时刻对外查询和文档向量来自同一表、同一嵌入语义；
- 原 Mock 表在验收后仍保留，真实表失败不影响在线服务；
- 使用原 `chunk_id`，不改 API、前端、BM25、RRF 或重排；
- 模型仍为 `BAAI/bge-large-zh`，1024 维；重排仍为 Mock；
- 所有 DDL 只走 Alembic；revision ID 不超过 32 字符；
- 后台重建限 1 CPU / 2500 MB、`OMP_NUM_THREADS=1`，不与构建、备份、实验矩阵并行；
- 切换前必须覆盖后台期间新增和删除的文档，不能只核对总行数。

## 3. 影子表迁移

新增 Alembic revision `c4f2a8d7e9b1`，down revision 为 `d5a8c1e3f7b2`。
创建 `knowledge_vectors_bge`，列、1024 维 vector 类型、主键和 HNSW 参数与原表一致。

PostgreSQL 索引名在 schema 内共享，不能复用 `ix_kv_*`。影子表索引使用：

- `ix_kvbge_embedding_hnsw`；
- `ix_kvbge_collection_doc`；
- `ix_kvbge_collection_user`。

无 pgvector 的 CI/本地 PostgreSQL 必须与既有迁移一样 no-op；扩展可安装时才建扩展。
downgrade 只删影子表，不删扩展、不碰原表。

### 迁移与旧代码兼容

旧镜像内的 Alembic 脚本不认识新 head，不能在新 head 上直接重启旧镜像。
因此顺序固定为：

1. 构建含新迁移的新镜像；
2. 用新代码、Mock 嵌入、原 `knowledge_vectors` 重启 backend并自动迁移；
3. 验证 Mock 在线服务正常，保留此同版镜像作为普通回滚镜像；
4. 才开始后台写影子表。

普通回滚使用同版新代码 + Mock + 原表。只有明确要回退代码时才先用新镜像
`alembic downgrade d5a8c1e3f7b2` 删除影子表，再恢复旧镜像。

## 4. 增量对账设计

后台期间新文档仍写 Mock 原表，影子表会缺新 chunk；删除文档则会在影子表留下孤儿。
现有 reindex 全量重算无法作为切换前补差手段，因此增加 reconcile 模式。

### 4.1 通用按 ID 删除

- `VectorBackend` 协议新增 `delete_by_ids(ids) -> int`；
- pgvector 用一条参数化 `DELETE ... WHERE collection=%s AND id=ANY(%s)`；
- Chroma 用 `collection.delete(ids=ids)`，以删除前后数量差返回；
- `VectorStore.remove_chunks(chunk_ids)` 暴露同一能力，空列表为 0；
- 上层 reindex 不直接拼 SQL，也不依赖 pgvector 私有属性。

### 4.2 reconcile 语义

`reindex_from_chunks(..., reconcile=True)`：

1. 读取当前 ready 文档的全部 chunk；
2. 读取目标向量表全部 ID；
3. 只对 `DB IDs - store IDs` 生成嵌入并 upsert，已存在 ID 不重算；
4. 缺项全部成功后，删除 `store IDs - DB IDs` 的孤儿；
5. 结果增加 `orphans_present` 与 `deleted_orphans`，`planned/indexed` 只表示本次缺项；
6. dry-run 返回缺项数和孤儿数，但不写、不删；
7. 普通模式保持原有「全量 upsert」语义，不调用删除。

文档 chunk 没有编辑端点，内容更新通过新文档/chunk ID 发生，因此 ID 差集覆盖
线上支持的变更。若未来支持原 ID 内容编辑，需要另加内容哈希，不在本次扩展。

CLI 增加 `--reconcile`。它与 `--limit`、`--user-id` 互斥，防止把部分对账误当全量。
切换前至少执行两次：在线预对账减少差集；停止 backend 后最终对账封闭写入竞态。

## 5. 执行顺序与负载保护

1. 新迁移和 reconcile 均先写失败测试，确认因功能缺失而红，再写最小实现；
2. 本地跑向量、迁移、reindex 聚焦回归与格式/类型门禁；
3. 同步并构建新镜像，旧 backend 继续服务；
4. 用新代码 + Mock + 原表重启，自动创建空影子表，公网 Mock 验收通过；
5. 一次性容器设置 `RAG__PGVECTOR_TABLE=knowledge_vectors_bge`、
   `RAG_EMBEDDING_MOCK=0`，复用已有模型缓存，先 dry-run/32 条样本；
6. 后台全量重建，旧 Mock 服务继续在线；每 2–5 分钟检查进度与资源；
7. 全量成功后在线执行一次 reconcile，再停止 backend 做最终 reconcile；
8. 最终 `consistent=true`、缺失/孤儿 ID 为 0 后，清理 `rag:retrieval:*`，
   将 `.env` 切到影子表和真实嵌入并启动新 backend；
9. 验证真实模型、中文目标文档 top 5、英文查询、公网 health/frontend、
   新容器离线模型缓存和最终负载。

资源中止线沿用已确认设计：构建前磁盘至少 10 GB；后台期 1 分钟 load 连续三次
高于 `2 × vCPU`、`MemAvailable < 300 MB` 或根盘余量 < 3 GB 时停止重建容器。
已写影子批次幂等，资源恢复后可用 reconcile/全量 upsert 继续；不得并行启动第二个进程。

## 6. 回滚

### 6.1 后台阶段

停止影子重建容器即可；在线 Mock 与原表不受影响。影子表可保留供续跑。

### 6.2 切换后普通回滚

1. 停止 backend，封闭切换后新增/删除文档的竞态；
2. 用同版代码、Mock 嵌入和原 `knowledge_vectors` 执行一次 `--reconcile`，
   只补切换后新增的缺项并删除孤儿；若确认切换后零写入，也仍需 dry-run/一致性证明；
3. `.env` 恢复 `RAG__PGVECTOR_TABLE=knowledge_vectors`、
   `RAG_EMBEDDING_MOCK=1`；
4. 用同版新代码镜像启动 backend，清理旧检索缓存；
5. 验证 Mock 一致性与公网检索。

原 Mock 表从未被 BGE 覆盖，因此不需要全量重算或恢复数据库；但切换后的新写入
必须通过 Mock reconcile 回补，不能假设旧表永远无需对账。

## 7. 验收证据

- 新迁移测试证明单 head、正确 down revision、无扩展 no-op、影子索引名独立；
- reconcile 测试证明只写缺失、删除孤儿、dry-run 零副作用、普通模式不回归；
- pgvector 删除 SQL 参数化且空列表不访问数据库；
- 生产后台结果 `indexed == planned`、`failed_batches=0`；
- 最终 reconcile 后数量及 ID 差集均为 0；
- 生产日志和实际 encode 均为真实 BGE；真实 API 返回可回溯 UUID；
- Mock 回滚路径使用同版代码和原表，不需要重算；
- 所有数字取现场输出，实测耗时与估计偏差写入执行记录。
