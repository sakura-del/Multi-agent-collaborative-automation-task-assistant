# Literature Review Frontend Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将阶段 0 登录页替换为可完成文献上传、任务创建、规划确认、DAG 监控、人工干预、结果查看和下载的答辩工作台。

**Architecture:** Vue Router 定义四个页面，Pinia 保存鉴权与任务快照，HTTP adapter 与 WebSocket adapter 分离。任务详情始终以服务端快照为准，WebSocket 只追加增量事件，失败时自动轮询。

**Tech Stack:** Vue 3、TypeScript、Pinia、Vue Router、Axios、Vue Flow、Lucide Vue、Vitest、Vue Test Utils、Playwright、Vite。

---

## File Structure

- Create: `frontend/src/router/index.ts` - 路由与鉴权守卫。
- Create: `frontend/src/stores/auth.ts`, `tasks.ts`, `knowledge.ts` - 状态与动作。
- Create: `frontend/src/api/client.ts`, `auth.ts`, `tasks.ts`, `knowledge.ts`, `types.ts` - 后端契约 adapter。
- Create: `frontend/src/services/task-events.ts` - WebSocket、重放和轮询降级。
- Create: `frontend/src/layouts/AppLayout.vue` - 工作台导航布局。
- Create: `frontend/src/views/LoginView.vue`, `TaskListView.vue`, `TaskDetailView.vue`, `KnowledgeView.vue`.
- Create: `frontend/src/components/tasks/TaskCreateForm.vue`, `PlanPanel.vue`, `TaskDag.vue`, `TaskToolbar.vue`, `TaskLog.vue`, `TaskResult.vue`, `NodeDetail.vue`.
- Create: `frontend/src/components/knowledge/DocumentUpload.vue`, `DocumentTable.vue`.
- Create: `frontend/src/components/common/StatusBadge.vue`, `ErrorBanner.vue`, `IconButton.vue`.
- Create: `frontend/src/test/setup.ts` and component/store tests.
- Create: `frontend/e2e/literature-review.spec.ts`, `frontend/playwright.config.ts`.
- Modify: `frontend/src/App.vue`, `main.ts`, `assets/main.css`, `vite.config.ts`, `package.json`, `package-lock.json`.

### Task 1: Add Tested Workspace Shell And Authentication Store

**Files:**
- Modify: `frontend/package.json`, `frontend/package-lock.json`
- Modify: `frontend/src/main.ts`, `frontend/src/App.vue`, `frontend/src/assets/main.css`, `frontend/vite.config.ts`
- Create: `frontend/src/router/index.ts`
- Create: `frontend/src/layouts/AppLayout.vue`
- Create: `frontend/src/views/LoginView.vue`
- Create: `frontend/src/stores/auth.ts`
- Create: `frontend/src/api/client.ts`, `frontend/src/api/auth.ts`, `frontend/src/api/types.ts`
- Create: `frontend/src/test/setup.ts`, `frontend/src/stores/auth.test.ts`

- [ ] **Step 1: Install only required UI and test dependencies**

```powershell
Set-Location frontend
npm install @vue-flow/core lucide-vue-next
npm install --save-dev vitest @vue/test-utils happy-dom @playwright/test
```

Add scripts:

```json
{
  "test:unit": "vitest",
  "test:e2e": "playwright test"
}
```

- [ ] **Step 2: Configure Vitest**

In `vite.config.ts`, use `defineConfig` from `vitest/config` and add:

```typescript
test: {
  environment: 'happy-dom',
  setupFiles: ['./src/test/setup.ts'],
  clearMocks: true,
}
```

`setup.ts` clears localStorage after every test.

- [ ] **Step 3: Write auth store tests**

Test login persistence, bootstrap from an access token, refresh rotation, logout clearing both tokens, and failed refresh redirect state. Mock only `authApi`, not Axios internals.

```typescript
it('rotates both tokens on refresh', async () => {
  vi.mocked(authApi.refresh).mockResolvedValue(tokens)
  const store = useAuthStore()
  localStorage.setItem('refresh_token', 'old')
  await store.refresh()
  expect(localStorage.getItem('access_token')).toBe(tokens.access_token)
  expect(localStorage.getItem('refresh_token')).toBe(tokens.refresh_token)
})
```

- [ ] **Step 4: Run the test and verify failure**

```powershell
npm run test:unit -- --run src/stores/auth.test.ts
```

Expected: FAIL because the store and new API files do not exist.

- [ ] **Step 5: Split the Axios adapter and add single-flight refresh**

`client.ts` exports one Axios instance. A 401 response starts one shared `refreshPromise`; concurrent requests await it, retry once with the new access token, and logout when refresh fails. Mark retried requests with a typed `_retry` property to prevent loops.

- [ ] **Step 6: Implement Pinia auth state**

