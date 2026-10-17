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

Plan 2 notes:

- 计划 2 在共享工作树内用分支 `codex/plan2-task-runtime` 开发，未再建独立 worktree（同一时间只有一个活跃计划，避免 Windows 侧 venv 与路径重复）。
- 迁移 `8f1c2b3d4e5f` 的 `event_seq` 历史回填使用 PostgreSQL `UPDATE ... FROM` + 窗口函数；本地 SQLite 不执行迁移 round-trip，统一在服务器 PostgreSQL 验证（SQLite 本身也不支持首版迁移里的 `now()` 默认值）。
- `EventRepository` 将 `TaskEvent.status` 存于 `ExecutionLog.context` 的保留键 `status`，不新增列；`to_event()` 回放时剥离该键，payload 原样返回。
- 跨仓开发流：Windows 本地写代码 + 本地 pytest（`--basetemp`），每个任务结束后 bundle 同步到服务器跑完整套件、迁移 round-trip 与集成测试。
