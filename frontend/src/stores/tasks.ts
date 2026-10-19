import { ref } from "vue";
import { defineStore } from "pinia";

import { taskApi } from "@/api/tasks";
import { createTaskEventSession, type TaskEventSession } from "@/services/task-events";
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
  const listLoading = ref(false);
  const detailLoading = ref(false);

  let session: TaskEventSession | null = null;
  let watchedTaskId: string | null = null;

  async function loadList(): Promise<void> {
    listLoading.value = true;
    try {
      items.value = await taskApi.list();
    } finally {
      listLoading.value = false;
    }
  }

  async function create(payload: TaskCreate): Promise<TaskCreateOut> {
    return taskApi.create(payload);
  }

  /** 加载快照：整体替换，避免旧状态残留 */
  async function load(taskId: string): Promise<TaskSnapshot> {
    detailLoading.value = true;
    try {
      const fresh = await taskApi.get(taskId);
      snapshot.value = fresh;
      if (watchedTaskId !== taskId) {
        events.value = [];
        lastEventId.value = 0;
      }
      return fresh;
    } finally {
      detailLoading.value = false;
    }
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
      })
      .catch(() => undefined);
  }

  function stopWatching(): void {
    session?.stop();
    session = null;
    watching.value = false;
    watchedTaskId = null;
  }

  return {
    items,
    snapshot,
    events,
    lastEventId,
    watching,
    listLoading,
    detailLoading,
    loadList,
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
