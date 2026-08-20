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