```typescript
export const useAuthStore = defineStore('auth', () => {
  const user = ref<UserOut | null>(null)
  const ready = ref(false)

  const authenticated = computed(() => user.value !== null)

  async function bootstrap() {
    try {
      if (localStorage.getItem('access_token')) user.value = await authApi.me()
    } finally {
      ready.value = true
    }
  }

  async function logout() {
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
    user.value = null
  }

  return { user, ready, authenticated, bootstrap, logout }
})
```

Add login, register and refresh actions using the same token persistence helper.

- [ ] **Step 7: Build router and layout**

Routes are `/login`, `/tasks`, `/tasks/:taskId`, and `/knowledge`. The guard waits for `auth.ready`, redirects anonymous users to `/login`, and redirects authenticated users away from `/login`. `App.vue` contains only `<RouterView />`.

`AppLayout.vue` uses a quiet left navigation with Tasks and Knowledge links, current user, and icon logout button. Do not add a dashboard marketing hero or decorative cards.

- [ ] **Step 8: Run unit, type and build checks**

```powershell
npm run test:unit -- --run src/stores/auth.test.ts
npm run type-check
npm run build
```

Expected: PASS.

- [ ] **Step 9: Commit**

```powershell
git add frontend
git commit -m "feat(frontend): add authenticated workspace shell"
```

### Task 2: Add Typed Task API And Snapshot Store

**Files:**
- Create: `frontend/src/api/tasks.ts`
- Modify: `frontend/src/api/types.ts`
- Create: `frontend/src/stores/tasks.ts`
- Create: `frontend/src/stores/tasks.test.ts`

- [ ] **Step 1: Mirror backend contracts in TypeScript**

Define string unions for `TaskStatus`, `SubtaskStatus`, `TaskCommand`, and interfaces for `TaskCreate`, `TaskSummary`, `TaskSnapshot`, `TaskPlan`, `PlanNode`, `TaskEvent`, `ReviewReport`, and `Artifact`.

```typescript
export type TaskStatus =
  | 'created' | 'planning' | 'waiting_approval' | 'running'
  | 'reviewing' | 'completed' | 'paused' | 'retrying'
  | 'waiting_human' | 'failed' | 'cancelled'
```

- [ ] **Step 2: Write store merge tests**

Assert loading replaces a stale snapshot, an event with lower/equal `event_id` is ignored, a higher event updates status and `lastEventId`, and `stopWatching()` cancels timers and sockets.

- [ ] **Step 3: Run test and verify imports fail**

```powershell
npm run test:unit -- --run src/stores/tasks.test.ts
```

Expected: FAIL.

- [ ] **Step 4: Implement task HTTP adapter**

```typescript
export const taskApi = {
  list: () => api.get<TaskSummary[]>('/tasks').then(r => r.data),
  create: (payload: TaskCreate) => api.post<TaskSnapshot>('/tasks', payload).then(r => r.data),
  get: (id: string) => api.get<TaskSnapshot>(`/tasks/${id}`).then(r => r.data),
  approve: (id: string, revision: number) =>
    api.post<TaskSnapshot>(`/tasks/${id}/plan/approve`, { revision }).then(r => r.data),
  control: (id: string, payload: TaskControl) =>
    api.post<TaskSnapshot>(`/tasks/${id}/control`, payload).then(r => r.data),
  events: (id: string, after: number) =>
    api.get<TaskEventPage>(`/tasks/${id}/events`, { params: { after } }).then(r => r.data),
  wsTicket: (id: string) => api.post<WsTicket>('/ws-ticket', { task_id: id }).then(r => r.data),
}
```

- [ ] **Step 5: Implement store ownership of snapshots**

The store exposes `loadList`, `create`, `load`, `approve`, `control`, `applyEvent`, `watch`, and `stopWatching`. Views never call Axios directly.

- [ ] **Step 6: Run tests and type checks**

```powershell
npm run test:unit -- --run src/stores/tasks.test.ts
npm run type-check
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add frontend/src/api frontend/src/stores/tasks.ts frontend/src/stores/tasks.test.ts
git commit -m "feat(frontend): add typed task state"
```

### Task 3: Build Task List And Creation Workflow

**Files:**
- Create: `frontend/src/views/TaskListView.vue`
- Create: `frontend/src/components/tasks/TaskCreateForm.vue`
- Create: `frontend/src/components/common/StatusBadge.vue`, `ErrorBanner.vue`
- Create: `frontend/src/components/tasks/TaskCreateForm.test.ts`

- [ ] **Step 1: Write form validation tests**

Cover topic length, 1 to 10 keywords, max documents 1 to 50, date order, language, citation style and selected document IDs. Assert successful submission emits the exact `TaskCreate` payload once.

- [ ] **Step 2: Run test and verify component failure**

```powershell
npm run test:unit -- --run src/components/tasks/TaskCreateForm.test.ts
```

