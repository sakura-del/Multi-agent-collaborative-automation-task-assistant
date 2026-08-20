# Persistent Task Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立鉴权任务接口、结构化规划、持久化 DAG 调度、检查点恢复、人工控制和鉴权事件流组成的完整任务运行时。

**Architecture:** `TaskExecution` 是路由调用的唯一编排接口，后台任务使用独立 SQLAlchemy session。`TaskScheduler` 通过异步 observer 持久化每次节点转换；数据库事务提交后由事件 publisher 发布 Redis 通知。

**Tech Stack:** FastAPI、Pydantic 2、SQLAlchemy asyncio、Alembic、PostgreSQL、Redis Pub/Sub、asyncio、NetworkX、pytest。

---

## File Structure

- Create: `backend/app/orchestration/contracts.py` - TaskPlan、结果、事件和命令契约。
- Create: `backend/app/db/repositories/task.py` - owner-scoped 任务、DAG、子任务访问。
- Create: `backend/app/db/repositories/event.py` - 递增任务事件。
- Create: `backend/app/orchestration/persistence.py` - Scheduler 数据库 observer。
- Create: `backend/app/orchestration/events.py` - publisher、subscriber 与 WebSocket hub。
- Create: `backend/app/orchestration/runtime.py` - LLM/Agent/Scheduler 运行时依赖容器。
- Create: `backend/app/orchestration/services/task_execution.py` - 深模块外部接口。
- Create: `backend/app/api/v1/endpoints/tasks.py` - 任务命令与查询 HTTP adapter。
- Create: `backend/app/schemas/task.py` - HTTP Schema。
- Create: `backend/app/api/v1/endpoints/task_ws.py` - ticket 与任务 WebSocket。
- Create: `backend/alembic/versions/8f1c2b3d4e5f_add_persistent_task_runtime.py` - 运行时字段迁移。
- Create: `backend/tests/test_task_repository.py`, `test_task_api.py`, `test_task_execution.py`, `test_task_events.py`, `test_task_recovery_integration.py`.
- Modify: `backend/app/db/models/task.py`, `backend/app/db/models/log.py` - 持久化字段与约束。
- Modify: `backend/app/orchestration/task_state.py`, `scheduler.py`, `agents/planner.py` - 结构化状态与 observer。
- Modify: `backend/app/api/v1/router.py`, `backend/app/main.py` - 路由和 lifespan。

### Task 1: Add Runtime ORM Fields And Typed Contracts

**Files:**
- Modify: `backend/app/db/models/task.py`
- Modify: `backend/app/db/models/log.py`
- Create: `backend/app/orchestration/contracts.py`
- Create: `backend/alembic/versions/8f1c2b3d4e5f_add_persistent_task_runtime.py`
- Create: `backend/tests/test_task_runtime_models.py`

- [ ] **Step 1: Write model contract tests**

Assert `Task` has `task_type`, `config`, `plan`, `plan_version`, `paused_from_status`, `review_report`, and `artifacts`; assert `ExecutionLog` has `event_type`, `event_seq`, and `attempt`; assert `(task_id, node_id)` and `(task_id, event_seq)` are unique constraints.

- [ ] **Step 2: Run the test and verify missing fields**

```powershell
Set-Location backend
python -m pytest tests/test_task_runtime_models.py -v
```

Expected: FAIL on missing attributes.

- [ ] **Step 3: Extend ORM models without adding another table**

Add to `Task`:

```python
task_type: Mapped[str] = mapped_column(String(32), default="literature_review", nullable=False)
config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
plan: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
plan_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
paused_from_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
review_report: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
artifacts: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
```

Add `UniqueConstraint("task_id", "node_id", name="uq_subtask_task_node")` to `Subtask`.

Add to `ExecutionLog`:

```python
event_type: Mapped[str] = mapped_column(String(32), nullable=False, default="log")
event_seq: Mapped[int] = mapped_column(Integer, nullable=False)
attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

__table_args__ = (
    UniqueConstraint("task_id", "event_seq", name="uq_execution_log_task_event_seq"),
)
```

- [ ] **Step 4: Define all shared contracts once**

Create `contracts.py` with `ConfigDict(extra="forbid")` on every model:

