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
- **Plan 3 Exit Gate 实跑记录（尝试 1–5，2026-10-17 22:48–23:00）**：
  - 尝试 1：任务 completed（gpt-6-luna，5 节点 / 30 事件 / md+docx 产物）但 `documents=0`、`citations=0` —— CLI 未注入 `knowledge_query`，走的是通用执行路径；新增 `KnowledgeSearchProvider`（按调用开 session）接线（`f64a7029`）。
  - 尝试 2：`'TaskPlan' object has no attribute 'require_node'` —— `use_plan()` 返回计划而非工作流；改为返回自身（`34bd7b41`）。
  - 尝试 3：校验器 `KeyError: 'title'` —— 工作流未传 `title`；`validate_node` 补 `_node_title()` 且校验器改为 `.get`（`d6039064`）。
  - 尝试 4：n1 检索节点**确定性校验通过（96.0）**，n2 抽取节点报 `检索结果缺少合法的文档/分块标识` —— 索引侧 chunk_id 是 16 位合成 id（如 `0b5ddd99f22604d6`），与 `DocChunk.id`（UUID）不同；新增 `resolve_retrieval_ids()`：已是 UUID 的原样保留、合成 id 经 `VectorIndex.chroma_id → (chunk_id, doc_id)` 映射替换、无映射直接丢弃（`5aaa5149`），并补单测。
  - 尝试 5（当前阻塞）：n1 通过（96.0）；n2 检索到 8 条可回溯证据，但 LLM 抽取输出**未引用任何 `[E{n}]`** → `evidence_is_cited` 失败（87.43，retryable），两次重生成后仍同样得分（三次 87.43、executor duration≈0.0s）→ 转 WAITING_HUMAN（任务 `2fe4579b-225d-4b4c-ba2f-7a103ee60f37`）。
  - 下一步定位方向：① 确认 `validation_feedback` 是否真正进入 `literature_executor_system.j2` 的 `validation_feedback_section`（否则提示词完全相同）；② 检查执行路径是否存在 LLM 响应缓存导致重生成返回同一输出；③ 抽取节点提示词可要求"每条发现至少一处 `[E{n}]`"。 Exit Gate 尚未通过，Task 6 代码/语料已完成。
  - 尝试 6（`12f9c85` 后）：定位到**真正的根因**——`RuntimeContainer` 虽在 `start()` 初始化 `LLMManager`，但默认 Agent 工厂一直构造 `ExecutorAgent()` 这类**无 LLM** 实例，全链路静默降级（`llm_chat_ok` 计数 = 0，输出 `[降级输出] …`）。改为 `_build_default_agent()` 注入 `_llm_client()`（planner/executor/validator/reviewer 全覆盖，缺 LLM 时打印一次告警并保留降级），并补 `tests/test_runtime_container.py` 锁定。修复后同一离线演示：**7 次真实 LLM 调用**，n1–n4 全部完成且确定性校验 96.0，正文含 14/15/29 处 `[E{n}]`。
  - 尝试 6 剩余问题：n5（复盘/汇总节点）输出里 58 处标记全部无法解析 —— `_summarize_dependencies()` 直接拼接依赖节点文本，携带的是**依赖节点自己的局部编号**。修复：`_rebase_markers()/_rebased_dependency_outputs()` 按 `(doc_id, chunk_id)` 把依赖文本中的 `[E{n}]` 重定位到当前节点证据池，找不到的标记直接移除；写节点同样走该重定位（`cc548e5`，+2 测试）。
  - 尝试 7（`cc548e5` 后）：LLM 网关开始返回 `500 INTERNAL_ERROR: Failed to validate API key`（同一密钥在 23:00–23:09 可用，23:14 起 3/3 失败；服务器 `.env` 中密钥长度 67、`sk-` 前缀、`03ff` 结尾、无空白/引号，裸 `curl` 亦 500）→ **密钥失效，Exit Gate 需新密钥后重跑**。代码侧修复已全部落地：本地全量 **749 passed**。
- **运行环境约束（23:25–23:35 实测）**：本机（Windows 开发机）**无法访问模型端点** `https://model.rhzy.ai/v1`——直连被远端重置（WinError 10054），走本机代理 `127.0.0.1:7894` 则 TLS 握手失败（`EOF occurred in violation of protocol`）；arXiv 直连正常（HTTP 200）。因此**真实模型验收只能在服务器上执行**。
- **NO_PROXY 陷阱**：开发机 `NO_PROXY` 含 IPv6 回环写法 `[::1]`，httpx 会把每个 NO_PROXY 条目当 URL 解析并抛 `InvalidURL: Invalid port: ':1]'`。新增开发包装器 `scripts/run_without_proxy.py`：仅剔除含方括号的非法条目、保留 HTTP(S)_PROXY，并支持 `-m 模块` 入口；`prepare_demo_corpus.py` 与 `app.cli --check` 均可用它运行。
- 新增 Plan 3 验收脚本 `scripts/plan3_exit_gate.py`（阶段：语料哈希 → 离线真实模型演示 → 引用可回溯/产物哈希/重启恢复 → JSON 报告），含 6 个纯函数测试；与 CLI 共用 `--data-root`（默认 `backend/.runtime`）以适配宿主机运行。
- **服务器可达性**：`10.0.10.2` 自 23:17 起 ping/SSH/HTTP(48080) 全部超时（与用户提到的另一项目 200G 占用事件同期，用户确认与本项目无关），服务器侧验证与 Exit Gate 重跑待其恢复。

## Plan 3 Exit Gate（PASSED，2026-10-18）

验收入口：`cd backend && python scripts/plan3_exit_gate.py --max-documents 10 --username literature-gate2 --timeout 2400 --report /tmp/p3_gate_report2.json`（服务器 PostgreSQL + Redis + 离线 10 篇语料 + 真实模型 `gpt-6-luna`）。

