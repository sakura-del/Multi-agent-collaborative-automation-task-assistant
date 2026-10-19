/**
 * Issue #2 回归：规划就绪后详情页必须**自动**出现规划，无需手动刷新。
 *
 * 根因（Issue #2 已定位）：`tasks.ts::applyEvent` 的刷新条件是
 * `nextStatus !== previousStatus && REFRESH_ON_STATUS.has(nextStatus)`——只在状态变化时重拉快照。
 * 生产竞态：
 *   1. 页面加载 → `load()` 拉到的快照 `status` 已是 `waiting_approval`，但 `plan` 仍是 `null`；
 *   2. 随后回放/推送到达 `plan_ready`（status=waiting_approval）→ `nextStatus === previousStatus`
 *      → 刷新被跳过；
 *   3. 界面永远停在 `plan === null` 的旧快照 → 只能手动 F5。
 *
 * 本文件锁定三条性质：
 *   1. `plan_ready` 事件**无条件**重拉快照（不看状态是否变化）；
 *   2. 快照 `status=waiting_approval` 但 `plan === null` 时，任何到达的事件都应触发重拉；
 *   3. 既有去重与「状态变化才刷新」行为**不回归**。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import { taskApi } from "@/api/tasks";
import type { TaskCreate, TaskEvent, TaskSnapshot } from "@/api/types";
import { useTaskStore } from "@/stores/tasks";

vi.mock("@/api/tasks", () => ({
  taskApi: {
    list: vi.fn(),
    create: vi.fn(),
    get: vi.fn(),
    approve: vi.fn(),
    control: vi.fn(),
    events: vi.fn(),
    wsTicket: vi.fn(),
  },
}));

const startSpy = vi.fn(
  async (
    _taskId: string,
    _after: number,
    _onEvent: (event: TaskEvent) => void,
    _handlers?: { onSnapshot?: (snapshot: unknown) => void }
  ) => undefined
);
const stopSpy = vi.fn();
vi.mock("@/services/task-events", () => ({
  createTaskEventSession: () => ({ start: startSpy, stop: stopSpy }),
}));

const PLAN = {
  version: 1,
  topic: "检索增强生成的评估方法",
  research_questions: ["RAG 如何评估"],
  search_queries: ["RAG evaluation"],
  acceptance_criteria: ["引用可追溯"],
  nodes: [
    { id: "n1", type: "search" as const, title: "检索", dependencies: [], input: {}, required_evidence: 1 },
  ],
};

function snapshot(overrides: Partial<TaskSnapshot> = {}): TaskSnapshot {
  return {
    task_id: "task-1",
    status: "planning",
    title: "文献综述",
    plan_version: 0,
    revision: 0,
    plan: null,
    subtasks: [],
    artifacts: [],
    review_report: null,
    created_at: "2026-10-18T01:00:00Z",
    updated_at: "2026-10-18T01:00:00Z",
    ...overrides,
  };
}

function event(
  eventId: number,
  status: string,
  eventType = "task_status"
): TaskEvent {
  return {
    event_id: eventId,
    task_id: "task-1",
    subtask_id: null,
    event_type: eventType,
    status,
    occurred_at: "2026-10-18T01:00:01Z",
    payload: {},
  };
}

/** 让 load() 的 promise 链跑完（fake timers 下需要 flush 微任务）。 */
async function flush(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
}

describe("Issue #2：规划就绪后自动展示", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.useFakeTimers();
    vi.mocked(taskApi.get).mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("plan_ready 事件无条件重拉快照（即使状态未变）", async () => {
    const store = useTaskStore();
    // 生产竞态：状态已是 waiting_approval，但 plan 仍为 null
    store.snapshot = snapshot({ status: "waiting_approval", revision: 1, plan: null });
    vi.mocked(taskApi.get).mockResolvedValue(
      snapshot({ status: "waiting_approval", revision: 1, plan: PLAN })
    );

    store.applyEvent(event(2, "waiting_approval", "plan_ready"));
    await flush();

    expect(taskApi.get).toHaveBeenCalledWith("task-1");
    expect(store.snapshot?.plan).not.toBeNull();
  });

  it("快照 plan 为空时，非 plan_ready 事件也会触发重拉", async () => {
    const store = useTaskStore();
    store.snapshot = snapshot({ status: "waiting_approval", revision: 1, plan: null });
    vi.mocked(taskApi.get).mockResolvedValue(
      snapshot({ status: "waiting_approval", revision: 1, plan: PLAN })
    );

    // 状态等值，且事件类型不是 plan_ready —— 但规划内容缺失，必须补拉
    store.applyEvent(event(5, "waiting_approval", "node_status"));
    await flush();

    expect(taskApi.get).toHaveBeenCalledTimes(1);
    expect(store.snapshot?.plan).not.toBeNull();
  });

  it("守卫：状态未变、plan 已有、且非 plan_ready 时不重拉", async () => {
    const store = useTaskStore();
    store.snapshot = snapshot({ status: "running", revision: 2, plan: PLAN });
    vi.mocked(taskApi.get).mockResolvedValue(snapshot({ status: "running", plan: PLAN }));

    store.applyEvent(event(6, "running", "node_status"));
    await flush();

    expect(taskApi.get).not.toHaveBeenCalled();
  });

  it("守卫：状态变化到 waiting_approval 仍然重拉（既有行为不回归）", async () => {
    const store = useTaskStore();
    store.snapshot = snapshot({ status: "planning", revision: 0, plan: null });
    vi.mocked(taskApi.get).mockResolvedValue(
      snapshot({ status: "waiting_approval", revision: 1, plan: PLAN })
    );

    store.applyEvent(event(2, "waiting_approval", "plan_ready"));
    await flush();

    expect(taskApi.get).toHaveBeenCalledTimes(1);
  });

  it("守卫：重复 event_id 仍然被忽略（不重复拉快照）", async () => {
    const store = useTaskStore();
    store.snapshot = snapshot({ status: "waiting_approval", plan: null });
    store.lastEventId = 7;

    store.applyEvent(event(7, "waiting_approval", "plan_ready"));
    await flush();

    expect(taskApi.get).not.toHaveBeenCalled();
  });

  it("重拉失败不抛出、不破坏已有快照", async () => {
    const store = useTaskStore();
    store.snapshot = snapshot({ status: "waiting_approval", revision: 1, plan: null });
    vi.mocked(taskApi.get).mockRejectedValue(new Error("network down"));

    store.applyEvent(event(3, "waiting_approval", "plan_ready"));
    await flush();

    expect(store.snapshot?.status).toBe("waiting_approval");
    expect(store.lastEventId).toBe(3);
  });
});
