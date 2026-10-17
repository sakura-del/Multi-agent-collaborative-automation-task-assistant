# Literature Review Plan Execution Notes

> **Status:** Required preface for the 2026-08-09 literature-review implementation plans. These notes override the conflicting steps named below. They exist separately because the Windows sandbox could create the plan set but could not reopen existing files for a corrective patch.

## 1. Plan 1 Dependency Order

In `2026-08-09-01-foundation-security-plan.md`, Task 3 must create only:

- `backend/app/db/repositories/knowledge.py`
- `backend/app/schemas/knowledge.py`
- `backend/tests/test_knowledge_repository.py`

Task 3 runs only `python -m pytest tests/test_knowledge_repository.py -q` and commits with:

```text
feat(knowledge): add owner-scoped repository
```

Move the knowledge endpoint rewrite and `test_knowledge_api.py` changes to Task 5, after `KnowledgeDocumentService` exists. Task 5 must replace public `text` and server-side `file_path` inputs with authenticated multipart upload; every upload, list, stats, search and delete route passes `current_user.id` to the service. Its API tests must cover unauthenticated access and two-user isolation.

## 2. Plan 2 Module Boundaries

In `2026-08-09-02-task-runtime-plan.md`, Task 1 must define transport-independent `LiteratureReviewRequest`, `TaskControlRequest`, `SubtaskSnapshot`, and `TaskSnapshot` models in `backend/app/orchestration/contracts.py`. HTTP schemas map to these contracts; orchestration code must not import `app.schemas`.

The `TaskExecution` surface is:

```python
async def submit(self, user_id: UUID, request: LiteratureReviewRequest) -> UUID: ...
async def approve_plan(self, user_id: UUID, task_id: UUID, revision: int) -> TaskSnapshot: ...
async def control(self, user_id: UUID, task_id: UUID, request: TaskControlRequest) -> TaskSnapshot: ...
async def get_snapshot(self, user_id: UUID, task_id: UUID) -> TaskSnapshot: ...
async def events_after(self, user_id: UUID, task_id: UUID, event_id: int) -> list[TaskEvent]: ...
async def recover(self) -> None: ...
```

Task 3 must also create `backend/app/orchestration/events.py` with an `EventPublisher` protocol exposing `publish()` and a `NullEventPublisher`. `DatabaseScheduleObserver` depends on that protocol. Task 6 modifies the same file to add Redis publish/subscribe, tickets and the WebSocket hub; it does not create the file for the first time.

## 3. Plan 3 File Map

`backend/app/cli.py` is part of Plan 3 Task 6. It is a thin offline-demo caller of `TaskExecution` and must not duplicate workflow logic.

## 4. Plan 5 Deterministic Smoke Runtime

Before Plan 5 Task 3 creates the smoke client, add:

- `backend/app/llm/adapters/fixed.py`
- `backend/tests/test_fixed_llm_adapter.py`
- the minimal provider registration in `backend/app/llm/factory.py`

`FixedLLMAdapter` returns deterministic planner, executor, validator and reviewer responses. The factory may register provider `fixed` only when `settings.app.env == "test"`; selecting it in development, staging or production must raise a configuration error.

The Compose test profile sets `APP__ENV=test`, `LLM__DEFAULT_PROVIDER=fixed`, and explicit Mock embedding/reranker settings. It receives no real model keys. Run the adapter unit test before the smoke script.

The master smoke command is:

```powershell
docker compose --profile test run --rm smoke
```

## Execution Rule

Apply these corrections while checking off the affected task. Do not create compatibility shims that preserve the superseded interfaces.

## Plan 1 Execution Log (foundation-security)

Branch: `codex/literature-review`. TDD per task, one module per commit.