```python
class TaskStatus(StrEnum):
    CREATED = "created"
    PLANNING = "planning"
    WAITING_APPROVAL = "waiting_approval"
    RUNNING = "running"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    PAUSED = "paused"
    RETRYING = "retrying"
    WAITING_HUMAN = "waiting_human"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskCommand(StrEnum):
    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"
    RETRY_NODE = "retry_node"
    SUPPLY_INPUT = "supply_input"


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc_id: UUID | None = None
    chunk_id: UUID | None = None
    source_url: HttpUrl | None = None
    excerpt: str = Field(min_length=1, max_length=2000)


class PlanNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    type: Literal["search", "extract", "write", "validate", "review"]
    title: str = Field(min_length=1, max_length=256)
    dependencies: list[str] = Field(default_factory=list)
    input: dict[str, Any] = Field(default_factory=dict)
    required_evidence: int = Field(default=1, ge=0, le=50)


class TaskPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    topic: str = Field(min_length=5, max_length=200)
    research_questions: list[str] = Field(min_length=1, max_length=10)
    search_queries: list[str] = Field(min_length=1, max_length=20)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=20)
    nodes: list[PlanNode] = Field(min_length=1, max_length=50)


class ExecutionResult(BaseModel):
    node_id: str
    content: str
    evidence: list[Evidence] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    duration_ms: int = Field(ge=0)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)


class ValidationCheck(BaseModel):
    name: str
    passed: bool
    message: str


class ValidationReport(BaseModel):
    passed: bool
    score: float = Field(ge=0, le=100)
    checks: list[ValidationCheck]
    retryable: bool
    feedback: str


class ReviewReport(BaseModel):
    final_markdown: str
    bibliography: list[dict[str, Any]]
    scores: dict[str, float]
    issues: list[str]
    stats: dict[str, Any]


class TaskEvent(BaseModel):
    event_id: int
    task_id: UUID
    subtask_id: UUID | None = None
    event_type: str
    status: str
    occurred_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)
```

- [ ] **Step 5: Write reversible migration**

Migration `upgrade()` adds the fields, backfills existing rows, then adds non-null and unique constraints. `downgrade()` removes constraints and columns in reverse order. Use batch operations for SQLite test compatibility where required.

- [ ] **Step 6: Run model, migration and formatting checks**

```powershell
python -m pytest tests/test_task_runtime_models.py tests/test_repository.py -q
alembic upgrade head
alembic downgrade -1
alembic upgrade head
python -m ruff check app/orchestration/contracts.py app/db/models tests/test_task_runtime_models.py
```

Expected: PASS and migration round-trip succeeds.

- [ ] **Step 7: Commit**

```powershell
git add backend/app/db/models backend/app/orchestration/contracts.py backend/alembic/versions/8f1c2b3d4e5f_add_persistent_task_runtime.py backend/tests/test_task_runtime_models.py
git commit -m "feat(tasks): add persistent task contracts"
```

### Task 2: Add Owner-Scoped Task And Event Repositories

**Files:**
- Create: `backend/app/db/repositories/task.py`
- Create: `backend/app/db/repositories/event.py`
- Create: `backend/tests/test_task_repository.py`

- [ ] **Step 1: Write repository behavior tests**

Cover create/list/get for owner, cross-user lookup returning `None`, atomic legal status update, DAG/subtask replacement for a plan version, and `events_after(task_id, user_id, event_seq)` ordering.

- [ ] **Step 2: Run tests and verify imports fail**

```powershell
Set-Location backend
python -m pytest tests/test_task_repository.py -v
```

Expected: FAIL because repositories do not exist.

- [ ] **Step 3: Implement the task repository interface**

```python
class TaskRepository(BaseRepository[Task]):
    model = Task

    async def get_for_user(self, user_id: UUID, task_id: UUID) -> Task | None: ...
    async def list_for_user(self, user_id: UUID, *, skip: int, limit: int) -> list[Task]: ...
    async def lock_for_update(self, task_id: UUID) -> Task: ...
    async def replace_plan(self, task: Task, plan: TaskPlan, dag: TaskDAG) -> None: ...
    async def transition(self, task: Task, expected: set[TaskStatus], target: TaskStatus) -> None: ...
    async def completed_node_ids(self, task_id: UUID) -> set[str]: ...
```

`get_for_user()` and `list_for_user()` must include `Task.user_id == user_id` in SQL. `transition()` raises `IllegalTaskStateError` when the current state is outside `expected`.

- [ ] **Step 4: Implement monotonic event allocation**

`EventRepository.append()` first locks the parent `Task` row, then calculates `max(event_seq) + 1`, inserts `ExecutionLog`, flushes, and returns `TaskEvent`. The caller owns commit.