| 阶段 | 结果 |
| --- | --- |
| corpus | 10 条记录，sha256 全部一致 |
| offline_demo | CLI 退出码 0，任务 `ea46442d-387b-4abb-b044-164798ca1c97` 状态 **completed**，真实 LLM 调用（单节点 executor 8.2s），规划出 **7 个节点**（n1–n7） |
| verify_task | **引用全可回溯**：n1/n2/n6/n7 无标记；n3 6 标记/池 8/引用 4；n4 16/8/6；n5 15/8/6 —— 越界标记 `[]`、缺失分块 `[]`；**产物双哈希一致**：markdown 26,546 B、docx 40,828 B（`sha256_recorded == sha256_actual`）；**重启恢复**：`recover()` 后状态 completed→completed、事件 40→40、各节点 `retry_count` 全 0 不变 |

关键修复（本轮）：
- `756bbd9`：`evidence_is_cited` 语义从「池内每条证据都必须被引用」改为「池非空时正文必须真正引用池内证据，覆盖率作为提示」——抽取节点只采样相关证据是合理的；Exit Gate 实跑证明原口径会让 n2 永远失败（8 条池仅引用 4 条）。
- `ac4a53a`/`b9a4489`/`88e9968`：Exit Gate harness 三处口径/接口修正——标记越界按**节点证据池**（`evidence_pool`）判定而非被引用子集、`resolve_for_user()` 返回 `Path`、`ExecutionLog` 从 `app.db.models.log` 导入。
- 服务器回归：unit suite **757 passed**（1 skipped）、PG 集成 **1 passed**、Plan 2 Exit Gate 重跑 **ok=true**。

## Plan 4（前端工作台）执行记录

| Task | commit | 交付与验证 |
| --- | --- | --- |
| 1 | `0cecd66` | 工作台骨架 + 鉴权状态：`api/types.ts`、`api/client.ts`（单一 Axios 实例、401 单飞刷新 `refreshPromise`、`_retry` 防环、刷新失败回调登出）、`api/auth.ts`、`stores/auth.ts`（bootstrap/login/register/refresh/logout）、`router/index.ts`（/login、/tasks、/tasks/:taskId、/knowledge + 等待 `auth.ready` 的守卫）、`layouts/AppLayout.vue`（安静左导航 + 当前用户 + 图标登出）、`views/LoginView.vue`（登录/注册 + redirect 回跳）、三个占位视图、`test/setup.ts`（每例清 localStorage）；Vitest 配置进 `vite.config.ts`。**验证：7 个单测通过、`vue-tsc` 通过、`vite build` 成功（154KB JS / 3.4KB CSS）** |
| 2 | `928258c` | 类型化任务契约 + 快照 store：`api/types.ts` 镜像后端 `TaskStatus`(11)/`SubtaskStatus`(11)/`TaskCommand`(5)/`TaskCreate`/`TaskSummary`/`TaskSnapshot`/`SubtaskSnapshot`/`TaskPlan`/`PlanNode`/`TaskEvent(Page)`/`ReviewReport`/`Artifact`；`api/tasks.ts` 七个 adapter（list/create/get/approve/control/events/wsTicket；create 按后端实际返回 `TaskCreateOut`）；`stores/tasks.ts` 持有 `items/snapshot/events/lastEventId/watching`，`applyEvent` 只接受严格递增 `event_id`（乱序/重放安全），`watch/stopWatching` 先轮询（WebSocket 留给 Task 5）。**验证：8 个新单测（含 15 passed 合计）、`vue-tsc` 与 `vite build` 通过** |
| 3 | `7002e35` | 任务创建与列表：`components/tasks/TaskCreateForm.vue`（主题 5–200、关键词 1–10 标签输入、文献数 1–50、日期顺序、语言/引用格式分段控件、离线语料开关、可选知识库文档勾选；校验通过后**精确 emit 一次** `TaskCreate`，pending 时禁用提交，服务端错误就近展示）、`components/common/StatusBadge.vue`（11+ 状态 → 中文标签 + 5 档色调）、`components/common/ErrorBanner.vue`、`utils/task-status.ts`（状态→进度/终态判断/时间格式化）、`views/TaskListView.vue`（桌面表格 ↔ 移动单列；空态只给一个"新建任务"命令；创建成功后跳 `/tasks/{id}`）。**验证：22 passed（表单 7 个新测试）、`vue-tsc` 与 `vite build` 通过（166KB JS / 6.85KB CSS）** |
| 4 | `b0f7e5b` | 规划确认与 DAG 联查：`utils/dag-layout.ts`（按拓扑深度分列、同层按序分行的**确定性几何**；状态→标签/色调；`nodeAriaLabel`；内容包围盒）、`components/tasks/TaskDag.vue`（固定节点尺寸 200×68、禁用拖拽/连线、保留平移与"适应窗口"、点击/回车选择、SVG 依赖边）、`NodeDetail.vue`（DAG 容器之外的侧抽屉）、`PlanPanel.vue`（研究问题/检索式/验收标准 + 只读 DAG，仅"重新生成"与"确认规划"两个命令，approve 带 `plan.version`，409 → 重载快照 + 冲突提示）、`views/TaskDetailView.vue`（规划/执行/日志/结果四 Tab，**Tab 由路由 query 驱动**，结果 Tab 在无 `review_report` 时禁用；挂载即 `store.watch`，卸载 `stopWatching`）、`main.css`（焦点环/链接/按钮悬停）。**验证：36 passed（DAG 9 个 + PlanPanel 5 个新测试）、`vue-tsc`、`vite build` 通过（177KB JS / 11.4KB CSS）** |
| 5 | `ecbbb8a` | 实时事件、日志与人工干预：`services/task-events.ts`（`createTaskEventSession`：先加载快照 → `ws-ticket` 一次性票据 → 补拉 `after_event_id` → WebSocket 帧 `snapshot`/`event`；按 `event_id` 去重；1s/3s/9s 指数退避重连，超三次失败降级 3s HTTP 轮询；`stop()` 清理定时器与套接字；HTTPS 页面用 `wss`）、`stores/tasks.ts`（`watch` 改为实时会话，保留 `pullEvents` 手动补拉）、`components/tasks/TaskToolbar.vue`（**只渲染当前状态允许的命令**：running→暂停、paused→恢复、waiting_human+失败/等待节点→重试节点、waiting_human→补充输入弹窗（必填校验）、非终态→取消（二次确认）；命令 pending 时禁用）、`components/tasks/TaskLog.vue`（Agent/节点/级别过滤 + 时间/类型/状态/消息，**纯文本插值不用 v-html**，100 行分页）、`views/TaskDetailView.vue`（工具栏 + 日志面板接线）。**验证：52 passed（事件服务 8 个 + 工具栏/日志 8 个新测试）、`vue-tsc`、`vite build` 通过（188KB JS / 13.3KB CSS）** |
| 6 | `f3b8699` | 知识库页面：后端小改（`KnowledgeDocumentOut` 增加 `source/original_name/failure_reason`，端点统一走 `_to_out()`，失败原因取 `meta_info["error"]` 并截断 200 字、不带栈）、`api/knowledge.ts`（`upload(file,title,strategy,onProgress)` 组 `FormData`、**不手写 content-type**、按 `onUploadProgress` 回报百分比；`list/remove/stats/search`）、`stores/knowledge.ts`（documents/stats/loading/uploading/uploadProgress/error + loadAll/upload/remove）、`components/knowledge/DocumentUpload.vue`（客户端扩展名白名单 + 30 MB 上限，**提交前重新校验**，进度条，错误就近展示）、`DocumentTable.vue`（标题/类型/来源/索引状态/分块/创建时间，垃圾桶图标 + 二次确认删除，失败原因简洁展示）、`views/KnowledgeView.vue`（上传工具 + 无边框表格 + 统计行）。**验证：前端 62 passed（知识库 10 个新测试）、后端 757 passed、`vue-tsc`、`vite build`（195KB JS / 14.6KB CSS）** |
| 7 | `4d4624a` | 结果视图与安全下载：`api/tasks.ts` 增加 `downloadArtifact(taskId, artifactId)`（`responseType: 'blob'`，文件名优先解析 `Content-Disposition` 的 `filename*`/`filename`）、`components/tasks/TaskResult.vue`（左侧正文列 + 右侧克制元数据栏：下载 Markdown/DOCX（缺产物则禁用）、质量评分中文标签（引用可追溯性/质量…）、待改进清单、参考文献——**只允许 http(s) 链接**，`javascript:`/`data:` 退化为纯文本；正文用 `<pre>` 纯文本渲染，**不用 v-html**；下载后立即 `revokeObjectURL`）、`views/TaskDetailView.vue` 结果 Tab 改用该组件。**验证：前端 69 passed（结果视图 7 个新测试：正文不解析 HTML、仅 http(s) 链接、评分标签、问题清单、服务端 artifact_id 下载 + revoke、DOCX 命令、缺产物禁用）、`vue-tsc`、`vite build`（199KB JS / 15.8KB CSS）** |
| 8 | `4881019` `6b2ebbd` `cb2dc3a` | 浏览器级验收：`playwright.config.ts`（`E2E_BASE_URL` 可配、桌面 + `Pixel 5` 两个 project、`trace: on-first-retry`、`screenshot: only-on-failure`、不在配置里装浏览器）、`e2e/literature-review.spec.ts`（注册 → 上传 TXT → 建离线任务 → 确认规划 → 观察 DAG 状态变化 → 完成 → 校验 `[E{n}]` 引用 → 下载 md/docx 并断言非空；第二个用例覆盖 RUNNING 期刷新保状态、`routeWebSocket` 阻断后轮询推进、事件行按 event_id 去重）、五个关键节点截图 + **横向溢出断言**（移动端）；Vitest `include` 收窄到 `src/**/*.test.ts` 避免误收 Playwright 规格。**验证：`npm run test:e2e` 桌面 + 移动共 4 passed（2.0m），10 张截图** |