| Task | Commit | Summary |
|------|--------|---------|
| 1 | `cc91345` | Token expiry read from settings (`_access_token_expires_in`) |
| 2 | `cc2e751` | `DocumentStorage`: basename + UUID filename, 30MB cap, signature check, owner-scoped delete |
| 3 | `1057f7a` | `KnowledgeRepository` owner-scoped queries + HTTP schemas (`test_knowledge_api.py` deferred to Task 5 per notes §1) |
| 4 | `99bfd54` | Retrieval chain `where` filtering (BM25 filter-before-slice, Chroma `where`), cache namespace keys |
| 5 | `5525f72` | `KnowledgeDocumentService` (ingest/delete/list/stats/search/rebuild_bm25), explicit `init_kb()` (no implicit Mock fallback), authenticated multipart endpoints, two-user isolation tests |
| 6 | `4df8189` | Readiness probes (DB `SELECT 1` / Redis ping / Chroma heartbeat), `entrypoint.sh` migrations, `rag-models` extras, CPU-torch Dockerfile, in-image frontend build, nested env names, `JWT_SECRET_KEY` required, WebSocket upgrade under `/api/` |

Notes:

- BM25 `where` filter must filter **before** the top_k slice; a `score <= 0` cutoff is wrong when query terms occur in all documents (negative IDF) — use zero-token-overlap detection instead.
- Cache keys are raw prefixed (`rag:retrieval:{namespace}:{query}:{top_k}`), not hashed, so `clear(namespace)` can scan per-user.
- `get_kb()` raises `KnowledgeBaseNotInitializedError` when `init_kb()` was not called; tests pass `force_mock_*` explicitly.
- `config/` is copied into the backend image at `/config` because `PROJECT_ROOT` in-container resolves to `/` (app lives at `/app`).
- Backend Docker build context is the **repo root**; pyproject `readme` now points at `backend/README.md` (setuptools rejects readme paths outside the package root). torch is installed CPU-only (`--index-url https://download.pytorch.org/whl/cpu`) before `.[rag-models]` to avoid ~1.5GB of CUDA wheels.
- Compose defaults `RAG_EMBEDDING_MOCK=1` / `RAG_RERANKER_MOCK=1` (self-contained, offline, fast); set both to `0` to use real BGE models (~2.5GB first download, cached in the `model_cache` volume). `/app/.cache` (HF_HOME) is pre-created in the image as `appuser` so the named volume inherits writable ownership.

### Plan 1 Exit Gate — PASSED (2026-08-21, commit `4df8189`)

Recorded before starting Plan 2:

- [x] full `pytest tests -q` green — **573 passed**, `black --check` + `ruff check` clean
- [x] `docker compose up -d --build` + readiness `database=ok redis=ok chroma=ok` — all five containers healthy; `GET /api/v1/health/ready` via Nginx returned `{"status":"ready","checks":{"database":"ok","redis":"ok","chroma":"ok"}}`; `GET /` served the in-image Vue build (HTTP 200, `id="app"`)
- [x] two users cannot list/delete/search each other's documents — verified live via Nginx with two fresh accounts (script `scripts/gate-verify.ps1`): B list=0, B delete A doc=404, B search=0, A search=4 (own only), A delete own=200 then list=0

## Plan 2 Execution Log (task-runtime)

Branch: `codex/plan2-task-runtime`（从 `be66476` 拉出，TDD per task）。

