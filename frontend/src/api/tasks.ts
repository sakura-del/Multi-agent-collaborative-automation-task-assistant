import api from "./client";
import type {
  TaskControl,
  TaskCreate,
  TaskCreateOut,
  TaskEventPage,
  TaskSnapshot,
  TaskSummary,
  WsTicket,
} from "./types";

// 任务 API：对接后端 /api/v1/tasks/*（视图层一律通过 store 调用）
export const taskApi = {
  list: () => api.get<TaskSummary[]>("/tasks").then((response) => response.data),

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