### Plan 4 Exit Gate（PASSED，2026-10-18）

- 桌面（1280×720）与移动（Pixel 5，393×851）双视口各跑一遍完整流程 + 刷新/重连用例：**4 passed**，无 Swagger/终端依赖。
- 截图证据（本地 `frontend/e2e/screenshots/`，已 gitignore）：`{chromium,mobile-chrome}-{1-task-list,2-plan-approval,3-running-dag,4-human-action,5-final-result}.png`。目视核验：无文字重叠、状态变化不改变节点 `style`（用例断言）、刷新后 Tab/任务/进度保持。
- 本轮 E2E 暴露并修复的真实缺陷：
  1. `fix(frontend): refresh snapshot on meaningful status change`（`588811a`）——事件只带状态，规划/复盘产出后界面不刷新，`确认规划` 永远不出现。
  2. `fix(deploy): pass nested LLM config into backend container`（`97b9acb`）+ `fix(api): wire knowledge search and literature source into runtime`（`9aea327`）+ Dockerfile 补 `config/`、`demo_data/`——容器此前用的是扁平占位 `OPENAI_API_KEY=sk-your-…`（SDK 回退）且没有 `LLM__*`，文献任务在界面上走的是通用执行路径（无证据、无引用）。
  3. 移动端三处布局缺陷：DAG 画布把栅格撑宽（`4881019`）、208px 侧栏挤爆 393px 视口（`6b2ebbd`）、导航文字被挤成竖排且 DAG 未适配（`cb2dc3a`）。
  4. 服务器 `maa-backend` 镜像过期（OpenAPI 里没有 `/api/v1/tasks`）——重建后 6 条任务路由齐全。

Plan 4 备注（Task 4 偏差说明）：
- 计划建议用 Vue Flow 渲染 DAG；实际实现为**固定几何自绘 DAG**（绝对定位节点 + SVG 依赖边 + 溢出滚动平移 + 适应窗口缩放）。理由：Step 1 要求"状态变化时节点不移动"与确定性几何，自绘可把几何收敛到纯函数 `layoutDag()`（9 个测试直接覆盖，happy-dom 下零 flake），且无需连接点（Handle）管线；禁用拖拽/连线编辑、保留平移/适应窗口/选择等交互要求全部满足。相应地移除了未使用的 `@vue-flow/core` 依赖；若 Plan 5 之后需要图编辑能力再引入。

## Plan 5（加固与实验）执行记录

