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
    vi.unstubAllEnvs();
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

  it("未配置 VITE_WS_BASE_URL 时仍连当前页面 origin（同源部署零回归）", async () => {
    const { deps } = buildDeps();
    const session = createTaskEventSession(deps);

    await session.start("task-1", 0, vi.fn());

    const socket = FakeWebSocket.instances[0];
    expect(socket.url.startsWith(`ws://${window.location.host}/api/v1/tasks/task-1/ws`)).toBe(true);
    session.stop();
  });

  it("配置 VITE_WS_BASE_URL 时连到该 origin（Cloudflare Pages 跨域部署）", async () => {
    vi.stubEnv("VITE_WS_BASE_URL", "wss://api.example.com");
    const { deps } = buildDeps();
    const session = createTaskEventSession(deps);

    await session.start("task-1", 0, vi.fn());

    const socket = FakeWebSocket.instances[0];
    expect(socket.url.startsWith("wss://api.example.com/api/v1/tasks/task-1/ws")).toBe(true);
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

  it("emits onConnectionChange transitions across connected, reconnecting and polling", async () => {
    const { deps } = buildDeps();
    const session = createTaskEventSession(deps);
    const onConnectionChange = vi.fn();

    await session.start("task-1", 0, vi.fn(), { onConnectionChange });
    FakeWebSocket.instances[0].onopen?.();
    expect(onConnectionChange).toHaveBeenLastCalledWith("connected");

    // 第 1 次断线重连
    FakeWebSocket.instances[0].serverClose();
    expect(onConnectionChange).toHaveBeenLastCalledWith("reconnecting", 1);

    await vi.advanceTimersByTimeAsync(1000);
    FakeWebSocket.instances[1].serverClose();
    expect(onConnectionChange).toHaveBeenLastCalledWith("reconnecting", 2);

    await vi.advanceTimersByTimeAsync(3000);
    FakeWebSocket.instances[2].serverClose();
    expect(onConnectionChange).toHaveBeenLastCalledWith("reconnecting", 3);

    await vi.advanceTimersByTimeAsync(9000);
    // 第 4 次失败超过 maxFailures=3，降级为 polling
    FakeWebSocket.instances[3].serverClose();
    expect(onConnectionChange).toHaveBeenLastCalledWith("polling");

    // stop() 后清零 handlers，后续残留关闭不得再触发回调
    const callsBeforeStop = onConnectionChange.mock.calls.length;
    session.stop();
    FakeWebSocket.instances[3].onclose?.();
    expect(onConnectionChange.mock.calls.length).toBe(callsBeforeStop);
  });

  it("首连 connect() 失败时立刻进入重连调度并在连续失败后降级为 polling（C9）", async () => {
    const wsTicket = vi.fn().mockRejectedValue(new Error("网络不可达"));
    const replay = vi.fn(async () => page([]));
    const { deps } = buildDeps({ wsTicket, replay });
    const session = createTaskEventSession(deps);
    const onConnectionChange = vi.fn();

    await session.start("task-1", 0, vi.fn(), { onConnectionChange });
    expect(onConnectionChange).toHaveBeenLastCalledWith("reconnecting", 1);

    await vi.advanceTimersByTimeAsync(1000);
    expect(onConnectionChange).toHaveBeenLastCalledWith("reconnecting", 2);

    await vi.advanceTimersByTimeAsync(3000);
    expect(onConnectionChange).toHaveBeenLastCalledWith("reconnecting", 3);

    await vi.advanceTimersByTimeAsync(9000);
    expect(onConnectionChange).toHaveBeenLastCalledWith("polling");

    const callsBeforePoll = replay.mock.calls.length;
    await vi.advanceTimersByTimeAsync(3000);
    expect(replay.mock.calls.length).toBeGreaterThan(callsBeforePoll);
    session.stop();
  });

  it("首连 connect() 失败一次后重连成功并恢复 connected（C9）", async () => {
    let attempts = 0;
    const wsTicket = vi.fn(async (): Promise<WsTicket> => {
      attempts += 1;
      if (attempts === 1) {
        throw new Error("瞬时抖动");
      }
      return { ticket: "ticket-ok", expires_in: 60 };
    });
    const { deps } = buildDeps({ wsTicket });
    const session = createTaskEventSession(deps);
    const onConnectionChange = vi.fn();

    await session.start("task-1", 0, vi.fn(), { onConnectionChange });
    expect(onConnectionChange).toHaveBeenLastCalledWith("reconnecting", 1);

    await vi.advanceTimersByTimeAsync(1000);
    expect(FakeWebSocket.instances).toHaveLength(1);
    FakeWebSocket.instances[0].onopen?.();
    expect(onConnectionChange).toHaveBeenLastCalledWith("connected");
    session.stop();
  });

  it("stop() 后切到新任务时，旧会话延迟 reject 不得触发重连或污染新任务状态（C9）", async () => {
    let rejectTaskA!: (err: Error) => void;
    const wsTicket = vi.fn((tid: string) => {
      if (tid === "task-A") {
        return new Promise<WsTicket>((_resolve, reject) => {
          rejectTaskA = reject;
        });
      }
      return Promise.resolve({ ticket: "tk-task-B", expires_in: 60 });
    });

    const { deps } = buildDeps({ wsTicket });
    const session = createTaskEventSession(deps);
    const onChangeA = vi.fn();
    const onChangeB = vi.fn();

    const startAPromise = session.start("task-A", 0, vi.fn(), { onConnectionChange: onChangeA });
    await vi.waitFor(() => expect(wsTicket).toHaveBeenCalledWith("task-A"));

    session.stop();
    await session.start("task-B", 0, vi.fn(), { onConnectionChange: onChangeB });
    expect(FakeWebSocket.instances).toHaveLength(1);
    FakeWebSocket.instances[0].onopen?.();
    expect(onChangeB).toHaveBeenLastCalledWith("connected");

    rejectTaskA(new Error("task-A 过期失败"));
    await startAPromise;
    await Promise.resolve();

    expect(onChangeA).not.toHaveBeenCalled();
    expect(onChangeB).toHaveBeenCalledTimes(1);
    expect(onChangeB).toHaveBeenLastCalledWith("connected");
    expect(FakeWebSocket.instances).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(5000);
    expect(FakeWebSocket.instances).toHaveLength(1);
    session.stop();
  });

  it("stop() 后切到新任务时，旧会话延迟 resolve 不得创建游离 WebSocket 或串入旧事件（C9）", async () => {
    let resolveTicketA!: (ticket: WsTicket) => void;
    let resolveReplayA!: (p: TaskEventPage) => void;
    const replay = vi.fn((tid: string) => {
      if (tid === "task-A") {
        return new Promise<TaskEventPage>((resolve) => {
          resolveReplayA = resolve;
        });
      }
      return Promise.resolve(page([]));
    });
    const wsTicket = vi.fn((tid: string) => {
      if (tid === "task-A") {
        return new Promise<WsTicket>((resolve) => {
          resolveTicketA = resolve;
        });
      }
      return Promise.resolve({ ticket: "tk-task-B", expires_in: 60 });
    });

    const { deps } = buildDeps({ replay, wsTicket });
    const session = createTaskEventSession(deps);
    const onEventA = vi.fn();
    const onEventB = vi.fn();
    const onChangeB = vi.fn();

    // 1. 旧会话在 replay 阶段挂起时切到 task-B，随后旧 replay 返回也不得串入 task-B
    const startA1 = session.start("task-A", 0, onEventA);
    await vi.waitFor(() => expect(replay).toHaveBeenCalledWith("task-A", 0));
    session.stop();

    await session.start("task-B", 0, onEventB, { onConnectionChange: onChangeB });
    FakeWebSocket.instances[0].onopen?.();
    resolveReplayA(page([99]));
    await startA1;
    expect(onEventA).not.toHaveBeenCalled();
    expect(onEventB).not.toHaveBeenCalled();
    expect(FakeWebSocket.instances).toHaveLength(1);

    // 2. 旧会话在 wsTicket 阶段挂起时切到 task-B，随后旧 ticket 返回不得新建第 2 条 WebSocket
    session.stop();
    FakeWebSocket.instances = [];
    const replayImmediate = vi.fn(async () => page([]));
    const { deps: deps2 } = buildDeps({ replay: replayImmediate, wsTicket });
    const session2 = createTaskEventSession(deps2);

    const startA2 = session2.start("task-A", 0, vi.fn());
    await vi.waitFor(() => expect(wsTicket).toHaveBeenLastCalledWith("task-A"));
    session2.stop();

    const onChangeB2 = vi.fn();
    await session2.start("task-B", 0, vi.fn(), { onConnectionChange: onChangeB2 });
    expect(FakeWebSocket.instances).toHaveLength(1);
    FakeWebSocket.instances[0].onopen?.();

    resolveTicketA({ ticket: "tk-stale-A", expires_in: 60 });
    await startA2;

    expect(FakeWebSocket.instances).toHaveLength(1);
    expect(FakeWebSocket.instances[0].url).toContain("/tasks/task-B/ws");
    expect(FakeWebSocket.instances[0].url).not.toContain("tk-stale-A");
    expect(onChangeB2).toHaveBeenCalledTimes(1);
    session2.stop();
  });
});
