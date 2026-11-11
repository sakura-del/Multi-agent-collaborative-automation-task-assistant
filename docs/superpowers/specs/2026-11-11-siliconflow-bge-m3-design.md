# SiliconFlow BGE-M3 嵌入切换设计

> 日期：2026-11-11
> 状态：设计已获用户确认，待书面规格审阅
> 前置状态：本地 BGE-large 全量重建已暂停，生产保持 Mock + 原表

## 1. 目标

使用 SiliconFlow 免费 `BAAI/bge-m3` Embeddings API 替代生产本地模型推理，
在严格限制 RPM/TPM 的前提下重建真实向量，使生产 ECS 只承担 API 调用、
pgvector 检索与业务服务。

实测基线：2026-11-11 从 ECS 调用 `POST https://api.siliconflow.cn/v1/embeddings`，
HTTP 200，模型 `BAAI/bge-m3`，返回 1 条有效 1024 维向量，14 tokens，
耗时约 1196 ms。临时凭据文件已从本机和服务器删除，未进入仓库。

## 2. 不变量与范围

- 查询与文档始终使用同一模型；BGE-large 与 BGE-M3 虽同为 1024 维，
  语义空间不同，禁止混写；
- 原 `knowledge_vectors` Mock 表保持在线，只有完整 M3 影子表验收后才切换；
- 复用 `knowledge_vectors_bge` 与既有 reconcile，不新增 Alembic 迁移；
- 已在影子表的 2700 条 BGE-large 部分数据未被使用，API 冒烟成功后必须清空并回查 0，
  再开始 M3 写入；
- API 鉴权、配置或契约失败不得静默回退 Mock；
- 重排仍保持 Mock，不改检索 API、前端、BM25/RRF 或 chunk 结构；
- API key 只放服务器 600 权限 `.env`，不写日志、代码、文档、提交或终端回显。

## 3. 配置与组件

新增 `SiliconFlowEmbeddingService`，实现现有同步 `BaseEmbeddingService`。
`dimension=1024`，内部使用已有 httpx，不引入模型推理框架。

RAG 配置增加：

```dotenv
RAG__EMBEDDING_PROVIDER=siliconflow
RAG__EMBEDDING_MODEL=BAAI/bge-m3
RAG__SILICONFLOW_API_KEY=
RAG__SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1
RAG__EMBEDDING_RPM=1600
RAG__EMBEDDING_TPM=400000
RAG__EMBEDDING_BATCH_SIZE=64
```

`RAG_EMBEDDING_MOCK=1` 仍优先用于 CI/测试。生产最终为 mock=0、provider=siliconflow、
影子表 `knowledge_vectors_bge`。`INSTALL_RAG_MODELS=false`，不安装 torch；
compose/env 模板同步为 API 模式，保留本地 provider 作为其他环境的显式选择。

## 4. 批量与 RPM/TPM 双限流

### 4.1 批量拆分

- 每个 HTTP 请求最多 64 条输入；
- 输入顺序由响应 `index` 恢复；
- 除条数外，还按请求预估 token 数拆分，单请求不得超过 TPM 上限；
- BGE-M3 单文本超过 8192 token 的上游限制时失败并记录批次，不静默截断正文。

### 4.2 保守预估与实际用量

请求前使用 `sum(len(text.encode("utf-8")) + 4)` 作为保守 token 预估值。
这是限流估值，不声称等于真实 tokenizer 数量。响应若带 `usage.total_tokens`，
则用实际值修正本次滑动窗口记录，让后续调用按真实用量计算；无 usage 时保留预估值。

### 4.3 滑动窗口

服务进程内以锁保护最近 60 秒事件 `(timestamp, tokens)`：

1. 每次 HTTP 尝试（包括 429/失败重试）都必须先 acquire，并计入一次 RPM；
2. 若请求数将达到 1600，或 token 合计将超过 400,000，则睡眠到最早事件过期，
   直到两项都允许；
3. 默认阈值是用户所述 2000 RPM / 500,000 TPM 的 80%，保留在线查询和余量；
4. 当前部署只有一个 backend 进程，重建与最终在线切换不同时调用 M3，因此进程内
   限流足够。未来增加 worker 或多个同时调用方时，必须先改成 Redis 分布式限流，
   不能直接水平扩容。

## 5. 错误与重试

- 400/401/403/404：立即失败，不降级、不重复请求；
- 429、网络错误、超时、5xx：最多 5 次重试；优先遵守合法 `Retry-After`，否则
  指数退避 1/2/4/8/16 秒；等待超过 60 秒则本次调用失败，由运维后续恢复；
- 每次重试仍重新经过 RPM/TPM 窗口；
- 响应必须包含与输入相同数量的 data，按 index 排序后每条都是 1024 维、
  数值有限；违反契约立即失败；
- 网络/429/5xx 耗尽映射为现有 `VectorStoreUnavailableError`（API 503）；
  配置/鉴权/响应契约问题映射为配置错误，不伪装成暂时可用；
- 日志记录状态码、trace ID、重试次数、估算/实际 token，不记录 API key 或正文。

## 6. 清理、重建与切换顺序

1. TDD 完成适配器、限流与错误测试；真实 API 只做小冒烟，不用全量调用代替单测；
2. 同步并构建不含 torch 的生产镜像，先以同版 Mock + 原表验证；
3. 用真实 M3 服务做 2 条短文本调用，验证模型、顺序、1024 维与 usage；
4. 用现有 VectorStore.clear() 只清空 `knowledge_vectors_bge`，回查影子表 0、
   原 Mock 表仍 28,341；
5. 对影子表 dry-run，随后 32 条真实 M3 样本，确认 0 失败；
6. 后台全量重建，记录请求数、实际 token、429、吞吐与失败批次；
   旧 Mock 服务持续在线；
7. 全量零失败后在线预对账，停止 backend 做最终 reconcile 和 ID 一致性检查；
8. 定向清理 `rag:retrieval:*`，将 `.env` 切到 M3 provider + 影子表并启动；
9. 验证真实 API 检索、目标文档 top 5、UUID 回溯、公网 health/frontend、
   最终服务器负载与 API 429 情况。

不再按固定小时数估算；以实际 token 用量、请求耗时和窗口等待时间计算吞吐。
若持续 429 或远端不可用，停止重建并保留 Mock，不靠提高阈值硬闯。

## 7. 回滚

- 后台阶段停止重建容器即可，原 Mock 服务不受影响；
- 切换后停止 backend，用同版代码 + Mock + 原表执行 reconcile，只补切换后的新写入；
- 确认原表 ID 一致后恢复 Mock 配置并启动同版镜像；
- 无需全量重算或整库恢复；只有明确回退代码时才按既有迁移规则处理影子表。

## 8. 测试与验收

自动测试必须覆盖：

- 空输入零请求；64 条与 token 边界拆分；响应按 index 恢复；
- RPM 到达 1600 时等待，TPM 到达 400,000 时等待，两种条件分别独立触发；
- 每次 429 重试都重新计数；Retry-After 与指数退避使用假时钟/假睡眠验证；
- 401/400 不重试，429/5xx 有界重试，失败时不返回 Mock；
- 缺 data、维度错误、非有限数值、缺 key 均明确失败；
- 生产以真实 API 小样本、全量结果、ID 一致性、实际检索和公网健康为结果证据。

官方契约来源：

- https://api-docs.siliconflow.cn/docs/api/embeddings-post
- https://api-docs.siliconflow.cn/docs/userguide/faqs/rate-limit-and-upgradation