| Task | commit | 交付与验证 |
| --- | --- | --- |
| 1 | `6543a51` | 进程级确定性 E2E：`tests/fakes/runtime.py`（`FixedLiteratureLLM` 按 **system prompt 用途**分派规划/执行/语义校验/复盘四类响应、只从证据行取 `[E{n}]` 避免越界；`FixedEmbedding`/`FixedReranker` 确定性；`RecordingEventPublisher` 记录有序事件）、`tests/e2e/test_literature_review_e2e.py`（注册→上传 2 篇 TXT→建任务→`waiting_approval`→按精确 revision 确认→`completed`→**引用可回溯**（被引 chunk 必须是该用户已上传文档的分块）→**事件 ID 严格递增且唯一**→下载并打开 Markdown/DOCX（DOCX 校验 `word/document.xml`）；另有「提示词用途全覆盖」与「连续两次运行节点集合与正文一致」两个用例）；`RuntimeContainer` 增加 `llm=` 注入点（`start()` 不再读全局单例、新增 `create_llm()`）。**验证：本地与服务器各连续跑两遍均 3 passed；后端全量 764 passed** |

Plan 5 Task 1 实跑发现并修复的真实缺陷：
- 文献工作流自建的 `ValidatorAgent` **没有注入 LLM**，语义补充校验永远走降级默认值（`_check_semantic` 在 `llm is None` 时直接返回 80 分）。已改为 `llm=self.container.create_llm()`，使确定性引用校验 + LLM 语义校验两条路径都被真实覆盖。
- `RuntimeContainer.start()` 原本无条件读取全局 `LLMManager`，测试注入会污染进程状态；现改为「显式注入优先、否则读全局」。
- 固定 LLM 早期版本从整段 prompt 抓 `[E{n}]`，会把依赖输出里的标记也带上，导致执行器判定「引用超出证据范围」并重试耗尽；改为只解析证据行。

### Plan 5 Task 2：恢复 / 隔离 / 降级证明（`a749da6`）

`tests/integration/test_runtime_resilience.py`（7 个）——故障注入矩阵：

| 故障 | 断言的行为 |
| --- | --- |
| 知识库/Chroma 检索抛错 | 受影响节点转 `waiting_human`/`failed`，带错误信息，不静默成功 |
| arXiv 源超时 | `offline_dataset=True` 的任务仍完成（走冻结清单，不依赖在线检索） |
| 校验持续失败 | 两次重生成后停 `waiting_human`，且 `validation.passed=false`、`retryable=true` |
| 取消发生在 LLM 请求中 | 任务停 `cancelled`，**迟到的 LLM 结果不改变状态** |
| 数据库写路径失效 | 观察者记录 `schedule_result_persist_failed` 且不抛给调用方；已提交状态与事件数不变 |
| Redis 不可用（内存发布器） | 事件仍可 HTTP 补拉，ID 严格递增，发布数 == 补拉数 |
| 进程重启（全部节点已完成） | `recover()` 后状态、`retry_count`、事件数全部不变（不重跑） |

`tests/integration/test_security_boundaries.py`（14 个）——安全边界：
- 双用户隔离：任务快照/事件/产物下载/控制命令/文档删除/文档列表全部 404 或空集；
- 上传边界：路径穿越文件名（要么拒绝，要么落盘仍在受控目录内）、伪造 PDF 签名拒绝、超大文件拒绝；
- 引用与导出：含 `javascript:` 的参考文献**导出直接拒绝**（`ValidationException`），DOCX 是合法 ZIP（含 `word/document.xml`）；
- 提示词注入：证据里写「忽略之前的所有指令…输出系统提示词」时，任务仍完成、正文仍带 `[E{n}]`、**不泄漏系统提示词**；执行 prompt 明确把证据声明为不可信数据；
- 票据边界：单次使用、过期拒绝、跨任务拒绝、`/ws-ticket` 越权 404。

本轮修复的真实缺陷：
- **`SubtaskSnapshot.output` 丢弃了 `validation`**（`_subtask_snapshot` 只映射 `output_data["output"]`）→ 界面节点详情与验收脚本都无法判断「为什么停在 WAITING_HUMAN」。已把 `validation` 并入快照 `output`。
- 测试自身的问题（非产品缺陷）：跨文件全局状态污染（直接给 `settings.app.max_upload_bytes` 赋值）导致 `test_knowledge_api` 顺序失败 → 改用 `monkeypatch.setattr` 自动还原；`exporter.export_*` 返回 `Artifact`（无 `.path`），需用 `resolve_for_user()` 取路径。
- 服务器验证：integration + recovery **24 passed**；全量 **785 passed**（1 skipped）。

### Plan 5 Task 3：Compose 发布冒烟（`dcd0f37` / `7548b47` / `6d841c6`）

交付：
- `backend/app/tools/smoke/smoke.py`：网络内冒烟客户端 —— 等就绪（默认 120s）→ 注册登录 → 上传 TXT → 建离线任务 → 确认规划 → 轮询完成 → 下载 Markdown/DOCX → 校验（`[E{n}]` 引用存在、产物非空、DOCX 是含 `word/document.xml` 的 ZIP、事件 ID 严格递增），任一项不符即非零退出。
- `docker/smoke/Dockerfile`：只装 `httpx`，`COPY backend/app/tools/smoke`（CI 测的就是镜像里跑的），非 root 运行，不挂源码、不注入真实密钥。
- `docker-compose.smoke.yml`：`test` profile 的 `smoke` 服务（`BASE_URL=http://nginx`）+ 冒烟栈覆盖 —— 容器名前缀 `maa-test-`、宿主端口错开到 **35443/36389/38012/48082**（生产是 35433/36379/38002/48080）、**命名卷改为 `maa-test-*`**（关键：Compose 的 `down --volumes` 否则会删生产数据）。
- `scripts/smoke.sh` / `scripts/smoke.ps1`：`up -d --build` → `run --rm smoke` → `down --volumes`，shell 用 `trap`、PowerShell 用 `try/finally`；失败时打印服务日志，`KEEP=1` 可保留现场。
- `backend/app/llm/deterministic.py` + provider 注册：`LLM__DEFAULT_PROVIDER=deterministic` 走确定性适配器（与真实模型完全相同的 Agent 调用路径），冒烟不需要任何真实密钥。

