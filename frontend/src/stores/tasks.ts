import { ref } from "vue";
import { defineStore } from "pinia";

import { taskApi } from "@/api/tasks";
import {
  createTaskEventSession,
  type TaskEventConnectionState,
  type TaskEventSession,
} from "@/services/task-events";
import type { TaskCounts, TaskStatusGroup } from "@/api/tasks";
import type {
  TaskControl,
  TaskCreate,
  TaskCreateOut,
  TaskEvent,
  TaskSnapshot,
  TaskSummary,
} from "@/api/types";

// 任务状态唯一归属：视图只读 store，不直接调用 Axios。
// 实时事件走 WebSocket 会话（Task 5），失败自动降级为 HTTP 补拉轮询。
export const useTaskStore = defineStore("tasks", () => {
  const items = ref<TaskSummary[]>([]);
  const snapshot = ref<TaskSnapshot | null>(null);
  const events = ref<TaskEvent[]>([]);
  const lastEventId = ref(0);
  const watching = ref(false);
  const connectionState = ref<TaskEventConnectionState>("connected");
  const listLoading = ref(false);
  /** 服务端分页：每页条数与后端默认 limit 保持一致。 */
  const PAGE_SIZE = 20;
  const listTotal = ref(0);
  const listPage = ref(1);
  /** 当前生效的筛选与搜索（服务端侧，分页必须带上，否则会跨页串数据）。 */
  const listGroup = ref<TaskStatusGroup>("all");
  const listQuery = ref("");
  const counts = ref<TaskCounts>({ all: 0, needs_me: 0, active: 0, done: 0 });
  const detailLoading = ref(false);

  let session: TaskEventSession | null = null;
  let watchedTaskId: string | null = null;
  const inFlightList = new Map<string, Promise<void>>();
  const inFlightCounts = new Map<string, Promise<void>>();
  const inFlightDetail = new Map<string, Promise<TaskSnapshot>>();

  /**
   * 加载指定页（从 1 开始，相同筛选参数在飞期间自动去重合并）。
   *
   * 默认第 1 页：创建任务、从详情返回等既有调用点都不传参，行为不变。
   * 分页必须走服务端 skip/limit —— 后端默认只回 20 条，前端切片切不出
   * 第 21 个任务。
   */
  async function loadList(
    page = 1,
    options?: { group?: TaskStatusGroup; query?: string },
  ): Promise<void> {
    const target = Math.max(1, Math.floor(page));
    const group = options?.group ?? listGroup.value;
    const query = (options?.query ?? listQuery.value).trim();
    const key = `${target}|${group}|${query}`;
    const existing = inFlightList.get(key);
    if (existing) return existing;

    listLoading.value = true;
    const job = (async () => {
      try {
        const result = await taskApi.list({
          skip: (target - 1) * PAGE_SIZE,
          limit: PAGE_SIZE,
          status_group: group,
          // 空串不发：后端把 q="" 也当过滤条件时会导致 SQL 里多一个 %% 匹配
          q: query || undefined,
        });
        items.value = result.items;
        listTotal.value = result.total;
        listPage.value = target;
        listGroup.value = group;
        listQuery.value = query;
      } finally {
        inFlightList.delete(key);
        listLoading.value = false;
      }
    })();
    inFlightList.set(key, job);
    return job;
  }

  /** 分组计数（不受分页影响；带当前搜索词，并发去重）。 */
  async function loadCounts(query = listQuery.value): Promise<void> {
    const clean = query.trim();
    const existing = inFlightCounts.get(clean);
    if (existing) return existing;

    const job = (async () => {
      try {
        counts.value = await taskApi.counts({ q: clean || undefined });
      } finally {
        inFlightCounts.delete(clean);
      }
    })();
    inFlightCounts.set(clean, job);
    return job;
  }

  async function create(payload: TaskCreate): Promise<TaskCreateOut> {
    return taskApi.create(payload);
  }

  /** 加载快照：切换任务时清掉旧快照避免误展示，同 taskId 并发去重 */
  async function load(taskId: string): Promise<TaskSnapshot> {
    const existing = inFlightDetail.get(taskId);
    if (existing) return existing;

    if (snapshot.value && snapshot.value.task_id !== taskId) {
      snapshot.value = null;
    }
    detailLoading.value = true;
    const job = (async () => {
      try {
        const fresh = await taskApi.get(taskId);
        snapshot.value = fresh;
        if (watchedTaskId !== taskId) {
          events.value = [];
          lastEventId.value = 0;
        }
        return fresh;
      } finally {
        inFlightDetail.delete(taskId);
        detailLoading.value = false;
      }
    })();
    inFlightDetail.set(taskId, job);
    return job;
  }

  /** 路由预取：静默发起请求并与页面 chunk 下载并行，组件挂载时通过 inFlight 复用 */
  function prefetchList(): void {
    void loadList(1, { group: "all", query: "" }).catch(() => undefined);
    void loadCounts("").catch(() => undefined);
  }

  function prefetchDetail(taskId: string): void {
    void load(taskId).catch(() => undefined);
  }

  async function approve(taskId: string, revision?: number): Promise<TaskSnapshot> {
    const target = revision ?? snapshot.value?.revision ?? 1;
    const updated = await taskApi.approve(taskId, target);
    snapshot.value = updated;
    return updated;
  }

  async function control(taskId: string, payload: TaskControl): Promise<TaskSnapshot> {
    const updated = await taskApi.control(taskId, payload);
    snapshot.value = updated;
    return updated;
  }

  /**
   * 合并单个事件：仅接受严格递增的 event_id，保证乱序/重放安全。
   * 事件只携带状态，像"规划生成完毕""复盘生成完毕"这类新字段需要重新拉快照，
   * 否则界面会停在旧快照上（E2E 实跑发现：确认规划按钮一直不出现）。
   */
  const REFRESH_ON_STATUS = new Set(["waiting_approval", "waiting_human", "completed", "failed"]);

  /** 事件名本身即代表"快照里有新内容"，必须重拉（Issue #2） */
  const REFRESH_ON_EVENT_TYPE = new Set(["plan_ready", "task_completed"]);

  /** 这些状态下界面应已能展示规划；若 `plan` 仍为空则说明快照过期（Issue #2） */
  const PLAN_VISIBLE_STATUS = new Set(["waiting_approval", "running", "reviewing", "completed"]);

  /**
   * 是否需要重新拉快照（Issue #2）。
   *
   * 原实现只判断「状态是否变化」，漏掉了生产上的竞态：快照的 `status` 已经是
   * `waiting_approval`，但 `plan` 仍是 `null`（快照与规划写入有时序差）；随后到达的
   * `plan_ready` 事件因 `nextStatus === previousStatus` 被跳过 → 界面永远停在
   * 「暂无规划」，只能手动刷新。
   *
   * 判据（任一成立即重拉）：
   * 1. 事件类型本身代表新内容（`plan_ready` / `task_completed`）——与状态无关；
   * 2. 状态发生变化且新状态值得刷新（既有行为）；
   * 3. 当前快照**缺少规划内容**（`plan === null`）而状态已要求展示规划
   *    （`waiting_approval` / `running` / `reviewing` / `completed`）——补拉兜底。
   */
  function needsSnapshotRefresh(
    previousStatus: TaskSnapshot["status"],
    nextStatus: TaskSnapshot["status"],
    eventType: string,
    current: TaskSnapshot
  ): boolean {
    if (REFRESH_ON_EVENT_TYPE.has(eventType)) {
      return true;
    }
    if (nextStatus !== previousStatus && REFRESH_ON_STATUS.has(nextStatus)) {
      return true;
    }
    return current.plan == null && PLAN_VISIBLE_STATUS.has(nextStatus);
  }

  /** 不可逆终态：历史事件回放不得将已完结任务状态倒退回早期阶段，也不应触发重复拉取（Issue #55） */
  const IRREVERSIBLE_TERMINAL_STATUS = new Set<TaskSnapshot["status"]>(["completed", "cancelled"]);

  /** 合法的任务级状态（子任务独有状态如 validating/ready/pending 不得覆写任务级 status，Issue #59） */
  const TASK_LEVEL_STATUSES = new Set<string>([
    "created",
    "planning",
    "waiting_approval",
    "running",
    "reviewing",
    "paused",
    "retrying",
    "waiting_human",
    "completed",
    "failed",
    "cancelled",
  ]);

  function applyEvent(event: TaskEvent): void {
    if (event.event_id <= lastEventId.value) {
      return;
    }
    lastEventId.value = event.event_id;
    events.value = [...events.value, event];
    if (!snapshot.value || event.task_id !== snapshot.value.task_id) {
      return;
    }
    const previousStatus = snapshot.value.status;
    if (IRREVERSIBLE_TERMINAL_STATUS.has(previousStatus)) {
      // 已完成/已取消任务的快照已是最新的终态；回放创建/规划/执行历史事件时只记日志，
      // 避免把页面状态短暂闪烁回「待确认/执行中」并反复触发 GET /tasks/:id
      if (
        previousStatus === "completed" &&
        (snapshot.value.plan == null || snapshot.value.review_report == null) &&
        event.event_type === "task_completed"
      ) {
        void load(snapshot.value.task_id).catch(() => undefined);
      }
      return;
    }

    // 子任务级事件（带 subtask_id 或状态为 validating/ready/pending 等非任务级状态）：
    // 只同步更新对应子任务状态，绝不污染任务级 snapshot.status（避免右侧属性闪烁「校验中/执行中」，Issue #59）
    const isSubtaskOnlyEvent =
      event.subtask_id !== null || !TASK_LEVEL_STATUSES.has(event.status);
    if (isSubtaskOnlyEvent) {
      const nodeId = typeof event.payload?.node_id === "string" ? event.payload.node_id : null;
      if (nodeId) {
        const nextSubtasks = snapshot.value.subtasks.map((item) =>
          item.node_id === nodeId
            ? { ...item, status: event.status as TaskSnapshot["subtasks"][number]["status"] }
            : item,
        );
        snapshot.value = { ...snapshot.value, subtasks: nextSubtasks };
      }
      return;
    }

    const nextStatus = event.status as TaskSnapshot["status"];
    const merged = { ...snapshot.value, status: nextStatus };
    snapshot.value = merged;
    if (needsSnapshotRefresh(previousStatus, nextStatus, event.event_type, merged)) {
      const taskId = merged.task_id;
      void load(taskId).catch(() => undefined);
    }
  }

  /** 拉取 after 之后的事件并逐个合并 */
  async function pullEvents(taskId: string): Promise<void> {
    const page = await taskApi.events(taskId, lastEventId.value);
    for (const event of page.events) {
      applyEvent(event);
    }
  }

  /** 启动实时事件会话：票据 → 快照 → 补拉 → WebSocket；失败由服务内部降级轮询 */
  function watch(taskId: string): void {
    stopWatching();
    watching.value = true;
    watchedTaskId = taskId;
    session = createTaskEventSession({
      wsTicket: taskApi.wsTicket,
      replay: taskApi.events,
      loadSnapshot: (id) => load(id),
    });
    void session
      .start(taskId, lastEventId.value, (event) => applyEvent(event), {
        onSnapshot: (incoming) => {
          const next = incoming as TaskSnapshot | null;
          if (next && next.task_id === taskId) {
            snapshot.value = { ...(snapshot.value ?? next), ...next };
          }
        },
        onConnectionChange: (state) => {
          connectionState.value = state;
        },
      })
      .catch(() => undefined);
  }

  function stopWatching(): void {
    session?.stop();
    session = null;
    watching.value = false;
    watchedTaskId = null;
    connectionState.value = "connected";
  }

  return {
    items,
    snapshot,
    events,
    lastEventId,
    watching,
    connectionState,
    listLoading,
    detailLoading,
    PAGE_SIZE,
    listTotal,
    listPage,
    listGroup,
    listQuery,
    counts,
    loadList,
    loadCounts,
    prefetchList,
    prefetchDetail,
    create,
    load,
    approve,
    control,
    applyEvent,
    pullEvents,
    watch,
    stopWatching,
  };
});
