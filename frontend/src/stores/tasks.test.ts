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

function snapshot(overrides: Partial<TaskSnapshot> = {}): TaskSnapshot {
  return {
    task_id: "task-1",
    status: "waiting_approval",
    title: "文献综述",
    plan_version: 1,
    revision: 3,
    plan: null,
    subtasks: [],
    artifacts: [],
    review_report: null,
    created_at: "2026-10-18T01:00:00Z",
    updated_at: "2026-10-18T01:00:00Z",
    ...overrides,
  };
}

function event(eventId: number, status: string): TaskEvent {
  return {
    event_id: eventId,
    task_id: "task-1",
    subtask_id: null,
    event_type: "subtask_state_transition",
    status,
    occurred_at: "2026-10-18T01:00:01Z",
    payload: {},
  };
}

describe("task store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("replaces a stale snapshot on load", async () => {
    const store = useTaskStore();
    store.snapshot = snapshot({ status: "created", revision: 1 });
    vi.mocked(taskApi.get).mockResolvedValue(snapshot({ status: "running", revision: 5 }));

    await store.load("task-1");

    expect(store.snapshot?.status).toBe("running");
    expect(store.snapshot?.revision).toBe(5);
  });

  it("ignores an event whose event_id is not newer", async () => {
    const store = useTaskStore();
    store.snapshot = snapshot();
    store.applyEvent(event(7, "running"));
    const staleEvents = store.events.length;

    store.applyEvent(event(7, "failed"));
    store.applyEvent(event(3, "failed"));

    expect(store.lastEventId).toBe(7);
    expect(store.snapshot.status).toBe("running");
    expect(store.events).toHaveLength(staleEvents);
  });

  it("applies a newer event to status and lastEventId", () => {
    const store = useTaskStore();
    store.snapshot = snapshot({ status: "waiting_approval" });

    store.applyEvent(event(9, "running"));

    expect(store.lastEventId).toBe(9);
    expect(store.snapshot?.status).toBe("running");
    expect(store.events).toHaveLength(1);
  });

  it("creates a task through the API adapter", async () => {
    const store = useTaskStore();
    vi.mocked(taskApi.create).mockResolvedValue({ task_id: "task-9", status: "created" });
    const payload: TaskCreate = { topic: "检索增强生成综述", keywords: ["RAG"] };

    const created = await store.create(payload);

    expect(taskApi.create).toHaveBeenCalledWith(payload);
    expect(created.task_id).toBe("task-9");
  });

  it("approves the plan with the current revision", async () => {
    const store = useTaskStore();
    store.snapshot = snapshot({ revision: 4 });
    vi.mocked(taskApi.approve).mockResolvedValue(snapshot({ status: "running", revision: 4 }));

    await store.approve("task-1");

    expect(taskApi.approve).toHaveBeenCalledWith("task-1", 4);
    expect(store.snapshot?.status).toBe("running");
  });

  it("polls for new events while watching and stops on stopWatching", async () => {
    const store = useTaskStore();
    vi.mocked(taskApi.get).mockResolvedValue(snapshot());
    vi.mocked(taskApi.events).mockResolvedValue({ events: [], next_event_id: 0 });

    store.watch("task-1");
    await vi.advanceTimersByTimeAsync(0); // 首次立即拉取
    const initialCalls = vi.mocked(taskApi.events).mock.calls.length;

    await vi.advanceTimersByTimeAsync(4000);
    const pollingCalls = vi.mocked(taskApi.events).mock.calls.length;
    expect(pollingCalls).toBeGreaterThan(initialCalls);

    store.stopWatching();
    expect(store.watching).toBe(false);
    await vi.advanceTimersByTimeAsync(8000);
    expect(vi.mocked(taskApi.events).mock.calls.length).toBe(pollingCalls);
  });

  it("sends control commands and stores the returned snapshot", async () => {
    const store = useTaskStore();
    store.snapshot = snapshot({ status: "running" });
    vi.mocked(taskApi.control).mockResolvedValue(snapshot({ status: "paused" }));

    await store.control("task-1", { command: "pause" });

    expect(taskApi.control).toHaveBeenCalledWith("task-1", { command: "pause" });
    expect(store.snapshot?.status).toBe("paused");
  });

  it("loads the task list", async () => {
    const store = useTaskStore();
    vi.mocked(taskApi.list).mockResolvedValue([
      {
        task_id: "task-1",
        status: "completed",
        title: "文献综述",
        plan_version: 1,
        revision: 9,
        created_at: null,
        updated_at: null,
      },
    ]);

    await store.loadList();

    expect(store.items).toHaveLength(1);
    expect(store.items[0].status).toBe("completed");
  });
});