Expected: FAIL.

- [ ] **Step 3: Implement compact task creation**

Use standard labels and inputs, a tag input for keywords, numeric stepper for documents, segmented controls for language and citation style, and a checkbox for the offline demo dataset. Disable submit while pending and keep server error text near the form.

- [ ] **Step 4: Implement task list page**

Show title, status, progress, updated time and action to open. Use one table on desktop and a single-column list on mobile. Empty state contains one create command, not explanatory marketing text.

After successful create, navigate to `/tasks/{id}`.

- [ ] **Step 5: Run component tests, type check and build**

```powershell
npm run test:unit -- --run src/components/tasks/TaskCreateForm.test.ts
npm run type-check
npm run build
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add frontend/src/views/TaskListView.vue frontend/src/components/tasks frontend/src/components/common
git commit -m "feat(frontend): add task creation workflow"
```

### Task 4: Render Plan Approval And Stable DAG Inspection

**Files:**
- Create: `frontend/src/views/TaskDetailView.vue`
- Create: `frontend/src/components/tasks/PlanPanel.vue`, `TaskDag.vue`, `NodeDetail.vue`
- Create: `frontend/src/components/tasks/TaskDag.test.ts`
- Modify: `frontend/src/assets/main.css`

- [ ] **Step 1: Write DAG state tests**

Mount a diamond graph and assert nodes do not move when status labels change, completed/running/waiting/failed states map to distinct accessible labels, and selecting a node emits its ID.

- [ ] **Step 2: Run test and verify component failure**

```powershell
npm run test:unit -- --run src/components/tasks/TaskDag.test.ts
```

Expected: FAIL.

- [ ] **Step 3: Implement plan approval**

`PlanPanel` displays research questions, search queries, acceptance criteria and a read-only DAG. It provides only Regenerate and Approve commands. Approve sends the current `plan.version`; a 409 reloads the snapshot and shows a revision conflict.

- [ ] **Step 4: Implement DAG viewer with fixed geometry**

Use Vue Flow with deterministic positions calculated from topological depth and node index. Set fixed node width/height, disable dragging, connection creation, zoom-on-scroll and editing. Keep pan, fit-view and node selection. `NodeDetail` opens in a side drawer outside the DAG container.

- [ ] **Step 5: Implement detail page tabs**

Tabs are Plan, Execution, Logs and Result. The active tab derives from the route query so refresh preserves it. Result remains disabled until a review report exists.

- [ ] **Step 6: Run tests and build**

```powershell
npm run test:unit -- --run src/components/tasks/TaskDag.test.ts
npm run type-check
npm run build
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add frontend/src/views/TaskDetailView.vue frontend/src/components/tasks frontend/src/assets/main.css
git commit -m "feat(frontend): visualize task execution dag"
```

### Task 5: Add Realtime Events, Logs And Human Controls

**Files:**
- Create: `frontend/src/services/task-events.ts`
- Create: `frontend/src/components/tasks/TaskToolbar.vue`, `TaskLog.vue`
- Create: `frontend/src/services/task-events.test.ts`
- Modify: `frontend/src/stores/tasks.ts`
- Modify: `frontend/src/views/TaskDetailView.vue`

- [ ] **Step 1: Write reconnect and fallback tests**

Use fake timers and a fake WebSocket. Assert ticket acquisition, snapshot load before connection, replay after `lastEventId`, exponential reconnect at 1/3/9 seconds, polling every 3 seconds after three failures, and complete cleanup on route leave.

- [ ] **Step 2: Run test and verify service failure**

```powershell
npm run test:unit -- --run src/services/task-events.test.ts
```

Expected: FAIL.

- [ ] **Step 3: Implement event service**

```typescript
export interface TaskEventSession {
  start(taskId: string, after: number, onEvent: (event: TaskEvent) => void): Promise<void>
  stop(): void
}
```

The implementation acquires a single-use ticket, connects using `wss` when the page is HTTPS, ignores duplicate event IDs, and switches to HTTP replay polling after three failed reconnects.

- [ ] **Step 4: Implement state-specific controls**

Render only commands allowed by the current state. Retry Node requires a selected failed/waiting node. Supply Input opens a modal with one required text field. Cancel requires confirmation. Pause/resume commands are disabled while the previous command is pending.

- [ ] **Step 5: Implement structured log viewer**

Filter by Agent, node and level. Render timestamp, event type, status and message. Never render raw HTML. Keep log rows virtualized only if measured rendering exceeds 100 ms for 500 events; the default implementation paginates 100 rows.

- [ ] **Step 6: Run tests and build**

```powershell
npm run test:unit -- --run src/services/task-events.test.ts
npm run type-check
npm run build
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add frontend/src/services frontend/src/components/tasks/TaskToolbar.vue frontend/src/components/tasks/TaskLog.vue frontend/src/stores/tasks.ts frontend/src/views/TaskDetailView.vue
git commit -m "feat(frontend): add realtime task controls"
```

