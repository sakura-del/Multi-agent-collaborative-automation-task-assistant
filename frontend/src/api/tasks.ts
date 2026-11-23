import api from "./client";
import type {
  TaskControl,
  TaskCreate,
  TaskCreateOut,
  TaskEventPage,
  TaskRefreshRun,
  TaskRoutine,
  TaskRoutineInput,
  TaskSnapshot,
  TaskSummary,
  WsTicket,
} from "./types";

/** 与后端 `/tasks?status_group=` 一一对应；语义见 utils/task-status.ts。 */
export type TaskStatusGroup = "all" | "needs_me" | "active" | "done";

export interface TaskCounts {
  all: number;
  needs_me: number;
  active: number;
  done: number;
}

export interface TaskListPage {
  items: TaskSummary[];
  /** 可见任务总数（来自 X-Total-Count）；后端没给时退化为本页条数。 */
  total: number;
}

// 任务 API：对接后端 /api/v1/tasks/*（视图层一律通过 store 调用）
export const taskApi = {
  /**
   * 分页列出任务。
   *
   * 后端 body 仍是数组（老客户端不受影响），总数在 `X-Total-Count` 头里。
   * 读不到头时（跨域未 expose / 旧版本后端）退化为本页条数——这样最坏
   * 情况是「只有一页」，而不是 NaN 页把分页渲染成空白。
   */
  list: async (params?: {
    skip?: number;
    limit?: number;
    status_group?: TaskStatusGroup;
    q?: string;
  }): Promise<TaskListPage> => {
    const response = await api.get<TaskSummary[]>("/tasks", { params });
    const header = Number.parseInt(String(response.headers["x-total-count"] ?? ""), 10);
    return {
      items: response.data,
      total: Number.isFinite(header) ? header : response.data.length,
    };
  },

  /** 分组计数（服务端全量）：分页后不能再用当前页条数当统计。 */
  counts: (params?: { q?: string }) =>
    api.get<TaskCounts>("/tasks/counts", { params }).then((response) => response.data),

  create: (payload: TaskCreate) =>
    api.post<TaskCreateOut>("/tasks", payload).then((response) => response.data),

  get: (taskId: string) =>
    api.get<TaskSnapshot>(`/tasks/${taskId}`).then((response) => response.data),

  approve: (taskId: string, revision: number) =>
    api
      .post<TaskSnapshot>(`/tasks/${taskId}/plan/approve`, { revision })
      .then((response) => response.data),

  control: (taskId: string, payload: TaskControl) =>
    api.post<TaskSnapshot>(`/tasks/${taskId}/control`, payload).then((response) => response.data),

  events: (taskId: string, after: number) =>
    api
      .get<TaskEventPage>(`/tasks/${taskId}/events`, { params: { after } })
      .then((response) => response.data),

  wsTicket: (taskId: string) =>
    api.post<WsTicket>("/ws-ticket", { task_id: taskId }).then((response) => response.data),

  /** 增量检索新文献（Issue #27，手动触发）；返回 running 记录，结果需轮询 refreshes。 */
  startRefresh: (taskId: string) =>
    api.post<TaskRefreshRun>(`/tasks/${taskId}/refresh`).then((response) => response.data),

  /** 刷新历史（最新在前）。 */
  refreshes: (taskId: string, limit = 20) =>
    api
      .get<TaskRefreshRun[]>(`/tasks/${taskId}/refreshes`, { params: { limit } })
      .then((response) => response.data),

  /** 定时重跑排期（没有则 null）。 */
  routine: (taskId: string) =>
    api.get<TaskRoutine | null>(`/tasks/${taskId}/routine`).then((response) => response.data),

  saveRoutine: (taskId: string, payload: TaskRoutineInput) =>
    api.put<TaskRoutine>(`/tasks/${taskId}/routine`, payload).then((response) => response.data),

  deleteRoutine: (taskId: string) =>
    api.delete(`/tasks/${taskId}/routine`).then(() => undefined),

  /** 下载产物：以 blob 拉取，文件名优先取服务端 Content-Disposition */
  downloadArtifact: (taskId: string, artifactId: string) =>
    api
      .get<Blob>(`/tasks/${taskId}/artifacts/${artifactId}`, { responseType: "blob" })
      .then((response) => ({
        blob: response.data,
        filename: filenameFromDisposition(response.headers?.["content-disposition"]),
      })),
};

/** 解析 `attachment; filename="x.md"`（含 RFC 5987 的 filename*） */
function filenameFromDisposition(header: unknown): string | null {
  if (typeof header !== "string" || !header) {
    return null;
  }
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(header);
  if (encoded) {
    try {
      return decodeURIComponent(encoded[1]);
    } catch {
      return null;
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(header);
  return plain ? plain[1] : null;
}

export default taskApi;
