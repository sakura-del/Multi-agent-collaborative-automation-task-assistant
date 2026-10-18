import { ref } from "vue";
import { defineStore } from "pinia";

import { taskApi } from "@/api/tasks";
import type {
  TaskControl,
  TaskCreate,
  TaskCreateOut,
  TaskEvent,
  TaskSnapshot,
  TaskSummary,
} from "@/api/types";

/** 轮询间隔：WebSocket 由 Plan 4 Task 5 接入，此处先保证状态可推进 */
const POLL_INTERVAL_MS = 2000;

// 任务状态唯一归属：视图只读 store，不直接调用 Axios
export const useTaskStore = defineStore("tasks", () => {
  const items = ref<TaskSummary[]>([]);
  const snapshot = ref<TaskSnapshot | null>(null);
  const events = ref<TaskEvent[]>([]);
  const lastEventId = ref(0);
  const watching = ref(false);
  const listLoading = ref(false);
  const detailLoading = ref(false);

  let timer: ReturnType<typeof setInterval> | null = null;
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

  /** 合并单个事件：仅接受严格递增的 event_id，保证乱序/重放安全 */
  function applyEvent(event: TaskEvent): void {
    if (event.event_id <= lastEventId.value) {
      return;
    }
    lastEventId.value = event.event_id;
    events.value = [...events.value, event];
    if (snapshot.value && event.task_id === snapshot.value.task_id) {
      snapshot.value = { ...snapshot.value, status: event.status as TaskSnapshot["status"] };
    }
  }

  /** 拉取 after 之后的事件并逐个合并 */
  async function pullEvents(taskId: string): Promise<void> {
    const page = await taskApi.events(taskId, lastEventId.value);
    for (const event of page.events) {
      applyEvent(event);
    }
  }

  function watch(taskId: string): void {
    stopWatching();
    watching.value = true;
    watchedTaskId = taskId;
    void (async () => {
      await load(taskId);
      await pullEvents(taskId);
    })();
    timer = setInterval(() => {
      void pullEvents(taskId).catch(() => undefined);
    }, POLL_INTERVAL_MS);
  }

  function stopWatching(): void {
    if (timer !== null) {
      clearInterval(timer);
      timer = null;
    }
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
