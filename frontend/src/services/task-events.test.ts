import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createTaskEventSession } from "@/services/task-events";
import type { TaskEvent, TaskEventPage, WsTicket } from "@/api/types";

/** 极简 WebSocket 替身：记录实例、支持服务端帧与断开 */
class FakeWebSocket {
  static instances: FakeWebSocket[] = [];

  url: string;
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  emit(frame: unknown) {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }

  close() {
    this.closed = true;
    this.readyState = 3;
  }

  serverClose() {
    this.readyState = 3;
    this.onclose?.();
  }
}

function event(eventId: number): TaskEvent {
  return {
    event_id: eventId,
    task_id: "task-1",
    subtask_id: null,
    event_type: "subtask_state_transition",
    status: "running",
    occurred_at: "2026-10-18T02:00:00Z",
    payload: {},
  };
}

function page(ids: number[], next?: number): TaskEventPage {
  const events = ids.map(event);
  return { events, next_event_id: next ?? (ids.length ? ids[ids.length - 1] : 0) };
}

function buildDeps(overrides: Record<string, unknown> = {}) {
  const order: string[] = [];
  const deps = {
    loadSnapshot: vi.fn(async () => {
      order.push("snapshot");
    }),
    wsTicket: vi.fn(async (): Promise<WsTicket> => {
      order.push("ticket");
      return { ticket: `ticket-${order.length}`, expires_in: 60 };
    }),
    replay: vi.fn(async (_taskId: string, _after: number) => page([])),
    WebSocketImpl: FakeWebSocket as unknown as typeof WebSocket,
    isSecure: () => false,
    baseDelayMs: 1000,
    pollIntervalMs: 3000,
    maxFailures: 3,
    ...overrides,
  };
  return { deps, order };
}

describe("task event session", () => {
  beforeEach(() => {
    FakeWebSocket.instances = [];
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("loads the snapshot and acquires a ticket before connecting", async () => {
    const { deps, order } = buildDeps();
    const session = createTaskEventSession(deps);
    const onEvent = vi.fn();

    await session.start("task-1", 0, onEvent);

    expect(order).toEqual(["snapshot", "ticket"]);
    const socket = FakeWebSocket.instances[0];
    expect(socket.url).toContain("/api/v1/tasks/task-1/ws");
    expect(socket.url).toContain("ticket=ticket-2");
    expect(socket.url).toContain("after_event_id=0");
    session.stop();
  });

  it("replays missed events after the last seen id before connecting", async () => {
    const replay = vi.fn(async (_taskId: string, after: number) =>
      after === 0 ? page([1, 2]) : page([])
    );
    const { deps } = buildDeps({ replay });
    const session = createTaskEventSession(deps);
    const onEvent = vi.fn();

    await session.start("task-1", 0, onEvent);
    expect(onEvent).toHaveBeenCalledTimes(2);

    // 断开后重连：先用 lastEventId 补拉，再带上 after_event_id
    FakeWebSocket.instances[0].serverClose();
    await vi.advanceTimersByTimeAsync(1000);

    expect(replay).toHaveBeenLastCalledWith("task-1", 2);
    const socket = FakeWebSocket.instances[1];
    expect(socket.url).toContain("after_event_id=2");
    session.stop();
  });

  it("ignores duplicate event ids", async () => {
    const { deps } = buildDeps();
    const session = createTaskEventSession(deps);
    const onEvent = vi.fn();

    await session.start("task-1", 0, onEvent);
    const socket = FakeWebSocket.instances[0];
    socket.emit({ type: "event", data: event(5) });
    socket.emit({ type: "event", data: event(5) });
    socket.emit({ type: "event", data: event(3) });
    socket.emit({ type: "event", data: event(6) });

    expect(onEvent.mock.calls.map((call) => call[0].event_id)).toEqual([5, 6]);
    session.stop();
  });

  it("forwards snapshot frames to the snapshot handler", async () => {
    const { deps } = buildDeps();
    const session = createTaskEventSession(deps);
    const onSnapshot = vi.fn();

    await session.start("task-1", 0, vi.fn(), { onSnapshot });
    FakeWebSocket.instances[0].emit({ type: "snapshot", data: { task_id: "task-1", status: "running" } });

    expect(onSnapshot).toHaveBeenCalledWith({ task_id: "task-1", status: "running" });
    session.stop();
  });

  it("reconnects with 1s, 3s and 9s backoff", async () => {
    const { deps } = buildDeps();
    const session = createTaskEventSession(deps);
    await session.start("task-1", 0, vi.fn());

    FakeWebSocket.instances[0].serverClose();
    await vi.advanceTimersByTimeAsync(999);
    expect(FakeWebSocket.instances).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(FakeWebSocket.instances).toHaveLength(2);

    FakeWebSocket.instances[1].serverClose();
    await vi.advanceTimersByTimeAsync(2999);
    expect(FakeWebSocket.instances).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(1);
    expect(FakeWebSocket.instances).toHaveLength(3);

    FakeWebSocket.instances[2].serverClose();
    await vi.advanceTimersByTimeAsync(9000);
    expect(FakeWebSocket.instances).toHaveLength(4);
    session.stop();
  });

  it("switches to 3s polling after three failed reconnects", async () => {
    const replay = vi.fn(async () => page([]));
    const { deps } = buildDeps({ replay });
    const session = createTaskEventSession(deps);
    await session.start("task-1", 0, vi.fn());
    const replayCallsAfterStart = replay.mock.calls.length;

    // 初次 + 三次重连全部失败
    FakeWebSocket.instances[0].serverClose();
    await vi.advanceTimersByTimeAsync(1000);
    FakeWebSocket.instances[1].serverClose();
    await vi.advanceTimersByTimeAsync(3000);
    FakeWebSocket.instances[2].serverClose();
    await vi.advanceTimersByTimeAsync(9000);
    FakeWebSocket.instances[3].serverClose();
    await vi.advanceTimersByTimeAsync(1);

    await vi.advanceTimersByTimeAsync(3000);
    const afterFirstPoll = replay.mock.calls.length;
    expect(afterFirstPoll).toBeGreaterThan(replayCallsAfterStart + 1);
    await vi.advanceTimersByTimeAsync(3000);
    expect(replay.mock.calls.length).toBeGreaterThan(afterFirstPoll);
    session.stop();
  });

  it("cleans up timers and sockets on stop", async () => {
    const replay = vi.fn(async () => page([]));
    const { deps } = buildDeps({ replay });
    const session = createTaskEventSession(deps);
    await session.start("task-1", 0, vi.fn());
    const socket = FakeWebSocket.instances[0];

    socket.serverClose();
    session.stop();
    const callsAfterStop = replay.mock.calls.length;
    await vi.advanceTimersByTimeAsync(20000);

    expect(socket.closed).toBe(true);
    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(replay.mock.calls.length).toBe(callsAfterStop);
  });

  it("uses wss on secure pages", async () => {
    const { deps } = buildDeps({ isSecure: () => true });
    const session = createTaskEventSession(deps);

    await session.start("task-1", 0, vi.fn());

    expect(FakeWebSocket.instances[0].url.startsWith("wss://")).toBe(true);
    session.stop();
  });
});