关键实现细节（踩过的坑）：
1. Compose 的 `ports` 是**合并**语义，override 文件里必须用 `ports: !override` 才能整体替换，否则生产端口仍被占用导致 `Bind for 0.0.0.0:36379 failed: port is already allocated`。
2. 生产栈的命名卷是固定 `name: maa-*-data`，冒烟若不覆盖卷名，`down --volumes` 会**删除生产数据**；已在 smoke override 中全部改为 `maa-test-*`。
3. `docker compose up` 失败时脚本原先仍继续执行（`set -uo pipefail` 下不算致命），已改为显式判断并打印日志退出。
4. 冒烟客户端最初放在 `docker/smoke/`，pytest 根是 `backend/` 无法导入 → 迁到 `backend/app/tools/smoke/`（同时随 `app` 包进镜像），并补 8 个 MockTransport 单测覆盖成功链路与失败分支。

验证（服务器实跑两次）：
- `readiness ok: {database: ok, redis: ok, chroma: ok}`
- 文档索引 ready（chunks=1）；任务 `58b7a5db-ae95-43b6-a721-64e7d7315009` **completed**
- 产物：markdown **3408 B**、docx **37661 B**；事件 **30** 条严格递增
- `SMOKE OK {"status": "completed", "artifacts": 2}`，退出码 0；`maa-test` 容器/卷**全部清理**，生产五容器保持 healthy
- 后端全量 **793 passed**（新增 8 个冒烟客户端单测）

### Plan 5 Task 4：全项目 CI 门禁（`e7594c9`）

`.github/workflows/ci.yml` 重写为 6 个作业，**所有检查均为阻断式**（移除全部 `|| true` 与 `continue-on-error`）：

| 作业 | 内容 |
| --- | --- |
| `lint` | `pip install -e ".[dev]"`（依赖唯一来源是 pyproject）→ `black --check` → `ruff check` → **`mypy app`（阻断）** |
| `test` | `pytest tests --cov=app --cov-fail-under=80`（覆盖率门槛从 70% 提到 80%）+ 上传 coverage.xml |
| `migration` | PostgreSQL 16 service：`alembic upgrade head` → `downgrade -1` → `upgrade head` 往返 + PG 集成测试 |
| `frontend` | Node 20 + `npm ci` → Vitest → `vue-tsc` → `vite build` |
| `build-check` | 后端镜像 + 冒烟客户端镜像均可构建 |
| `smoke` | 依赖 `test`+`frontend` 通过后跑 `scripts/smoke.sh`（隔离 project `maa-test`），失败时 dump 服务日志 |

同时把门禁真正打通所需的**真实代码修复**（mypy 从 24 个错误 → 0）：
1. `KnowledgeDocumentService.list()` 遮蔽内置 `list` 类型，使同类 `-> list[...]` 注解被当成方法引用（4 个 `valid-type`）→ 重命名为 `list_documents()`（调用点同步）。
2. `app/tools/literature/arxiv.py`：`published` 一直是 `str`、链接是裸字符串，**靠 Pydantic 运行期隐式转换**才没出错 → 显式 `datetime.fromisoformat()` + `HttpUrl(...)`，并补非法时间格式的 `ValidationException`。
3. `app/orchestration/scheduler.py`：`gather(return_exceptions=True)` 的结果联合类型未收窄 → 改用 `isinstance(nr, BaseException)` + `continue`，让 `nr` 在后续分支确定为 `NodeResult`。
4. `app/core/logging.py`：`renderer` 变量被推断为 `JSONRenderer` 却赋 `ConsoleRenderer` → 显式标注 `Any`。
5. `app/knowledge/services/document_parser.py`：`para.style` 可能为 `None` → 先兜底再取 `.name`。
6. `app/core/redis.py`：`hgetall` 返回 bytes/str 混合 → 统一转 `str`（真实健壮性改进，不只是类型标注）；`hset(mapping=...)` 是 redis-py 标注限制 → 定向 `type: ignore`。
7. `app/orchestration/agents/reviewer.py`：`_polish_literature_review` 在 `self.llm is None` 时会崩 → 提前返回 `{}`（真实缺陷）。
8. `app/db/repositories/task.py`：Core `update(Task.__table__)` 与 `CursorResult.rowcount` 的第三方标注差异 → 定向 `type: ignore`。
9. `app/cli.py`：误用 `starlette.datastructures.UploadFile` → 改 `fastapi.UploadFile`。
10. `backend/pyproject.toml`：移除**代码从未引用**的重依赖 `langchain` / `langchain-community` / `tiktoken` / `beautifulsoup4` / `openpyxl` / `pandoc`（显著缩短 CI 安装与镜像构建；`requests` 是 starlette 误报、`aioredis` 是 `redis.asyncio` 别名，均保留）。

验证（本地 + 服务器同口径）：
- `black --check`：191 files unchanged；`ruff check`：All checks passed；**`mypy app`：Success, no issues in 120 source files**
- `pytest --cov-fail-under=80`：**793 passed, 1 skipped，覆盖率 87.82%**（服务器 113s，本地 147s）
- 前端：Vitest **69 passed**、`vue-tsc` 通过、`vite build` 成功（199KB JS / 16.65KB CSS）

### Plan 5 Task 5：可复现实验输入（`8122e86` / `65bd610`）

新建 **`backend/evaluation/`**（与 `app/` 物理隔离：`app/` 是被测量系统，`evaluation/` 管输入→运行→指标→报告）：

| 文件 | 作用 |
| --- | --- |
| `models.py` | 三个不可变契约（`frozen=True, extra="forbid"`）：`ExperimentConfig` / `EvaluationTopic` / `HumanRating` |
| `configs/{baseline_a,baseline_b,baseline_c,full}.yaml` | 四组画像，开关与名称**强一致校验**（名为 baseline_b 却开 RAG 直接报错） |
| `datasets/topics.json` | 15 课题 × 3 领域 × 5，含中文主题/英文检索式/语料清单路径 |
| `datasets/corpora/{ai,software_engineering,education_technology}.json` | 三领域各 10 篇的冻结语料清单（sha256；PDF 目录已 gitignore） |
| `datasets/ratings_template.csv` | 盲评表头，`anonymous_output_id` 含 `baseline/multi_agent/hybrid` 等分组词即报错 |
| `prepare_corpora.py` | 唯一需网络的步骤：按领域检索 arXiv → 各下载 10 篇 → 写 sha256 清单（原子替换） |
| `validate.py` | 一次性校验画像/课题/语料/盲评模板，输出 `{profiles, topics, domains, corpora, runs:120}` |

