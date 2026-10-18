import type { TaskEvent, TaskEventPage, WsTicket } from "@/api/types";

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

export interface TaskEventHandlers {
  onSnapshot?: (snapshot: unknown) => void;
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
  let taskId: string | null = null;
  let lastEventId = 0;
  let onEvent: (event: TaskEvent) => void = () => undefined;
  let handlers: TaskEventHandlers = {};

  function merge(event: TaskEvent): void {
    if (event.event_id <= lastEventId) {
      return; // 去重：重连/补拉/轮询都可能重复投递
    }
    lastEventId = event.event_id;
    onEvent(event);
  }

  async function replayMissed(): Promise<void> {
    if (taskId === null) {
      return;
    }
    const page = await deps.replay(taskId, lastEventId);
    for (const event of page.events) {
      merge(event);
    }
  }

  function socketUrl(ticket: string): string {
    const scheme = isSecure() ? "wss" : "ws";
    const query = new URLSearchParams({ ticket, after_event_id: String(lastEventId) });
    return `${scheme}://${window.location.host}/api/v1/tasks/${taskId}/ws?${query.toString()}`;
  }

  function stopPolling(): void {
    if (pollTimer !== null) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function startPolling(): void {
    if (pollTimer !== null || stopped) {
      return;
    }
    pollTimer = setInterval(() => {
      void replayMissed().catch(() => undefined);
    }, pollIntervalMs);
  }

  function scheduleReconnect(): void {
    if (stopped) {
      return;
    }
    failures += 1;
    if (failures > maxFailures) {
      startPolling();
      return;
    }
    const delay = baseDelayMs * 3 ** (failures - 1);
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      void connect().catch(() => scheduleReconnect());
    }, delay);
  }

  async function connect(): Promise<void> {
    if (stopped || taskId === null) {
      return;
    }
    await deps.loadSnapshot?.(taskId);
    await replayMissed();
    const ticket = await deps.wsTicket(taskId);
    if (stopped) {
      return;
    }

    const current = new WebSocketImpl(socketUrl(ticket.ticket));
    socket = current;
    current.onopen = () => {
      failures = 0;
    };
    current.onmessage = (message: { data: string }) => {
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
      scheduleReconnect();
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
    stopped = false;
    taskId = targetTaskId;
    lastEventId = after;
    onEvent = eventHandler;
    handlers = sessionHandlers;
    failures = 0;
    await connect();
  }

  function stop(): void {
    stopped = true;
    stopPolling();
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (socket !== null) {
      socket.onclose = null;
      socket.onerror = null;
      socket.onmessage = null;
      socket.close();
      socket = null;
    }
    taskId = null;
  }

  return { start, stop };
}