| Task | Commit | Summary |
|------|--------|---------|
| 1 | `39cac85` | 运行时 ORM 字段、共享契约 `contracts.py`、可回滚迁移 `8f1c2b3d4e5f`；服务器 PostgreSQL `upgrade → downgrade -1 → upgrade` round-trip 通过 |
| 2 | `cc8ddcb` | owner-scoped `TaskRepository` / `EventRepository`、原子状态流转、递增事件序号、`events_after` 回放；PostgreSQL 并发序号集成测试通过 |
| 3 | `dd15c21` | `SubtaskStatus` 增加 `READY` / `CANCELLED`；`TaskScheduler` 引入 `ScheduleObserver`（默认 `NullScheduleObserver`）与节点上下文（task_id/trace_id/subtask_id/依赖输出）；新增 `DatabaseScheduleObserver`（独立 session 锁行 → 更新子任务/重试/输出 → 追加事件 → commit → publish）；`events.py` 提供 `EventPublisher` 协议 + `NullEventPublisher` |
| 4 | `2c7189f` | 新增 `RuntimeContainer`（LLM/Agent/Planner/事件/后台任务注册）与 `TaskExecution` 深模块（submit / approve_plan / control / get_snapshot / events_after / recover）；`contracts.py` 增加 `LiteratureReviewRequest`、`TaskControlRequest`、`SubtaskSnapshot`、`TaskSnapshot`；Planner 改为输出 `TaskPlan` + `TaskDAG`；`main.py` lifespan 接线并调用 `recover()` |
| 5 | `e972324` | 新增 `app/schemas/task.py`（严格 HTTP 模型 + 契约映射）与 `/api/v1/tasks` 六条路由（创建/列表/详情/确认规划/控制/事件回放），全部鉴权 + owner 404；`TaskExecution.list_tasks` 支撑列表接口；OpenAPI 生成 15 条路径 |
| 6 | `abbed05` | `events.py` 扩展 `subscribe` + `InMemoryEventPublisher` + `RedisEventPublisher` 与票据服务（内存/Redis GETDEL）；新增 `/api/v1/ws-ticket` 与 `WS /api/v1/tasks/{task_id}/ws`（消费票据 → 快照 → 补拉 → 实时转发）；`RuntimeContainer.start()` Redis 可用时自动升级实时后端；OpenAPI 16 条路径 |
| 7 | `81bee64` + `0751819` | 端到端与恢复：`tests/llm/deterministic_llm.py::DeterministicPlanLLM`（BaseLLM 子类）驱动 Planner；`RecoveryManager.load_state()` 从 `TaskDag.graph` + `Subtask` 恢复并重置遗留运行态节点、追加 `task_recovery` 事件；`test_task_recovery_integration.py`（HTTP E2E + 崩溃恢复 + 恢复状态单测）；`scripts/plan2_exit_gate.py` 退出验收脚本 |

Plan 2 notes:

- 计划 2 在共享工作树内用分支 `codex/plan2-task-runtime` 开发，未再建独立 worktree（同一时间只有一个活跃计划，避免 Windows 侧 venv 与路径重复）。
- 迁移 `8f1c2b3d4e5f` 的 `event_seq` 历史回填使用 PostgreSQL `UPDATE ... FROM` + 窗口函数；本地 SQLite 不执行迁移 round-trip，统一在服务器 PostgreSQL 验证（SQLite 本身也不支持首版迁移里的 `now()` 默认值）。
- `EventRepository` 将 `TaskEvent.status` 存于 `ExecutionLog.context` 的保留键 `status`，不新增列；`to_event()` 回放时剥离该键，payload 原样返回。
- Task 3 额外修改了 `retry.py`（原计划文件清单未列）：加入 READY/CANCELLED 后，重试生命周期改为
  `PENDING → READY → RUNNING → FAILED → RETRYING → READY → RUNNING`，并新增可注入的异步转换回调，
  保证 `ScheduleObserver` 收到的转换顺序与真实顺序一致（含重试内部转换）。
