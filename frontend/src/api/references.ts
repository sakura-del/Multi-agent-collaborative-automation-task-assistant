import api from "./client";
import type { ReferenceImportFormat, ReferenceImportResponse } from "./types";

// 参考文献导入 API（Phase 1 P1-E 端点接线）：
//   把 BibTeX / RIS 文本解析成条目预览——**不落库**，由用户在界面上确认后再走保存路径。
// Bearer 鉴权由 client.ts 的请求拦截器统一注入。

export interface ImportReferencesPayload {
  content: string;
  /** 默认 `auto`：交给后端按内容探测格式 */
  format?: ReferenceImportFormat;
}

/**
 * 导入 BibTeX / RIS 并返回条目预览。
 *
 * 后端对非法输入固定返回 400 且 `detail` 可读（带定位信息）；`client.ts` 的
 * 错误归一化只保留 `message`，会吞掉 `detail`，因此这里把 400 当正常响应解析后
 * 手工抛出可展示错误（401 刷新等仍走既有拦截器）。
 */
export async function importReferences(
  payload: ImportReferencesPayload
): Promise<ReferenceImportResponse> {
  const response = await api.post<ReferenceImportResponse>(
    "/references/import",
    { content: payload.content, format: payload.format ?? "auto" },
    { validateStatus: (status) => status < 400 || status === 400 }
  );
  if (response.status === 400) {
    throw new Error(importRejectionDetail(response.data) ?? "导入失败（400）");
  }
  return response.data;
}

/** 从 400 响应体提取可展示的失败原因；取不到时返回 null */
export function importRejectionDetail(data: unknown): string | null {
  if (typeof data === "string") {
    return data.trim() ? data : null;
  }
  if (!data || typeof data !== "object") {
    return null;
  }
  const detail = (data as { detail?: unknown }).detail;
  if (typeof detail === "string") {
    return detail.trim() ? detail : null;
  }
  if (Array.isArray(detail)) {
    const reasons = detail
      .map((item) => (item && typeof item === "object" ? (item as { msg?: unknown }).msg : item))
      .filter((item): item is string => typeof item === "string" && item.trim().length > 0);
    return reasons.length ? reasons.join("；") : null;
  }
  return null;
}