```python
async def events_after(
    self,
    user_id: UUID,
    task_id: UUID,
    after: int,
    limit: int = 500,
) -> list[ExecutionLog]:
    statement = (
        select(ExecutionLog)
        .join(Task, Task.id == ExecutionLog.task_id)
        .where(
            Task.user_id == user_id,
            Task.id == task_id,
            ExecutionLog.event_seq > after,
        )
        .order_by(ExecutionLog.event_seq)
        .limit(min(limit, 500))
    )
```

- [ ] **Step 5: Run repository tests**

```powershell
python -m pytest tests/test_task_repository.py -q
```

Expected: PASS, including concurrent sequence allocation in the PostgreSQL integration marker.

- [ ] **Step 6: Commit**

```powershell
git add backend/app/db/repositories/task.py backend/app/db/repositories/event.py backend/tests/test_task_repository.py
git commit -m "feat(tasks): add owner-scoped repositories"
```

### Task 3: Persist Every Scheduler Transition Through An Observer

**Files:**
- Modify: `backend/app/orchestration/task_state.py`
- Modify: `backend/app/orchestration/scheduler.py`
- Create: `backend/app/orchestration/persistence.py`
- Modify: `backend/tests/test_task_state.py`
- Modify: `backend/tests/test_scheduler.py`

- [ ] **Step 1: Write READY/CANCELLED and observer tests**

Add `PENDING -> READY -> RUNNING`, `PAUSED -> READY`, and all active states to `CANCELLED`. Assert an async observer receives transitions in exact order and receives the final `NodeResult` once.

- [ ] **Step 2: Run focused tests and verify failures**

```powershell
Set-Location backend
python -m pytest tests/test_task_state.py tests/test_scheduler.py -q
```

Expected: FAIL because statuses and observer are absent.

- [ ] **Step 3: Add the observer seam**

In `scheduler.py`:

```python
class ScheduleObserver(Protocol):
    async def on_transition(
        self,
        task_id: str,
        node_id: str,
        old: SubtaskStatus,
        new: SubtaskStatus,
    ) -> None: ...

    async def on_result(self, task_id: str, result: NodeResult) -> None: ...


class NullScheduleObserver:
    async def on_transition(self, task_id, node_id, old, new) -> None:
        return None

    async def on_result(self, task_id, result) -> None:
        return None
```

`TaskScheduler` receives `observer: ScheduleObserver | None`. Replace direct transitions inside scheduler with one `_transition(task_id, sm, target)` method that changes the state and then awaits `observer.on_transition()`.

- [ ] **Step 4: Implement the database observer**

`DatabaseScheduleObserver` opens an independent session for each callback, locks the task/subtask row, updates the subtask state/retry/output, appends an `ExecutionLog` event, commits, then calls `EventPublisher.publish()`.

If commit fails, do not publish. If publish fails, keep the committed database transition and log a warning.

- [ ] **Step 5: Pass task context into node execution**

Change `_execute_node(dag, node_id)` to `_execute_node(dag, node_id, task_id)` and include `task_id`, `subtask_id`, `attempt`, and dependency outputs in `input_data`. Do not use `trace_id=f"task-{node_id}"`; use the task's persisted trace ID from config.

- [ ] **Step 6: Run scheduler regressions**

```powershell
python -m pytest tests/test_task_state.py tests/test_scheduler.py tests/test_scheduler_integration.py -q
```

Expected: PASS and the legacy no-observer path behaves as before.

- [ ] **Step 7: Commit**

```powershell
git add backend/app/orchestration/task_state.py backend/app/orchestration/scheduler.py backend/app/orchestration/persistence.py backend/tests/test_task_state.py backend/tests/test_scheduler.py
git commit -m "feat(orchestration): persist scheduler transitions"
```

### Task 4: Build Runtime Container And TaskExecution Deep Module