- `events.py` 由 Task 3 创建（本文件 §2 的要求），Task 6 在其上扩展 Redis 发布/订阅、ticket 与 WebSocket hub。
- Task 4 按本文件 §2 在 `contracts.py` 定义 transport-independent 契约（`LiteratureReviewRequest` / `TaskControlRequest` / `SubtaskSnapshot` / `TaskSnapshot`），HTTP Schema 留到 Task 5 做映射；编排层不导入 `app.schemas`。
- Task 4 把 `PlannerAgent` 的输出改为 `TaskPlan`（一次 LLM 调用 + schema 校验），DAG 由 `TaskPlan.nodes` 构建并在成环时报 `DAGCycleError`；无 LLM 时降级为确定性三节点计划。`tests/test_planner_agent.py` 已同步重写。
- `TaskRepository.transition()` 改为对 `Task.__table__` 的 Core UPDATE：ORM-enabled UPDATE 会让 `updated_at`（onupdate 列）在会话中过期，随后构建快照时触发 `MissingGreenlet`。
- TaskExecution 测试使用文件型 SQLite（WAL）：内存库 + `StaticPool` 是单连接，后台 session 与轮询 session 会互相回滚事务，不能用来验证真实的多 session 行为。
- `main.py` lifespan 现在创建 `RuntimeContainer` + `TaskExecution`，挂到 `app.state.container` / `app.state.runtime`，启动时调用 `recover()`（表未建时只告警不阻塞），关闭时先取消后台任务再释放 engine/Redis。
- `TaskExecution` 的规划失败路径严格只执行一次 schema 修复调用；`recover()` 只调度 `RUNNING` / `REVIEWING` 状态任务，已完成节点由 `pre_completed` 跳过。
- Task 5 的响应模型直接复用编排层契约（`TaskSnapshot` / `TaskEvent`），只在 `app/schemas/task.py` 定义请求模型与列表/事件页模型；路由函数不含业务分支，逐条调用 `TaskExecution` 的一个方法。
- `TaskControl` 同时接受 `node_id` 与 `subtask_id`（计划原文用 subtask_id，引擎内部用 node_id），二者至少提供一个；`supply_input` 要求非空 input，均由 Pydantic 校验返回 422。
- 为支撑 `GET /tasks` 列表，`TaskExecution` 增加 `list_tasks()` 查询方法（六个核心接口之外的只读扩展）。
- Task 5 的 API 测试用 TestClient 的 portal 事件循环承载后台规划/执行任务，配合文件型 SQLite；`api_env` fixture 在每个测试内替换 `app.state.runtime` 为注入确定性 Planner/Agent 的 `TaskExecution`，并在退出前通过 portal 关闭容器。
- Task 6 的 WebSocket 顺序为：消费票据（未通过不 accept）→ owner 快照 → **先建立订阅** → accept 并发送 snapshot → 补拉 `after_event_id` → 按 event_id 去重转发实时事件 → 断开时 `aclose()` 订阅。
- `RuntimeContainer` 默认使用内存事件/票据实现（测试与无 Redis 环境）；`start()` 用 0.5s 超时探测 Redis，可用时升级为 Redis Pub/Sub 与 `SET NX EX` + `GETDEL` 票据，不可用则保持内存实现，不阻塞启动。
- `tests/test_task_api.py` 的运行时夹具改用 `InMemoryEventPublisher`（原 `_NullPublisher` 无 `subscribe`，无法支撑 WS 测试）。
- Task 7 的端到端测试不再手工构造 DAG：`DeterministicPlanLLM`（`BaseLLM` 子类）返回合法三节点 `TaskPlan`，走 `PlannerAgent` 的真实 LLM 解析/schema 校验路径。
- `RecoveryManager.load_state()` 以 PostgreSQL 为事实来源：读取 `TaskDag.graph` + 已完成子任务，把遗留 `RUNNING`/`VALIDATING`/`RETRYING` 节点重置为 `READY`，并追加 `task_recovery` 事件；`TaskExecution._load_execution_state()` 委托它，提交后再发布恢复事件。
- `RuntimeContainer` 新增公开注入缝 `use_planner()` / `use_agent_factory()`，供退出验收与后续实验注入确定性 Planner/Agent（不再访问私有属性）。
- 退出验收脚本 `backend/scripts/plan2_exit_gate.py` 覆盖两条链路：完整 HTTP 链路（注册/登录 → 建任务 → 确认 → 完成 → 事件回放）与崩溃恢复（n2 运行中取消后台执行 → 重建 runtime → `recover()`）。
- 本地 SQLite 冒烟时脚本会把全局引擎的 `StaticPool` 换成 `NullPool`：`StaticPool` 单连接会让 HTTP 请求与后台任务的事务互相回滚（生产 PostgreSQL 无此问题，服务器验收未启用该补丁）。
- 跨仓开发流：Windows 本地写代码 + 本地 pytest（`--basetemp`），每个任务结束后 bundle 同步到服务器跑完整套件、迁移 round-trip 与集成测试。

### Plan 2 Exit Gate — PASSED (2026-10-17, commit `0751819`)

服务器（PostgreSQL 16 + Redis 7，真实连接池）逐条执行并记录：