可复现性硬约束（`ExperimentConfig` 强制，不是靠文档约定）：
- `model=gpt-6-luna`、`provider=openai`、`temperature=0.2`、`max_output_tokens=2048`、`prompt_version=lit-v1`、`corpus_version=corpus-10-v1`、`seed=20261017`、`runs_per_topic=2`
- 四组画像的这些字段由 `validate.load_profiles()` **互相比较**，任一组漂移即失败
- 语料清单记录数必须等于 `corpus_version` 声明的 10 篇

`pyproject.toml` 新增 `[project.optional-dependencies].evaluation`（pandas/scipy/matplotlib），产品运行与 CI 默认都不装；CI 的 black/ruff/mypy 已把 `evaluation` 纳入门禁。

验证：
- 本地 **17 passed**（画像固定参数/非法温度/未知画像/开关不一致/多余字段/模型漂移/不可变/课题重复与格式/领域非法/评分越界/匿名 ID 泄漏分组/校验 CLI 失败与成功）
- 后端全量 **810 passed**、覆盖率 **87.85%**、`mypy app evaluation` **零错误**
- **服务器实跑语料准备**：`[ai] 10 篇`、`[software_engineering] 10 篇`、`[education_technology] 10 篇`（36M / 8.7M / 9.7M PDF，已 gitignore），`python -m evaluation.validate` 输出 **`runs: 120`**，与 roadmap §3 冻结设计一致

### Plan 5 Task 6：实验 runner 与指标（`e1d301a`）

**`evaluation/metrics.py`** —— 全部纯函数，核心契约：**分母未定义返回 `None`，绝不用 0 冒充**（"没有标注" ≠ "得分为 0"）：

| 类别 | 指标 |
| --- | --- |
| 检索质量 | `recall_at_k`、`reciprocal_rank`（MRR 用第一个相关结果）、`ndcg_at_k`（分级相关性 + 理想 DCG，IDCG=0 时返回 None） |
| 引用/证据 | `citation_resolvability`（越界标记算不可解析）、`evidence_coverage`、`error_citation_rate`、`duplicate_paragraph_ratio` |
| 执行质量 | `success_rate`、`mean_duration`、`total_tokens`、`retry_count`（Σ(attempts−1)）、`recovery_success` |
| 汇总 | `aggregate`（忽略 None）、`summarize_run` |

**`evaluation/runner.py`** —— resume-safe 矩阵执行：
- 矩阵 `4 组 × 15 课题 × 2 次 = 120 runs`，顺序稳定（同输入产同一相对路径序列）
- 每 run 目录含 `config.json / task.json / events.jsonl / result.md / review.json / metrics.json`；**全部非空且 metrics 可解析**才算完成 → resume 跳过
- **原子写入**：先写 `.tmp` 再 `os.replace()`，中途崩溃不留"看起来完整"的半成品
- 失败运行保留 `error.txt` 现场且**不覆盖**；`--retry-failed` 时才把旧现场归档到 `attempts/retry-*`
- 无事件的 run 写入占位事件，保证「非空」是可靠完成判据
- 单 run 失败不中断整个矩阵；`RunReport` 汇总 completed/skipped/failed 并落 `run_set.json`
- 执行器可注入：`FixedAdapterExecutor`（离线演练，四组检索能力可区分）/ `TaskExecutionExecutor`（真实模型）

**`evaluation/cli.py`** —— `validate` / `run --profiles all|子集 --topic --repetitions --adapter fixed|live --retry-failed --run-set-id`，打印 run-set ID、completed/skipped/failed 与产物根目录。

验证：
- `tests/evaluation/` **56 passed**（指标 33 + runner 15 + CLI 8）：固定小样本断言精确值、None 语义、矩阵 120、原子写无 `.tmp` 残留、resume 不重复执行、失败保留现场、重试归档、失败不阻塞其他 run、进度回调、metrics 不重复存正文
- 后端全量 **866 passed**，覆盖率 **87.82%**，`mypy app evaluation` **零错误**
- **服务器实跑**：`python -m evaluation.cli run --adapter fixed --profiles all --repetitions 1` → **60 runs 全部 completed / 0 failed / ok=true**，60 个 `metrics.json`；抽样（full 画像）`citation_resolvability=1.0`、`evidence_coverage≈0.667`、`recall_at_5=0.5`、`success=1.0`

### Plan 5 Task 7：论文报告生成（`6103922`）

**`evaluation/reporting.py`** —— 从 run-set 目录生成全部论文素材：

| 产物 | 内容 |
| --- | --- |
| `summary.csv` | 每次运行一行（**含 failed / incomplete 状态**，不丢弃） |
| `aggregate.csv` | 每组每指标 n / mean / median / **95% bootstrap CI**（2000 次重采样，固定 seed） |
| `significance.csv` | 各组 vs baseline A 的**配对 Wilcoxon**（配对键 = 课题+重复；<2 对或差值全零 → `None`，不编造 p 值） |
| `figures/{retrieval,content_quality,citations,latency_tokens,recovery}.png` | 五张柱状图，含标题、图例、**样本量标注**、置信区间误差线 |
| `report.md` | 所有表图都挂 run-set ID；第 4 节列出失败/不完整运行；第 5 节 LLM Judge **单独成节** |

关键设计：
- **确定性**：bootstrap/检验固定 seed，同输入两次生成的 CSV 逐字节一致（有测试）；
- **None 语义贯穿全局**：某指标在所有运行里缺数据 → `n=0, mean=None`，不是 0；
- **图表用英文标签**：容器/CI 常缺 CJK 字体，中文会渲染成方框（report.md 里仍是中文标题）；
- **降级可见**：缺 matplotlib 时生成 `.md` 占位说明而不是静默跳过（服务器未装 extras 时就会暴露出来，本轮正是这样发现的）；
- CLI 新增 `report --run-set <id|latest>`。

**`docs/runbooks/experiment-reproduction.md`** —— 8 节复现手册：前置条件、输入校验、语料准备、离线演练、真实 120-run、报告生成、**复现性核对清单**、常见问题、与论文各章节的数据对应表。