**Files:**
- Create: `backend/app/orchestration/runtime.py`
- Create: `backend/app/orchestration/services/task_execution.py`
- Create: `backend/tests/test_task_execution.py`
- Modify: `backend/app/orchestration/agents/planner.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Write TaskExecution lifecycle tests**

Using a deterministic planner and agents, assert:

1. `submit()` persists `PLANNING` and returns a UUID;
2. planning persists a valid plan and moves to `WAITING_APPROVAL`;
3. nothing executes before `approve_plan()`;
4. approval moves to `RUNNING` and completes;
5. invalid planner JSON performs one schema-repair call then enters `WAITING_HUMAN`;
6. `recover()` schedules only active tasks and skips completed nodes.

- [ ] **Step 2: Run test and verify imports fail**

```powershell
Set-Location backend
python -m pytest tests/test_task_execution.py -v
```

Expected: FAIL because runtime files do not exist.

- [ ] **Step 3: Make Planner return `TaskPlan`**

Replace the generic decomposition schema and handwritten Kahn implementation with `TaskPlan.model_validate_json()` followed by `TaskDAG.from_dict()` validation. Preserve `_safe_parse_json()` only for extracting a JSON object; schema violations must be visible to the repair attempt.

- [ ] **Step 4: Implement one dependency container**

`RuntimeContainer` owns `LLMManager`, Agent factory, knowledge query interface, event publisher and background task registry. It exposes:

```python
class RuntimeContainer:
    async def start(self) -> None: ...
    async def close(self) -> None: ...
    def create_scheduler(self, observer: ScheduleObserver) -> TaskScheduler: ...
    def create_planner(self) -> PlannerAgent: ...
    def track(self, task_id: UUID, coroutine: Coroutine[Any, Any, None]) -> None: ...
    async def cancel(self, task_id: UUID) -> None: ...
```

`close()` cancels and awaits only coroutines started by this container.

- [ ] **Step 5: Implement the external interface**

`TaskExecution` exposes exactly:

```python
async def submit(self, user_id: UUID, request: TaskCreate) -> UUID: ...
async def approve_plan(self, user_id: UUID, task_id: UUID, revision: int) -> TaskSnapshot: ...
async def control(self, user_id: UUID, task_id: UUID, command: TaskControl) -> TaskSnapshot: ...
async def get_snapshot(self, user_id: UUID, task_id: UUID) -> TaskSnapshot: ...
async def events_after(self, user_id: UUID, task_id: UUID, event_id: int) -> list[TaskEvent]: ...
async def recover(self) -> None: ...
```

`submit()` commits the task before tracking `_plan(task_id)`. `approve_plan()` requires matching `revision` and `WAITING_APPROVAL`. `control()` uses a transition table, not route conditionals. Background methods create their own sessions from `AsyncSessionLocal`.

- [ ] **Step 6: Wire lifespan**

After logging, database and RAG initialization, create `RuntimeContainer`, attach it to `app.state.runtime`, call `TaskExecution.recover()`, then yield. On shutdown close runtime before Redis and engine disposal.

- [ ] **Step 7: Run task execution tests**

```powershell
python -m pytest tests/test_task_execution.py tests/test_planner_agent.py tests/test_scheduler.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```powershell
git add backend/app/orchestration/runtime.py backend/app/orchestration/services/task_execution.py backend/app/orchestration/agents/planner.py backend/app/main.py backend/tests/test_task_execution.py
git commit -m "feat(orchestration): add task execution runtime"
```

### Task 5: Expose Owner-Scoped Task Commands And Queries

**Files:**
- Create: `backend/app/schemas/task.py`
- Create: `backend/app/api/v1/endpoints/tasks.py`
- Create: `backend/tests/test_task_api.py`
- Modify: `backend/app/api/v1/router.py`

- [ ] **Step 1: Write API contract tests**

Cover create/list/get, plan approval with revision conflict, pause/resume/cancel, retry-node requiring `subtask_id`, supply-input requiring non-empty input, 401 without token, and 404 across users.

- [ ] **Step 2: Run tests and verify route failures**

```powershell
Set-Location backend
python -m pytest tests/test_task_api.py -v
```

Expected: FAIL with 404 because task routes are not mounted.

- [ ] **Step 3: Define strict HTTP schemas**

`TaskCreate` fields:

```python
topic: str = Field(min_length=5, max_length=200)
keywords: list[str] = Field(min_length=1, max_length=10)
date_from: date | None = None
date_to: date | None = None
max_documents: int = Field(default=20, ge=1, le=50)
output_language: Literal["zh", "en"] = "zh"
citation_style: Literal["gbt7714", "apa"] = "gbt7714"
document_ids: list[UUID] = Field(default_factory=list, max_length=50)
offline_dataset: bool = False
```

Also define `TaskControl`, `TaskSummary`, `TaskSnapshot`, `PlanApproval`, `TaskEventPage` and nested subtask/DAG schemas. Reject unknown fields.

- [ ] **Step 4: Keep routes thin**

Each route injects `current_user` and `TaskExecution` from `request.app.state.runtime`. Routes perform Pydantic validation only, then call one external interface method. Mount the router under `/tasks`.

- [ ] **Step 5: Run API tests and OpenAPI generation**

```powershell
python -m pytest tests/test_task_api.py -q
python -c "from app.main import app; app.openapi()"
```

Expected: PASS and OpenAPI generation exits 0.

- [ ] **Step 6: Commit**