- [x] `APP_ENV=test .venv/bin/python -m pytest tests -q --cov-fail-under=70` — **663 passed, 1 skipped**（覆盖率 ≥70）
- [x] `pytest tests/test_task_recovery_integration.py tests/test_scheduler_integration.py tests/test_task_execution.py -q --no-cov` — **15 passed**
- [x] PostgreSQL 集成：`pytest tests/test_task_repository.py -m integration -q --no-cov` — **1 passed**
- [x] 退出验收脚本：`.venv/bin/python scripts/plan2_exit_gate.py`（`DATABASE__URL` 指向 PostgreSQL，`REDIS__URL=redis://localhost:6379/0`）— **exit 0，`ok=true`，`failures=[]`**

记录结果（真实 HTTP + PostgreSQL + Redis）：

| 阶段 | 任务 ID | 结果 |
|------|---------|------|
| 完整链路 | `529b57ec-c7b9-404b-80f3-262b32043ad2` | plan_version=1；事件序列 1→20（`task_status(planning)` → `plan_ready` → `plan_approved` → 3×(ready/running/validating/completed/node_result) → `task_status(reviewing)` → `task_completed`）；节点尝试次数 `{n1:0, n2:0, n3:0}`（均一次成功）；执行器调用 `[n1, n2, n3]` |
| 崩溃恢复 | `ab823084-1a36-48ac-a99d-1d30a5a9c367` | 崩溃前 `{n1: completed, n2: running, n3: pending}`（已持久化，非仅内存）；崩溃后同库重建 runtime → `recover()`：`task_recovery` 事件 `event_id=11`，`reset_nodes=[n2]`、`completed=[n1]`；恢复后执行器只调用 `[n2, n3]`（n1 未重跑）；最终 `completed`，尝试次数 `{n1:0, n2:0, n3:0}`，事件总数 23 |

结论：认证 HTTP、规划确认、持久化 Scheduler 执行、事件回放与重启恢复全部在 PostgreSQL 上闭环；不存在只保存在内存中的执行状态。按路线图 §5，Exit Gate 通过后 `merge --no-ff` 回 `codex/literature-review` 再开 Plan 3。

## Plan 3 Execution Log (literature-workflow)

Branch: `codex/plan3-literature-workflow`（从 `9609130` 拉出，TDD per task）。

| Task | Commit | Summary |
|------|--------|---------|
| 1 | `fbac430` | `app/tools/literature/`（严格模型 + arXiv Atom 适配器 + 受限 PDF 下载 + 离线清单回退）；`core/exceptions.py` 新增 `UpstreamError` / `UpstreamTemporaryError`；16 个 MockTransport 测试 |
| 2 | `3efa83a` | `LiteratureContextBuilder` + `EvidenceContext` 契约；`KnowledgeDocumentService.search_for_context()` owner-scoped 检索入口；8 个测试（标记/截断/隔离/非法标识/owner 过滤） |
| 3 | `484a1cf` | `LiteratureReviewWorkflow` 五个缝（plan/prepare_node/execute_node/validate_node/review）+ 文献契约校验（五类节点/终端 review/write 依赖 extract/3–10 研究问题，一次修复）；`PlannerAgent` 文献契约规划与五节点降级计划；`ExecutorAgent.execute_result()` 类型化 `ExecutionResult` + 引用越界拒绝；Prompt 经 PromptManager 渲染；逻辑测试 15 个 |
| 4 | `6a1bd8b` + `5d9b007` | `CitationValidator` 五项确定性检查（标记可解析/证据被引用/断言带证据/必需章节/重复段落比例）+ 两项不可重试完整性检查；`ValidatorAgent` 确定性优先、语义补充；`TaskScheduler` 定向重生成（同节点同证据 + `validation_feedback`，最多 2 次，耗尽转 WAITING_HUMAN）；重生成复用证据缓存（`prepared_for`）；11 + 3 + 2 个新测试 |
| 5 | `93a0114` + `59346a6` + `0e33760` | `LiteratureExporter`（Markdown/DOCX + 服务端 artifact_id + 受控目录 + HTML 转义/链接校验）；`Artifact` 契约；`ReviewerAgent` 只消费已持久化结果组装 `ReviewReport`；`TaskExecution` 同事务写入 `final_output`/`review_report`/`artifacts` 并置 COMPLETED；`GET /tasks/{id}/artifacts/{artifact_id}` owner-scoped 下载；14 + 2 个新测试 |
| 6 | `0d59d23` + `e78f7bc` + `36bbfc8` + `22c95a9` | 冻结查询 `query.json` + `prepare_demo_corpus.py`（暂存目录 + 原子替换 + sha256 清单）+ `ManifestLiteratureSource`（离线检索源）+ `tests/test_demo_corpus.py`（11 个：schema/重复 ID/哈希/穿越/日期过滤/真实语料）+ `app/cli.py`（`run-literature-demo --offline [--check]` 薄封装 TaskExecution）+ `.gitignore`/`README` |