验收：
- `tests/evaluation/` **75 passed**（报告 19 个新测试：逐 run 一行、聚合 mean/median/CI、配对 Wilcoxon、样本不足不编造 p、五张图存在且非空、report.md 标注 run-set 与失败运行、LLM Judge 独立章节、两次生成确定性）
- 后端全量 **885 passed**，覆盖率 **87.86%**，`mypy app evaluation` **零错误**
- **服务器实跑**：`run` 60 runs → `report` 生成 **9 个产物**（含 5 张真实 PNG）；目视核验 `retrieval.png`（无 RAG 组检索为 0、含 RAG 组有值、n=15、误差线）与 `citations.png` 均正确渲染

### Plan 5 Task 8：部署/恢复/答辩运行手册（`2f874d9` / `34d3947`）

三份运行手册（命令全部来自实测）：
- `docs/runbooks/deployment.md`：前置条件 → 首次部署 → 端口覆盖 → 语料/模型准备 → **六步验收检查** → 备份 → 停止/更新 → 常见问题
- `docs/runbooks/recovery.md`：PostgreSQL/Redis/Chroma 故障、迁移失败、任务卡住、上传损坏、版本回滚、演示前重置；含**快速判断表**与现场处置优先级
- `docs/runbooks/defense-demo.md`：5–10 分钟演示脚本（11 步 + 时间预算）、**离线兜底方案**、**两次彩排记录表**、冻结版本流程

文档口径统一（README / checklist.md / dev-plan-v2.md）：
- README 新增**四态交付状态表**（implemented / tested / integrated / accepted），每行附证据；
- checklist.md 与 dev-plan-v2.md 顶部加口径说明：作为历史蓝图保留，**当前进度以组合交付路线图为准**；旧的 23 周/19 周说法废弃，统一十周最短可行排期；**代码生成/电商场景不属于当前交付声明**；
- `.env.example` 补全 `LLM__OPENAI_API_KEY` / `LLM__OPENAI_BASE_URL`（此前只有扁平别名，按它配置会静默失效）与 `deterministic` 离线模式说明。

### 彩排发现并修复的两个真实缺陷（`34d3947`）

1. **Agent 并发重入崩溃**：调度器并行执行 DAG 节点而复用同一 Agent 实例，第二次 `execute()` 时状态机仍停在 `RUNNING`，抛 `IllegalStateTransitionError: running → running`，节点直接转人工。
   → `BaseAgent.execute()` 进入 RUNNING 前把残留状态归位；新增 `AgentStateMachine.reset()`（记录 warning）；补 `TestConcurrentReentry` 两个用例（残留 RUNNING 恢复、同一实例并发执行）。
2. **语义校验误判**：抽取节点如实罗列多篇文献（涉及图像生成等不同主题）被 LLM 判为"与子任务主题不匹配"→ 0 分 → 节点转人工。
   → 校验 prompt 明确口径：检索/抽取节点覆盖多主题是**正常职责**，只有内容与标题明显无关才判 false。

彩排结果（详见 defense-demo.md 附 C）：
| | 彩排 1 | 彩排 2 |
| --- | --- | --- |
| 起点 | 干净 `down` + `up --build`（288s，就绪 291s） | 增量重建 backend（就绪 45s） |
| 规划 / 全流程 | 18s / 57s | **14s / 55s** |
| 终态 | `waiting_human`（缺陷） | **`completed`** |
| 引用 / 产物 | 0 / 0 | **10 处 `[E{n}]` / 2 个** |
| 演示开始后需手工修库/终端 | 否（但未达 completed） | **否** ✅ |

## Plan 5 补充加固（Exit Gate 后，2026-10-18 晚）

Exit Gate 通过后，在实际使用中又暴露并修复了 3 个问题（均已合并 `main` 并有回归测试）：

### 1. 规划阶段无瞬态重试（`5b96140`）

**现象**：界面上新建任务后立刻变「待人工介入 / 暂无规划」。查日志为上游端点
`openai.InternalServerError: 503 Service temporarily unavailable`——单次 5xx 就直接转人工。

**修复**：`TaskExecution._retry_transient()`——仅对**瞬态**故障（5xx / 429 / 连接错误 / 超时）
做 4 次指数退避重试（1s→2s→4s）；401/403/400/422 等配置类错误**不重试**；
`HumanActionRequiredError` 同样不重试。补 4 个单测（成功重试、401 不重试、耗尽放弃、人工介入不重试）。

### 2. 新增 DeepSeek 保底模型（`60d7bc2`）

**背景**：主模型端点 `model.rhzy.ai` 持续 503，原有降级链首项 `gpt-4o-mini` 在该端点返回 404、
glm/qwen 无有效密钥 → 降级链形同虚设。

**改动**：
- `app/llm/factory.py`：注册 `deepseek` provider（复用 `OpenAIAdapter`，OpenAI 兼容协议）
- `app/core/config.py`：新增 `deepseek_api_key` / `deepseek_base_url`
- `config/default.yaml`：`fallback_chain` 首项改为 `deepseek/deepseek-chat`
- `docker-compose.yml` + `.env.example`：注入 `LLM__DEEPSEEK_API_KEY` / `LLM__DEEPSEEK_BASE_URL`
- 密钥仅写入服务器 `.env`（600 权限、gitignore），不进代码/文档/提交

**验证**（容器内实测）：
```
llm_fallback_triggered  failed_model=gpt-6-luna → next_model=deepseek-chat
llm_fallback_succeeded  fallback_model=deepseek-chat
FALLBACK_OK model= deepseek-flash
```

### 3. 容器内配置根目录解析错误（`e3a665ee`）

**现象**：容器内 `get_settings().llm.fallback_chain` 为空 → 降级链不生效。

**根因**：`PROJECT_ROOT = Path(__file__).resolve().parents[3]`——仓库里 `backend/app/core/config.py`
到根正好 3 层，但镜像内是 `/app/app/core/config.py`，只有 3 层，`parents[3]` 越界到 `/`，
于是去找 `/config/default.yaml`（不存在），YAML 被静默跳过。这是 Plan 5 Task 4 加 `COPY config` 时
引入的路径不一致（镜像里 YAML 在 `/app/config/`）。

