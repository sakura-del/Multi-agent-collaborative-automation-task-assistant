import api from "./client";
import type {
  LibraryItemOut,
  LibraryListOut,
  LibrarySaveResponse,
  LibrarySummaryOut,
} from "./types";

// 引用库 API（2026-10-26 引用库计划 §3.5）
//
// 背景：取证发现导入页能正确解析 BibTeX/RIS，但解析完**什么也做不了** ——
// 后端没有引用库表，任务文献只来自检索源。本模块让「导入 → 保存 → 任务使用」
// 这条链路成立。
//
// Bearer 鉴权由 client.ts 的请求拦截器统一注入。

/** 列出引用库条目（按创建时间倒序，稳定排序由后端保证） */
export function listLibraryItems(params?: {
  limit?: number;
  offset?: number;
}): Promise<LibraryListOut> {
  return api
    .get<LibraryListOut>("/reference-library", { params })
    .then((response) => response.data);
}

/** 引用库摘要：总条数 + 可作文献源条数（任务表单用它提示「将有 N 篇可用」） */
export function getLibrarySummary(): Promise<LibrarySummaryOut> {
  return api
    .get<LibrarySummaryOut>("/reference-library/summary")
    .then((response) => response.data);
}

/**
 * 保存条目到引用库。
 *
 * ## 部分成功是常态，不是异常
 *
 * 用户粘一份 `.bib` 里常有几条缺年份的（预印本、网页引用）。后端逐条判定：
 * 能进的进，不能进的带原因返回。**只有全部被拒才 400** —— 此时这里会抛出
 * 可展示错误（`detail` 里是逐条原因）。
 */
export async function saveLibraryItems(
  items: Record<string, unknown>[]
): Promise<LibrarySaveResponse> {
  const response = await api.post<LibrarySaveResponse>(
    "/reference-library",
    { items },
    { validateStatus: (status) => status < 400 || status === 400 }
  );
  if (response.status === 400) {
    throw new Error(saveRejectionDetail(response.data) ?? "保存失败（400）");
  }
  return response.data;
}

/** 删除单条 */
export function deleteLibraryItem(itemId: string): Promise<void> {
  return api.delete(`/reference-library/${encodeURIComponent(itemId)}`).then(() => undefined);
}

/**
 * 部分更新条目（2026-10-26 引用库遗留计划 §3.5）。
 *
 * 只发**用户真正改了的**字段：后端用 `exclude_unset` 判断，
 * 发全量会把用户没动的字段覆盖成 `null`。
 *
 * 改完后端会**重新跑准入检查**（把年份改空 → 400），
 * 所以这里要处理 400 而不是假定一定成功。
 */
export async function updateLibraryItem(
  itemId: string,
  changes: Record<string, unknown>
): Promise<LibraryItemOut> {
  const response = await api.patch<LibraryItemOut>(
    `/reference-library/${encodeURIComponent(itemId)}`,
    changes,
    { validateStatus: (status) => status < 400 || status === 400 }
  );
  if (response.status === 400) {
    throw new Error(saveRejectionDetail(response.data) ?? "修改后不符合入库条件");
  }
  return response.data;
}

/** 清空引用库，返回删除条数 */
export function clearLibrary(): Promise<{ deleted: number }> {
  return api.delete<{ deleted: number }>("/reference-library").then((response) => response.data);
}

/** 从 400 响应体提取可展示的失败原因；取不到时返回 null */
export function saveRejectionDetail(data: unknown): string | null {
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
