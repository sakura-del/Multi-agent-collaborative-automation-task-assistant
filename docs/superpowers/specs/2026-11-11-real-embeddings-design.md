# ECS 真实嵌入模型启用设计

> 日期：2026-11-11  
> 状态：样本实测全量约需 9 小时，原地维护方案已停用；改按 `2026-11-11-real-embeddings-shadow-design.md`
> 关联入口：`docs/runbooks/2026-11-11-handoff.md` 待办 #1

## 1. 目标

把 VeriPaper 生产知识库从确定性 Mock 嵌入切换为现有的
`BAAI/bge-large-zh` 真实嵌入，并用原 `doc_chunk.content` 全量重建向量，
使语义检索质量与真实 BGE 模型一致。

切换必须满足：

- 对外服务期间不出现「Mock 查询向量 + BGE 文档向量」或反向混用；
- 重建保持原 `chunk_id`，引用回溯与多用户过滤不回归；
- 模型权重持久化，重建或替换容器后不重复下载；
- 失败时不把半重建索引暴露给用户，并能回到可用的 Mock 检索；
- 验收以真实模型执行、全量一致性及实际检索结果为准。

## 2. 范围与不变项

### 2.1 本次范围

- ECS backend 镜像安装现有 `rag-models` 依赖；
- ECS 容器设置 Hugging Face 缓存目录并使用命名卷持久化；
- 生产嵌入开关从 Mock 切到真实模型；
- 全量重建现有知识库向量并清理旧检索缓存；
- 在 ECS runbook 写入可重复执行的切换、验收和回滚步骤。

### 2.2 明确不做

- 不更换模型：继续使用 `BAAI/bge-large-zh`，维度保持 1024；
- 不启用真实重排模型：`RAG_RERANKER_MOCK=1` 保持不变；
- 不修改混合检索、RRF、API 契约或前端；
- 不新增向量表、数据库字段或 Alembic 迁移；
- 不重跑实验矩阵，不改动用户正在进行的 qrels 标注与论文文件。

## 3. 现状与复用依据

仓库已有以下能力，本设计直接复用：

- `backend/app/knowledge/services/embedding.py`：真实/Mock 双模式，
  两者默认都是 1024 维；
- `backend/Dockerfile`：`INSTALL_RAG_MODELS=true` 时安装 CPU 版 torch
  与 `.[rag-models]`；
- `backend/scripts/reindex_vectors.py`：支持 dry-run、批量、限量、JSON 输出；
- `backend/app/knowledge/services/reindex.py`：从既有分块重建，使用原
  `chunk_id` upsert，并批量同步 `vector_index.chroma_id`；
- `backend/scripts/check_kb_consistency.py`：检查数量与向量 ID 双向差集；
- 根 `docker-compose.yml`：已有 `HF_HOME=/app/.cache` 和 `model_cache`
  的成熟配置，可照搬到 ECS standalone 编排。

2026-11-10 迁移后的基线是 28,341 个 ready 分块及 28,341 条向量；
实施时以现场 dry-run 数量为准，不把历史数量写成跳过校验的条件。

## 4. 方案选择

采用**维护窗口内原地重建**：

1. 构建真实模型镜像期间，旧 backend 继续服务；
2. 进入维护窗口后停止 backend，阻断任务、上传和检索写入；
3. 用新镜像的一次性容器预热模型、执行小样本与全量重建；
4. 验收通过后，以真实嵌入配置启动新 backend。

不采用在线逐批切换：原地 upsert 会产生两种嵌入共存的中间态，虽然维度相同，
但语义空间不同，检索结果不可信。影子表方案能缩短停机，但需要重复 DDL、
索引和额外存储；在用户已允许维护窗口的前提下不属于必要复杂度。

## 5. 部署配置设计

修改 `deploy/ecs/docker-compose.yml`：

- backend 构建参数 `INSTALL_RAG_MODELS` 的 ECS 默认值改为 `true`；
- backend 显式设置 `HF_HOME: /app/.cache`；
- backend 设置 `OMP_NUM_THREADS=1`，正常检索也为其他容器保留一个 CPU；
- backend 挂载 `model_cache:/app/.cache`；
- 顶层声明 `model_cache` 命名卷。

修改 `deploy/ecs/env.example`：

```dotenv
RAG_EMBEDDING_MOCK=0
RAG_RERANKER_MOCK=1
INSTALL_RAG_MODELS=true
```

真实服务器 `.env` 只修改上述两个嵌入/构建开关，不把凭据写入仓库。
`RAG__PGVECTOR_TABLE=knowledge_vectors` 与既有 DSN 保持不变。

## 6. 切换与数据流

### 6.1 预检与构建

1. 核对 backend、PostgreSQL、Redis、gateway 健康，记录当前镜像 ID 并打
   `veripaper-backend:mock-pre-bge` 回滚标签；
2. 记录 `nproc`、1/5/15 分钟 load、`MemAvailable`、Swap、磁盘与容器资源基线；
   根盘可用空间不足 10 GB 或 `MemAvailable` 不足 800 MB 时不开始构建；
3. 使用 `INSTALL_RAG_MODELS=true` 构建新镜像，旧容器继续运行；
4. 触发一次现有每日备份并核实新对象可读，不能把「命令已启动」当备份成功。

**负载硬约束**：镜像构建、模型预热、小样本与全量重建必须串行，禁止同时运行
测试矩阵、批量导入或其他重任务。构建期间每 2–5 分钟检查一次；若 1 分钟 load
连续三次高于 `2 × vCPU`，或 `MemAvailable` 低于 500 MB，则取消构建并保留已有
缓存，等待负载恢复后再续建，不让构建与在线服务争抢到失去响应。

### 6.2 维护窗口