### Task 6: Build The Knowledge Library

**Files:**
- Create: `frontend/src/api/knowledge.ts`
- Create: `frontend/src/stores/knowledge.ts`
- Create: `frontend/src/views/KnowledgeView.vue`
- Create: `frontend/src/components/knowledge/DocumentUpload.vue`, `DocumentTable.vue`
- Create: `frontend/src/components/knowledge/DocumentUpload.test.ts`

- [ ] **Step 1: Write upload tests**

Assert accepted extensions, 30 MB client-side limit, multipart fields, progress state, upload error, successful list refresh, delete confirmation and no client-controlled file path field.

- [ ] **Step 2: Run test and verify component failure**

```powershell
npm run test:unit -- --run src/components/knowledge/DocumentUpload.test.ts
```

Expected: FAIL.

- [ ] **Step 3: Implement typed knowledge API**

`upload(file, title, strategy)` builds `FormData`; `list`, `remove`, `stats` and `search` mirror backend routes. Do not set the multipart content type manually; Axios supplies the boundary.

- [ ] **Step 4: Implement the library page**

Use one upload tool followed by an unframed table showing title, type, source, index status, chunks and created time. Delete uses a trash icon with tooltip and confirmation. Index failures show a concise reason and no stack trace.

- [ ] **Step 5: Run tests and build**

```powershell
npm run test:unit -- --run src/components/knowledge/DocumentUpload.test.ts
npm run type-check
npm run build
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add frontend/src/api/knowledge.ts frontend/src/stores/knowledge.ts frontend/src/views/KnowledgeView.vue frontend/src/components/knowledge
git commit -m "feat(frontend): add knowledge library"
```

### Task 7: Render Review Results And Safe Downloads

**Files:**
- Create: `frontend/src/components/tasks/TaskResult.vue`
- Create: `frontend/src/components/tasks/TaskResult.test.ts`
- Modify: `frontend/src/views/TaskDetailView.vue`

- [ ] **Step 1: Write result tests**

Assert final Markdown is displayed as text or sanitized rendered Markdown, bibliography links allow only HTTP/HTTPS, quality scores have labels, issues are listed, and download commands use server artifact IDs.

- [ ] **Step 2: Run test and verify component failure**

```powershell
npm run test:unit -- --run src/components/tasks/TaskResult.test.ts
```

Expected: FAIL.

- [ ] **Step 3: Implement result view**

Use a document column and a restrained metadata sidebar, not nested cards. Provide Download Markdown and Download DOCX commands with download icons. Use Axios `responseType: 'blob'`, server-provided filename, and revoke object URLs after click.

- [ ] **Step 4: Run tests and build**

```powershell
npm run test:unit -- --run src/components/tasks/TaskResult.test.ts
npm run type-check
npm run build
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add frontend/src/components/tasks/TaskResult.vue frontend/src/components/tasks/TaskResult.test.ts frontend/src/views/TaskDetailView.vue
git commit -m "feat(frontend): add review result export"
```

### Task 8: Add Browser-Level Workflow Acceptance

**Files:**
- Create: `frontend/playwright.config.ts`
- Create: `frontend/e2e/literature-review.spec.ts`
- Modify: `frontend/package.json`, `frontend/package-lock.json`

- [ ] **Step 1: Configure Playwright against Compose**

Use `baseURL=http://127.0.0.1`, one Chromium project, trace on first retry, screenshot on failure, and no automatic dependency installation in CI.

- [ ] **Step 2: Write the complete user workflow**

The test registers a unique user, uploads a small TXT fixture, creates an offline task, waits for plan approval, approves, observes at least one DAG status change, waits for completion, verifies citations, and downloads both artifact types.

- [ ] **Step 3: Add refresh and reconnect coverage**

Refresh during RUNNING and assert the same task/progress reappears. Block the WebSocket route, assert polling updates status, then unblock and assert no duplicate events.

- [ ] **Step 4: Run locally**

```powershell
docker compose up -d --build
Set-Location frontend
npx playwright install chromium
npm run test:e2e
```

Expected: all Playwright tests PASS; downloaded files are non-empty.

- [ ] **Step 5: Run full frontend verification**

```powershell
npm run test:unit -- --run
npm run type-check
npm run build
npm run test:e2e
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add frontend/package.json frontend/package-lock.json frontend/playwright.config.ts frontend/e2e
git commit -m "test(frontend): cover literature review workflow"
```

## Plan 4 Exit Gate

Run the Playwright workflow at desktop and mobile viewport. Capture screenshots of task list, plan approval, running DAG, human action and final result. Reject the milestone if text overlaps, node state changes resize the DAG, refresh loses state, or operation requires Swagger/terminal access.
