import api from "./client";
import type { CitationStyleList, CitationStyleOut } from "./types";

// 引用样式 API（Issue #4 M2 §1.3）：列表 / 上传 .csl / 删除自定义
// 全部 Bearer 鉴权由 client.ts 的请求拦截器统一注入

/** 列出样式：预置样式 + 当前用户上传的自定义样式，并给出默认样式 id */
export function listCitationStyles(): Promise<CitationStyleList> {
  return api.get<CitationStyleList>("/citation-styles").then((response) => response.data);
}

/**
 * 上传 `.csl` 样式文件。
 *
 * - multipart 字段名固定为 `file`；**不手动设置 Content-Type**，boundary 交由浏览器生成；
 * - 准入失败时后端固定返回 400，`detail` 为字符串（可能含逐条 reasons）。
 *   `client.ts` 的错误归一化只保留 `message`，会把 FastAPI 的 `detail` 吞掉，
 *   因此这里把 400 当正常响应解析后手工抛出可展示的错误（401 刷新等仍走既有拦截器）。
 */
export async function uploadCitationStyle(file: File): Promise<CitationStyleOut> {
  const form = new FormData();
  form.append("file", file);
  const response = await api.post<CitationStyleOut>("/citation-styles", form, {
    validateStatus: (status) => status < 400 || status === 400,
  });
  if (response.status === 400) {
    throw new Error(styleRejectionDetail(response.data) ?? "样式上传失败（400）");
  }
  return response.data;
}

/** 删除当前用户的自定义样式（预置样式后端返回 404/400，由调用方展示错误） */
export function deleteCitationStyle(styleId: string): Promise<void> {
  return api
    .delete<void>(`/citation-styles/${encodeURIComponent(styleId)}`)
    .then(() => undefined);
}

/** 从 400 响应体提取可展示的失败原因；取不到时返回 null */
export function styleRejectionDetail(data: unknown): string | null {
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
  // 防御：FastAPI 的请求校验错误会把 detail 返回成数组
  if (Array.isArray(detail)) {
    const reasons = detail
      .map((item) => (item && typeof item === "object" ? (item as { msg?: unknown }).msg : item))
      .filter((item): item is string => typeof item === "string" && item.trim().length > 0);
    return reasons.length ? reasons.join("；") : null;
  }
  return null;
}