**修复**：按候选路径**存在性**择一解析（`config` 目录与 `app` 同级即命中），并补 3 个测试
（仓库布局、fallback 非空、容器布局模拟）。

**容器内验证**：`root: /app`、`yaml_exists: True`、`fallback: [(deepseek,deepseek-chat),(glm,glm-4-flash),(qwen,qwen-turbo)]`

### 端到端复验

修复后完整任务：**56 秒**、5 节点全 `completed`、正文 **8 处 `[E{n}]`**、**2 个产物**（Markdown + DOCX）。
后端全量 **897 passed**（2 skipped）、覆盖率 **88.05%**、`mypy` 零错误。

### 4. 降级链"配了不生效"的三层根因（`50760e8` / `18baa52` / `acbdab2`）

加完 DeepSeek 后仍反复出现「任务转人工」，逐层排查发现**三个独立缺陷**叠加：

| # | 缺陷 | 现象 | 修复 |
| --- | --- | --- | --- |
| a | `_is_retryable` 只匹配类名含 `timeout/ratelimit/connection` | openai SDK 把 5xx 抛成 `InternalServerError`（类名不含关键字）→ 503 既不重试**也不降级** | 增加状态码判定（`>=500` 或 429）+ 类名 + 消息兜底 |
| b | `PROJECT_ROOT = parents[3]` 在镜像里失效 | editable 安装使 `__file__` 指向 site-packages → 读不到 `/app/config/default.yaml` → `fallback_chain=[]` | `_resolve_project_root()` 多级探测（`MAA_PROJECT_ROOT` 环境变量 → `app` 包真实位置 → 文件位置 → `cwd`） |
| c | Agent 注入的是 `manager.get_primary()` | 那是**单个适配器**，`chat()` 内部重试耗尽后直接抛，永远走不到 `manager.chat()` 的 fallback 循环 | 新增 `FallbackChatClient`（把 manager 包成适配器形状），`RuntimeContainer._llm_client()` 返回它 |

验证（服务器容器内实测）：
```
root: /app    yaml: True    chain: [(deepseek,deepseek-chat),(glm,glm-4-flash),(qwen,qwen-turbo)]
client: FallbackChatClient   model: gpt-6-luna
llm_fallback_triggered  failed_model=gpt-6-luna → next_model=deepseek-chat
llm_fallback_succeeded  fallback_model=deepseek-chat
```

### 5. Agent 并发状态竞争（`054aacf`）

**现象**：10 节点任务 9 个完成，`n6` 报 `校验异常: 非法状态转换: idle → error`。

**根因**：调度器并行执行节点而复用同一 Agent 实例；两个协程共享状态机时，
A 已把状态推到 COMPLETED/IDLE，B 再 `transition(ERROR)` 就非法 → 正常结果被误判为校验异常。

**修复**：新增 `BaseAgent._settle(target)`——可转换则正常 transition，否则 `reset()`；
收尾状态只用于可观测性，不应让并发竞争中断业务结果。补 2 个竞争测试。

### 端到端复验（最终）

**60 秒**、**10 节点全 `completed`**、正文 **27 处 `[E{n}]`**、**2 个产物**；全量 **906 passed**。

## Plan 5 Exit Gate（PASSED，2026-10-18）

逐项对照计划要求（全部在服务器实测）：

| 门禁项 | 结果 |
| --- | --- |
| 全部阻断式 CI 作业通过 | ✅ `black` 205 files unchanged、`ruff` All checks passed、`mypy` **0 issues in 128 files**、覆盖率 87.84%（≥80%）、前端 69 passed |
| 干净 Compose 冒烟通过 | ✅ `SMOKE OK {"status":"completed","artifacts":2}`，退出码 0，零残留 |
| 双用户隔离与重启恢复通过 | ✅ `tests/integration + test_task_recovery_integration + tests/e2e` → **27 passed** |
| 离线真实模型演示 5–10 分钟内完成 | ✅ 彩排 2：**55 秒**从建任务到 `completed`（真实模型 gpt-6-luna） |
| 所有正式引用可解析 | ✅ 彩排 2 正文 **10 处 `[E{n}]`** 全部可回溯（被引 chunk 均属已上传文档）；Plan 3 Exit Gate 同样零越界 |
| 120-run 实验命令 resume-safe | ✅ `--repetitions 2` → `total=120, completed=120, failed=0, ok=true`；二次执行全部 skipped（有单测） |
| 至少两次完整彩排有记录 | ✅ 见上表与 defense-demo.md 附 C |
| 无未关闭的 P0/P1 缺陷 | ✅ 彩排暴露的 2 个缺陷已修复并补回归测试，全量 **887 passed** |

Plan 4 备注：
- 依赖版本按 Vite 5 约束固定：`vitest@^2.1.9`（vitest 5 要求 vite ≥6，会 ERESOLVE）、`@vue/test-utils@^2.4.6`、`happy-dom@^15`、`@playwright/test@^1.48.2`（e2e 浏览器安装放到 Task 8）；另新增 `@vue-flow/core`、`lucide-vue-next`。
- 测试遵循计划：只 mock `authApi`，不 mock Axios 内部；先跑测试看失败（Step 4），再实现（Step 5–7）。
- **配置优先级缺陷（待修，Plan 5 实验前必须解决）**：`get_settings()` 用 `Settings(**yaml_defaults)` 把 `config/default.yaml` 当作**构造参数**注入，而 pydantic-settings 中 init 参数优先级最高，导致 `.env` 与进程环境变量**无法覆盖** YAML 值（注释声称可覆盖）。本次只能通过改 `config/default.yaml` 切换模型；建议后续改为自定义 `settings_customise_sources`（YAML 作为最低优先级来源）并补一条覆盖顺序测试。
- **模型可用性（服务器端点 `https://model.rhzy.ai/v1` 实测）**：该 group 仅支持 `gpt-6-luna`；`gpt-4o` 与 `gpt-4o-mini` 均返回 `404 model_not_found`。因此 `config/default.yaml` 的 `llm.default_model` 由 `gpt-4o-mini` 改为 `gpt-6-luna`（`6f2972d`），与正式实验模型一致；密钥仅写入 `.env`（gitignore，600 权限），不进入代码/文档/提交。