Plan 3 notes:

- arXiv 下载只允许 HTTPS + host ∈ {`arxiv.org`, `export.arxiv.org`}，手动跟随 ≤3 跳重定向且每跳重新校验 host；大小上限与 `%PDF-` 签名复用 `DocumentStorage`（未改存储层，用 `_StreamingUpload` 适配 httpx 字节流）。
- `UpstreamTemporaryError`（超时/连接失败/5xx）才允许离线清单回退；4xx、非法 XML、schema 错误一律上抛，避免静默降级（Task 3 工作流复用同一语义）。
- `load_offline_manifest()` 只校验 `LiteratureRecord` 字段，清单里的 `local_path` / `sha256` 等附加元数据被忽略，供 Task 6 的冻结语料清单使用。
- Atom 字段统一空白折叠；`published_at` 统一转 UTC（naive 按 UTC 解释）；`source_id` 从 `/abs/<id>` 提取（含版本号如 `2401.12345v1`）。
- Task 2 额外修改了 `document_service.py`（原计划文件清单未列）：新增 `search_for_context(user_id, query, top_k)`，作为证据检索的命名缝，内部复用 `search()` 的 `where={"user_id": ...}` + 独立 namespace 语义，便于 Task 3 工作流注入假实现。
- 证据映射规则：空白片段跳过；doc_id/chunk_id 非 UUID 或 source_url 非法 → `ValidationException`（不静默丢弃）；excerpt 截断 2000 字符（与 `Evidence` 契约一致）；证据少于 2 条 `insufficient=True`。
- `EvidenceContext.text` 使用稳定标记 `[E{n}] doc=... chunk=...`，Task 4 的引用校验按该标记解析。
- Task 3 新增 `HumanActionRequiredError`（409）与 `EvidenceCitationError`（422）；工作流对计划契约/schema 失败只做一次修复请求，二次失败转 WAITING_HUMAN（由 TaskExecution 既有失败路径承接）。
- `PlannerAgent` 保持通用三节点降级计划不变；仅当输入带 `plan_contract="literature_review"` 时走五类节点文献契约（含 `_degraded_literature_plan` 五节点确定性计划），因此 Plan 2 的通用路径与历史退出验收语义不受影响。
- `ExecutorAgent.execute_result()` 面向文献工作流：证据约束 Prompt（检索文本不可信、形式化结论需 `[E{n}]`、无证据标注「模型推断」），解析标记后只回填被引用的证据；出现越界编号（如 `[E9]`）直接抛 `EvidenceCitationError`，不落库。
- TaskExecution 接线：`literature_review` 任务由工作流承担规划；执行时若容器提供 `knowledge_query`（KnowledgeDocumentService）则以 `LiteratureNodeExecutor`/`LiteratureNodeValidator` 替换通用 Agent，搜索节点使用容器新增的 `literature_source`（离线清单源在 Task 6 接入）。
- 由于文献任务改为五节点契约，Plan 2 的确定性计划夹具（`tests/llm/deterministic_llm.py`、`test_task_api.py`、`test_task_execution.py`）与 `scripts/plan2_exit_gate.py` 断言同步升级为 n1..n5；升级后在服务器 PostgreSQL+Redis 重跑验收脚本：`ok=true`、30 个事件、崩溃恢复 `reset_nodes=[n2]`；任务 `368a4734-1022-466e-84a8-492384c7a8d9`（完整链路）与 `80660bb3-e44c-4de2-b2d6-3c59efa5cda8`（恢复）均 completed。
- Task 4 的检查名固定为 `citation_markers_resolve` / `evidence_is_cited` / `formal_claims_have_evidence` / `required_sections_present` / `duplicate_paragraph_ratio`；另有 `evidence_ids_valid` 与 `evidence_ownership` 两项完整性检查，任一失败时整体 `retryable=False`（重生成无法修复标识缺失/归属越权）。
- `ValidationReport.score` 保持 0–100；`ValidationResult.score`（0–1）的归一化留给持久化层（Task 5 写 artifact 时一并落库）。
- 定向重生成复用同一证据集：`LiteratureReviewWorkflow` 缓存节点 `prepare_node` 结果，`LiteratureNodeExecutor` 重生成时传入缓存与 `validation_feedback`；`TaskScheduler` 用 `FAILED → RETRYING → READY → RUNNING` 复用既有状态机，未提高温度（执行路径固定 temperature=0.2）。
- 服务器复核（Task 4 后）：focused `test_citation_validator + test_scheduler + test_validator_agent` 41 passed；全量 718 passed；PG 集成 1 passed；退出验收脚本重跑 `ok=true`（任务 `616c52b6-45f6-487c-857f-fc6c00567ea0` / `f5b3a2e2-1caf-4c5e-8732-38d4022638f8`）。
- `citation_validator` 对 `app.orchestration.contracts` 采用 `TYPE_CHECKING` + 函数内导入，避免在 `app.orchestration` 包初始化前导入它时形成 knowledge → orchestration → agents → knowledge 循环（`5d9b007`）。
- Task 5 关键决定：产物路径固定 `<artifact_root>/<user_id>/<task_id>/<artifact_id><suffix>`，artifact_id 由服务端 `uuid4().hex` 生成；`resolve_for_user()` 只接受 32 位十六进制 ID，跨用户/跨任务/穿越一律 404；Markdown 对原始 HTML 做转义、只拒绝 `javascript:`/`data:`/`file:` 等非 HTTP(S) 链接。
- Task 5 需 `python-docx`（pyproject 已声明）；本地 venv 与服务器 uv venv 均已安装（服务器 uv venv 无 pip，改用 `uv pip install --python .venv/bin/python`）。
- 复盘报告可能带 `summary` 等附加键：导出前只取 `ReviewReport` 字段再校验；`TaskExecution._ordered_execution_results()` 在缺少类型化 `execution_result` 时退化为正文结果，保证 Plan 2 的通用执行路径仍能完成。
- 测试与验收脚本统一覆盖 `app.artifact_root`（conftest autouse 夹具 + 脚本内 temp 目录），避免写入容器默认 `/app/data/artifacts`。
- 两个由服务器验收脚本暴露的真实缺陷（`59346a6` 修复）：`ValidatorAgent._legacy_report()` 的 `checks` 曾放入 `ValidationCheck` 对象导致持久化 JSON 序列化失败；`ReviewerAgent` 曾用 `ValidationReport.model_validate` 直接校验通用校验器输出（含 `rule_check` 等额外键）而失败，现改为按字段白名单规范化并跳过异常条目。
- Task 6 语料准备已在服务器实测（唯一需要网络的步骤）：冻结查询命中 18 篇（需先取 3× 候选窗口再本地日期过滤，`e78f7bc`），下载 10 篇 PDF（36MB）并写入 `manifest.json`（含 sha256）；`files/` 与 staging 目录已 gitignore，只提交 `query.json` + `manifest.json`。服务器语料测试 **11 passed**（本地因 PDF 不随 git 分发而以明确提示 SKIP）。
- 语料下载复用 `DocumentStorage`（伪用户 `UUID(int=0)`），重命名后清理该子目录，保证 `files/` 只含 `<source_id>.pdf`。
- **Plan 3 Exit Gate 阻塞点（环境）**：服务器 `.env` 只有 `LLM__DEFAULT_MODEL/PROVIDER/MAX_RETRIES/REQUEST_TIMEOUT`，缺 `LLM__API_KEY`，`--check` 报 `Missing credentials`，因此 Task 6 Step 6 的真实模型离线验收尚未执行；代码与语料均已就绪，补上密钥后运行 `python -m app.cli run-literature-demo --offline --max-documents 10` 即可。