1. 停止 `vp-backend`，gateway 与数据库保持运行；
2. 用新镜像启动一次性容器，设置 `RAG_EMBEDDING_MOCK=0`，复用生产环境、
   数据库网络和 `model_cache`；
3. 直接调用嵌入服务对中文测试句执行 `embed_one`，断言：
   - 实例是 `SentenceTransformerEmbeddingService`，不是 Mock；
   - 向量长度为 1024；
   - 向量含非零值且所有值有限；
4. 执行 `reindex_vectors --dry-run --json`，记录现场 `planned`；
5. 先执行小批量 `--limit 32 --batch-size 32` 验证模型、pgvector 与 ID 同步链路；
6. 通过 `nohup` 执行全量 `reindex_vectors --batch-size 100 --json`，日志写入
   服务器临时目录；使用短 SSH 命令轮询，避免长连接中断影响任务；
7. 只有输出同时满足 `indexed == planned`、`failed_batches == 0`，才进入验收。

批大小 100 是本次运维参数，用于降低 2C/3.6G ECS 上的内存峰值与单批失败
影响；不修改应用默认批大小。一次性重建容器限制为 `--cpus=1`、
`--memory=2500m`，给 PostgreSQL、Redis、网关和系统保留资源；内存限制导致失败时
不得直接放大限制硬闯，应先保留维护状态并重新评估模型与主机容量。

停止 backend 后、开始写向量前，`MemAvailable` 必须至少 2200 MB。重建期间每
2–5 分钟记录 load、内存、Swap、磁盘和进度；若 1 分钟 load 连续三次高于
`2 × vCPU`、`MemAvailable` 低于 300 MB，或根盘可用空间低于 3 GB，立即停止
一次性重建容器并保持维护状态。已提交批次保持幂等，资源恢复后重新执行全量命令，
不得为了赶进度并行启动第二个重建进程。

### 6.3 缓存与恢复服务

- 全量重建成功后，仅扫描并删除 Redis 的 `rag:retrieval:*` 键；
- 禁止 `FLUSHDB`，因为同一 Redis 还承载配额、限流等数据；
- 用 `RAG_EMBEDDING_MOCK=0` 启动新 backend；
- 待 healthcheck 通过后再执行公网与真实检索验收。

## 7. 失败处理与回滚

### 7.1 模型或构建失败

若尚未开始写向量，保持或恢复旧 backend 即可。先解决依赖、模型下载、
磁盘或内存问题，不在真实模型不可用时恢复流量并声称切换完成。

### 7.2 重建中断或部分批次失败

继续保持维护状态。可按失败日志修复后重跑全量命令；upsert 幂等，但必须重新
达到 `indexed == planned` 且 `failed_batches == 0`，不能只检查向量表行数。

### 7.3 回退到 Mock

禁止仅把 `RAG_EMBEDDING_MOCK` 改回 `1` 就启动旧 backend，因为此时文档向量
可能已部分或全部变成 BGE。正确顺序：

1. 保持 backend 停止；
2. 用 `RAG_EMBEDDING_MOCK=1` 执行同一全量 reindex，利用 Mock 哈希向量的
   确定性恢复全部文档向量；
3. 验证一致性与实际 Mock 检索；
4. 启动保留的 `veripaper-backend:mock-pre-bge` 镜像；
5. 公网 health 与检索恢复后才结束维护窗口。

数据库备份用于灾难恢复，不把整库恢复作为普通嵌入回滚的第一步。

## 8. 验收与结果证据

### 8.1 自动检查

- `docker compose -f deploy/ecs/docker-compose.yml config` 能渲染配置，
  且 backend 同时具有 `HF_HOME=/app/.cache` 和 `model_cache` 挂载；
- 后端既有 embedding、reindex、pgvector 与一致性测试保持通过；
- 本任务不新增应用行为，因此不添加只断言 YAML 字符串存在的伪单测；
  配置是否可用由真实镜像、真实模型与真实数据库执行证明。

### 8.2 生产验收

- 实际运行类为 `SentenceTransformerEmbeddingService`，模型名是
  `BAAI/bge-large-zh`，向量 1024 维且数值有效；
- 全量结果 `indexed == planned` 且 `failed_batches == 0`；
- ready 分块数、向量数一致，缺失 ID 与孤儿 ID 都是 0；
- 从实际知识库选择有明确主题的中文文档，使用非逐字复制的自然语言查询，
  目标文档进入 top 5；另做一条英文查询并记录实际命中情况；
- 重新创建 backend 容器后模型缓存仍命中，不需重新下载权重；
- `https://api.veripaper.me/api/v1/health/ready` 返回 ready，
  数据库、Redis、vector 均为 ok；
- `https://veripaper.me` 返回 200，并通过真实 API 完成一次知识库检索。

### 8.3 文档回填

执行完成后在 `docs/runbooks/ecs-deployment.md` 记录实际镜像、模型、
重建数量、耗时、失败批次、检索样例与回滚状态；数字只取现场结果。
更新 `docs/runbooks/2026-11-11-handoff.md` 待办 #1 状态，不预先写成功。

## 9. 文件边界

预计只修改：

- `deploy/ecs/docker-compose.yml`：真实模型构建默认与持久化缓存；
- `deploy/ecs/env.example`：生产真实嵌入模板；
- `docs/runbooks/ecs-deployment.md`：切换、验收、回滚及实测记录；
- `docs/runbooks/2026-11-11-handoff.md`：完成后的状态回填；
- `docs/superpowers/plans/2026-08-09-00-execution-notes.md`：实测证据与偏差。

若实施发现必须修改应用代码，先补能复现具体缺陷的失败测试，再改实现；
不得借本任务顺带重构检索或重排模块。