```powershell
git add backend/app/schemas/task.py backend/app/api/v1/endpoints/tasks.py backend/app/api/v1/router.py backend/tests/test_task_api.py
git commit -m "feat(tasks): expose task lifecycle endpoints"
```

### Task 6: Add Authenticated Event Replay And WebSocket Streaming

**Files:**
- Create: `backend/app/orchestration/events.py`
- Create: `backend/app/api/v1/endpoints/task_ws.py`
- Create: `backend/tests/test_task_events.py`
- Modify: `backend/app/api/v1/router.py`

- [ ] **Step 1: Write ticket and replay tests**

Assert ticket issuance requires ownership, ticket TTL is 60 seconds, ticket consumption is single-use, wrong task/user is rejected, `events?after=2` returns only event 3+, and WebSocket sends a snapshot followed by live events.

- [ ] **Step 2: Run tests and verify imports fail**

```powershell
Set-Location backend
python -m pytest tests/test_task_events.py -v
```

Expected: FAIL because event streaming does not exist.

- [ ] **Step 3: Implement publisher and ticket interfaces**

```python
class EventPublisher:
    async def publish(self, event: TaskEvent) -> None: ...
    async def subscribe(self, task_id: UUID) -> AsyncIterator[TaskEvent]: ...


class WebSocketTicketService:
    async def issue(self, user_id: UUID, task_id: UUID) -> str: ...
    async def consume(self, ticket: str, task_id: UUID) -> UUID | None: ...
```

Use Redis `SET key value EX 60 NX` and atomic `GETDEL` for production. Tests inject an in-memory adapter with the same interface.

- [ ] **Step 4: Implement reconnect sequence**

WebSocket route order:

1. consume ticket before `accept()`;
2. load owner-scoped snapshot;
3. accept and send `{"type":"snapshot", ...}`;
4. replay `after_event_id` events from PostgreSQL;
5. forward Redis events;
6. stop subscription on disconnect.

Never log tickets, access tokens or full event payloads.

- [ ] **Step 5: Run event tests**

```powershell
python -m pytest tests/test_task_events.py tests/test_task_api.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add backend/app/orchestration/events.py backend/app/api/v1/endpoints/task_ws.py backend/app/api/v1/router.py backend/tests/test_task_events.py
git commit -m "feat(realtime): stream authenticated task events"
```

### Task 7: Prove Restartable End-To-End Task Execution

**Files:**
- Create: `backend/tests/test_task_recovery_integration.py`
- Modify: `backend/tests/test_scheduler_integration.py`
- Modify: `backend/app/orchestration/recovery.py`

- [ ] **Step 1: Replace the misleading manual-DAG closed loop**

Create a deterministic `BaseLLM` adapter whose Planner response contains a valid three-node `TaskPlan`. The integration test must call `POST /tasks`, wait for `WAITING_APPROVAL`, approve, then poll until `COMPLETED`. It must not manually construct the DAG.

- [ ] **Step 2: Write the crash recovery test**

Pause the observer after node `n1` commits, destroy the first runtime, create a new runtime against the same database, call `recover()`, and assert `n1` has one attempt while `n2` and `n3` complete.

- [ ] **Step 3: Run tests and verify recovery gaps**

```powershell
Set-Location backend
python -m pytest tests/test_scheduler_integration.py tests/test_task_recovery_integration.py -v
```

Expected: FAIL until recovery reads ORM state instead of JSON examples.

- [ ] **Step 4: Replace JSON-only recovery with repository recovery**

`RecoveryManager` must load `TaskDag.graph`, completed subtask node IDs and all non-terminal node states through `TaskRepository`. It resets abandoned `RUNNING`, `VALIDATING`, and `RETRYING` nodes to `READY`, appends a recovery event, and invokes Scheduler with `pre_completed`.

- [ ] **Step 5: Run the complete runtime suite**

```powershell
python -m pytest tests/test_task_state.py tests/test_scheduler.py tests/test_scheduler_integration.py tests/test_task_execution.py tests/test_task_api.py tests/test_task_events.py tests/test_task_recovery_integration.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add backend/app/orchestration/recovery.py backend/tests/test_scheduler_integration.py backend/tests/test_task_recovery_integration.py
git commit -m "test(orchestration): cover restartable task lifecycle"
```

## Plan 2 Exit Gate

Run one deterministic task through authenticated HTTP, approval, persisted Scheduler execution, event replay and restart recovery. Record task ID, event sequence, node attempt counts and exact pytest command. Do not start Plan 3 while any execution state exists only in memory.
