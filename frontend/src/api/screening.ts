import api from "./client";
import type {
  PrismaCounts,
  ScreeningDecisionPayload,
  ScreeningPriority,
  ScreeningRecord,
  SearchLogCreatePayload,
  SearchLogOut,
} from "./types";

// PRISMA 筛选工作台 API（Phase 2）
// Bearer 鉴权由 client.ts 的请求拦截器统一注入。

/** 记录一次检索（落快照，跨源去重在后端完成） */
export async function recordSearch(payload: SearchLogCreatePayload): Promise<SearchLogOut> {
  const response = await api.post<SearchLogOut>("/screening/search-logs", payload);
  return response.data;
}

/** 列出某个任务的候选记录；`stage` 可选（按当前阶段过滤）。 */
export function listScreeningRecords(
  taskId: string,
  stage?: string
): Promise<ScreeningRecord[]> {
  const params = new URLSearchParams({ task_id: taskId });
  if (stage) {
    params.set("stage", stage);
  }
  return api
    .get<{ items: ScreeningRecord[] }>(`/screening/records?${params.toString()}`)
    .then((response) => response.data.items ?? []);
}

/**
 * 追加一条筛选决策。
 *
 * 后端对「排除但未给理由」固定返回 400，`detail` 可读；`client.ts` 的错误归一化
 * 只保留 `message`，会吞掉 `detail`，因此这里把 400 当正常响应解析后手工抛出
 * （与 `uploadTemplate` 处理模板准入 400 的既有做法一致）。
 */
export async function createDecision(
  recordId: string,
  payload: ScreeningDecisionPayload
): Promise<{ record_id: string; current_stage: string }> {
  const response = await api.post<{ record_id: string; current_stage: string }>(
    `/screening/records/${encodeURIComponent(recordId)}/decisions`,
    payload,
    { validateStatus: (status) => status < 400 || status === 400 }
  );
  if (response.status === 400) {
    const detail = (response.data as { detail?: string } | undefined)?.detail;
    throw new Error(detail || "筛选决策提交失败（400）");
  }
  return response.data;
}

/** PRISMA 计数 + Markdown 表格 + Mermaid 流程图。`language` 支持中英输出。 */
export async function getPrismaCounts(
  taskId: string,
  language: "zh" | "en" = "zh"
): Promise<PrismaCounts> {
  const params = new URLSearchParams({ task_id: taskId, language });
  const response = await api.get<PrismaCounts>(`/screening/prisma?${params.toString()}`);
  return response.data;
}

/**
 * 任务内「主动排序」推荐顺序（标题/摘要阶段）。
 *
 * 只返回占位名次（record_id + rank），前端不得据此自动纳入/排除；
 * 后端可能返回 `insufficient_labels` / `no_candidates`，调用方需按状态展示提示。
 */
export async function getScreeningPriority(
  taskId: string,
  stage: "title_abstract" = "title_abstract"
): Promise<ScreeningPriority> {
  const params = new URLSearchParams({ task_id: taskId, stage });
  const response = await api.get<ScreeningPriority>(`/screening/priority?${params.toString()}`);
  return response.data;
}
