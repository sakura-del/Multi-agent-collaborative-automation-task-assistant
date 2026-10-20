import api from "./client";
import type {
  TemplateListResponse,
  TemplateOut,
  TemplateRenderRequest,
} from "./types";

// Word 模板 API（Phase 1 P1-A 端点接线）：
//   列出 / 上传 / 详情 / 删除 / 渲染
// 全部 Bearer 鉴权由 client.ts 的请求拦截器统一注入。

/** 列出当前用户的 Word 模板 */
export function listTemplates(): Promise<TemplateOut[]> {
  return api
    .get<TemplateListResponse>("/templates")
    .then((response) => response.data.items ?? []);
}

/**
 * 上传 `.docx` 模板。
 *
 * - multipart 字段名固定为 `file`，`name` 为显示名称；**不手动设置 Content-Type**，
 *   boundary 交由浏览器生成；
 * - 准入失败后端固定返回 400，`detail` 为字符串（可能含逐条 reasons）。
 *   `client.ts` 的错误归一化只保留 `message`，会把 FastAPI 的 `detail` 吞掉，
 *   因此这里把 400 当正常响应解析后手工抛出可展示的错误（401 刷新等仍走既有拦截器）。
 */
export async function uploadTemplate(file: File, name: string): Promise<TemplateOut> {
  const form = new FormData();
  form.append("file", file);
  form.append("name", name);
  const response = await api.post<TemplateOut>("/templates", form, {
    validateStatus: (status) => status < 400 || status === 400,
  });
  if (response.status === 400) {
    throw new Error(templateRejectionDetail(response.data) ?? "模板上传失败（400）");
  }
  return response.data;
}

/** 模板详情 */
export function getTemplate(templateId: string): Promise<TemplateOut> {
  return api
    .get<TemplateOut>(`/templates/${encodeURIComponent(templateId)}`)
    .then((response) => response.data);
}

/** 删除模板（owner-scoped；不存在 / 跨用户后端返回 404） */
export function deleteTemplate(templateId: string): Promise<void> {
  return api
    .delete<void>(`/templates/${encodeURIComponent(templateId)}`)
    .then(() => undefined);
}

/**
 * 用模板渲染成稿，返回 `.docx` 的 Blob。
 *
 * `bibliography` 必须是**已 CSL 渲染好的行**——引用渲染在上游完成，
 * 模板层只负责排版（这样切换引用样式不必改模板）。
 */
export async function renderTemplate(
  templateId: string,
  payload: TemplateRenderRequest
): Promise<Blob> {
  const response = await api.post<Blob>(
    `/templates/${encodeURIComponent(templateId)}/render`,
    payload,
    {
      responseType: "blob",
      validateStatus: (status) => status < 400 || status === 400,
    }
  );
  if (response.status === 400) {
    // 以 blob 请求时错误体也是 blob，需读回文本再解析
    const detail = await blobErrorDetail(response.data);
    throw new Error(detail ?? "模板渲染失败（400）");
  }
  return response.data;
}

/** 从 blob 错误响应里读出可展示原因（后端 400 的 body 是 JSON） */
async function blobErrorDetail(data: unknown): Promise<string | null> {
  if (!(data instanceof Blob)) {
    return templateRejectionDetail(data);
  }
  try {
    const text = await data.text();
    return templateRejectionDetail(JSON.parse(text));
  } catch {
    return null;
  }
}

/** 从 400 响应体提取可展示的失败原因；取不到时返回 null */
export function templateRejectionDetail(data: unknown): string | null {
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
