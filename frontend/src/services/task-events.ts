import type { TaskEvent, TaskEventPage, WsTicket } from "@/api/types";
import { API_PREFIX, resolveWsBase } from "@/config/endpoint";

// 任务事件会话：一次性票据 → 快照 → 补拉 → WebSocket 实时转发；
// 连续失败按 1s/3s/9s 退避重连，仍失败则降级为 3s HTTP 轮询补拉。

export interface TaskEventSessionDeps {
  /** 申请一次性连接票据（POST /ws-ticket，服务端会校验任务归属） */
  wsTicket: (taskId: string) => Promise<WsTicket>;
  /** 补拉 after 之后的事件（GET /tasks/{id}/events） */
  replay: (taskId: string, after: number) => Promise<TaskEventPage>;
  /** 连接前先加载快照，保证界面先有整体状态再吃增量 */
  loadSnapshot?: (taskId: string) => Promise<unknown>;
  WebSocketImpl?: typeof WebSocket;
  isSecure?: () => boolean;
  baseDelayMs?: number;
  pollIntervalMs?: number;
  maxFailures?: number;
}

export type TaskEventConnectionState = "connected" | "reconnecting" | "polling";

export interface TaskEventHandlers {
  onSnapshot?: (snapshot: unknown) => void;
  onConnectionChange?: (state: TaskEventConnectionState, attempt?: number) => void;
}

export interface TaskEventSession {
  start(
    taskId: string,
    after: number,
    onEvent: (event: TaskEvent) => void,
    handlers?: TaskEventHandlers
  ): Promise<void>;
  stop(): void;
}

interface WsFrame {
  type?: string;
  data?: unknown;
}

export function createTaskEventSession(deps: TaskEventSessionDeps): TaskEventSession {
  const baseDelayMs = deps.baseDelayMs ?? 1000;
  const pollIntervalMs = deps.pollIntervalMs ?? 3000;
  const maxFailures = deps.maxFailures ?? 3;
  const isSecure = deps.isSecure ?? (() => window.location.protocol === "https:");
  const WebSocketImpl = deps.WebSocketImpl ?? WebSocket;

  let socket: WebSocket | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let failures = 0;
  let stopped = true;
  // 会话代次守卫：每次 start/stop 单调递增，阻止过期异步请求污染新任务连接
  let sessionGen = 0;
  let taskId: string | null = null;
  let lastEventId = 0;
  let onEvent: (event: TaskEvent) => void = () => undefined;
  let handlers: TaskEventHandlers = {};

  function isStale(gen: number): boolean {
    return stopped || gen !== sessionGen;
  }

  function merge(event: TaskEvent): void {
    if (event.event_id <= lastEventId) {
      return; // 去重：重连/补拉/轮询都可能重复投递
    }
    lastEventId = event.event_id;
    onEvent(event);
  }

  async function replayMissed(gen: number): Promise<void> {
    if (isStale(gen) || taskId === null) {
      return;
    }
    const currentTaskId = taskId;
    const page = await deps.replay(currentTaskId, lastEventId);
    if (isStale(gen)) {
      return;
    }
    for (const event of page.events) {
      merge(event);
    }
  }

  function socketUrl(currentTaskId: string, ticket: string): string {
    const query = new URLSearchParams({ ticket, after_event_id: String(lastEventId) });
    // 跨域部署时走 API 所在 origin；未配置时沿用「当前页面 origin」（改造前行为）
    const wsBase = resolveWsBase();
    const origin = wsBase || `${isSecure() ? "wss" : "ws"}://${window.location.host}`;
    return `${origin}${API_PREFIX}/tasks/${currentTaskId}/ws?${query.toString()}`;
  }

  function stopPolling(): void {
    if (pollTimer !== null) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function startPolling(gen: number): void {
    if (pollTimer !== null || isStale(gen)) {
      return;
    }
    handlers.onConnectionChange?.("polling");
    pollTimer = setInterval(() => {
      if (isStale(gen)) {
        stopPolling();
        return;
      }
      void replayMissed(gen).catch(() => undefined);
    }, pollIntervalMs);
  }

  function scheduleReconnect(gen: number): void {
    if (isStale(gen)) {
      return;
    }
    failures += 1;
    if (failures > maxFailures) {
      startPolling(gen);
      return;
    }
    handlers.onConnectionChange?.("reconnecting", failures);
    const delay = baseDelayMs * 3 ** (failures - 1);
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      if (isStale(gen)) {
        return;
      }
      void connect(gen).catch(() => scheduleReconnect(gen));
    }, delay);
  }

  async function connect(gen: number): Promise<void> {
    if (isStale(gen) || taskId === null) {
      return;
    }
    const currentTaskId = taskId;
    await deps.loadSnapshot?.(currentTaskId);
    if (isStale(gen)) {
      return;
    }
    await replayMissed(gen);
    if (isStale(gen)) {
      return;
    }
    const ticket = await deps.wsTicket(currentTaskId);
    if (isStale(gen)) {
      return;
    }

    const current = new WebSocketImpl(socketUrl(currentTaskId, ticket.ticket));
    if (isStale(gen)) {
      current.onopen = null;
      current.onclose = null;
      current.onerror = null;
      current.onmessage = null;
      current.close();
      return;
    }
    socket = current;
    current.onopen = () => {
      if (isStale(gen) || socket !== current) {
        current.onclose = null;
        current.close();
        return;
      }
      failures = 0;
      handlers.onConnectionChange?.("connected");
    };
    current.onmessage = (message: { data: string }) => {
      if (isStale(gen) || socket !== current) {
        return;
      }
      try {
        const frame = JSON.parse(String(message.data)) as WsFrame;
        if (frame.type === "snapshot") {
          handlers.onSnapshot?.(frame.data);
        } else if (frame.type === "event" && frame.data) {
          merge(frame.data as TaskEvent);
        }
      } catch {
        // 非 JSON 帧直接忽略，避免污染状态
      }
    };
    current.onclose = () => {
      if (isStale(gen) || socket !== current) {
        return;
      }
      scheduleReconnect(gen);
    };
    current.onerror = () => {
      current.close();
    };
  }

  async function start(
    targetTaskId: string,
    after: number,
    eventHandler: (event: TaskEvent) => void,
    sessionHandlers: TaskEventHandlers = {}
  ): Promise<void> {
    stop();
    const gen = ++sessionGen;
    stopped = false;
    taskId = targetTaskId;
    lastEventId = after;
    onEvent = eventHandler;
    handlers = sessionHandlers;
    failures = 0;
    try {
      await connect(gen);
    } catch {
      if (!isStale(gen)) {
        scheduleReconnect(gen);
      }
    }
  }

  function stop(): void {
    sessionGen += 1;
    stopped = true;
    stopPolling();
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (socket !== null) {
      socket.onopen = null;
      socket.onclose = null;
      socket.onerror = null;
      socket.onmessage = null;
      socket.close();
      socket = null;
    }
    taskId = null;
    onEvent = () => undefined;
    handlers = {};
    failures = 0;
  }

  return { start, stop };
}
