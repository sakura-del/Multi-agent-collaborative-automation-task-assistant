import api from "./client";
import type { EvidenceLocator } from "./types";
import type { ExtractionColumnSpec } from "@/config/extraction-presets";

export type ExtractionSourceType = "knowledge_doc" | "screening_record";
export type ExtractionCellStatus = "pending" | "extracted" | "not_reported" | "error";
export type ExtractionTableStatus = "draft" | "running" | "completed" | "failed";

export interface ExtractionSourceRef {
  source_type: ExtractionSourceType;
  source_id: string;
}

export interface ExtractionSourceOut {
  source_type: ExtractionSourceType;
  source_id: string;
  title: string;
  year?: number | null;
  source_url?: string | null;
}

export interface ExtractionCellOut {
  source_type: ExtractionSourceType;
  source_id: string;
  column_key: string;
  status: ExtractionCellStatus;
  value?: string | null;
  quote?: string | null;
  passage?: string | null;
  locator?: EvidenceLocator | null;
  evidence_scope: "full_text" | "abstract_only";
  confidence?: number | null;
}

export interface ExtractionTableSummaryOut {
  id: string;
  task_id?: string | null;
  title: string;
  status: ExtractionTableStatus;
  error?: string | null;
  column_count: number;
  source_count: number;
  created_at: string;
  updated_at: string;
}

export interface ExtractionTableOut {
  id: string;
  task_id?: string | null;
  title: string;
  status: ExtractionTableStatus;
  error?: string | null;
  columns: ExtractionColumnSpec[];
  sources: ExtractionSourceOut[];
  cells: ExtractionCellOut[];
  created_at: string;
  updated_at: string;
}

export interface ExtractionTableCreatePayload {
  title: string;
  task_id?: string | null;
  columns: ExtractionColumnSpec[];
  sources: ExtractionSourceRef[];
  llm_credential_id?: string | null;
  auto_run?: boolean;
}

export interface ExtractionColumnAddPayload {
  column: ExtractionColumnSpec;
  auto_run?: boolean;
  llm_credential_id?: string | null;
}

export interface ExtractionRunPayload {
  column_keys?: string[];
  source_ids?: string[];
  llm_credential_id?: string | null;
}

/** 列出当前用户的抽取表摘要（可选按 task_id 过滤）。 */
export async function listExtractionTables(
  taskId?: string,
): Promise<ExtractionTableSummaryOut[]> {
  const params = taskId ? `?task_id=${encodeURIComponent(taskId)}` : "";
  const response = await api.get<{ items: ExtractionTableSummaryOut[] }>(`/extractions${params}`);
  return response.data.items ?? [];
}

/** 获取单张抽取表详情及全部单元格。 */
export async function getExtractionTable(tableId: string): Promise<ExtractionTableOut> {
  const response = await api.get<ExtractionTableOut>(`/extractions/${encodeURIComponent(tableId)}`);
  return response.data;
}

/** 创建抽取表（默认后台自动开始抽取）。 */
export async function createExtractionTable(
  payload: ExtractionTableCreatePayload,
): Promise<ExtractionTableOut> {
  const response = await api.post<ExtractionTableOut>("/extractions", payload);
  return response.data;
}

/** 向既有抽取表追加一列。 */
export async function addExtractionColumn(
  tableId: string,
  payload: ExtractionColumnAddPayload,
): Promise<ExtractionTableOut> {
  const response = await api.post<ExtractionTableOut>(
    `/extractions/${encodeURIComponent(tableId)}/columns`,
    payload,
  );
  return response.data;
}

/** 重跑整表，或仅对指定列/论文补抽。 */
export async function runExtractionTable(
  tableId: string,
  payload: ExtractionRunPayload = {},
): Promise<ExtractionTableOut> {
  const response = await api.post<ExtractionTableOut>(
    `/extractions/${encodeURIComponent(tableId)}/run`,
    payload,
  );
  return response.data;
}

/** 删除抽取表。 */
export async function deleteExtractionTable(tableId: string): Promise<void> {
  await api.delete(`/extractions/${encodeURIComponent(tableId)}`);
}

/** 导出抽取表 CSV（UTF-8 BOM，Excel 兼容）。 */
export async function exportExtractionTableCsv(
  tableId: string,
  includeQuotes = true,
): Promise<{ blob: Blob; filename: string }> {
  const params = new URLSearchParams({
    format: "csv",
    include_quotes: includeQuotes ? "true" : "false",
  });
  const response = await api.get<Blob>(
    `/extractions/${encodeURIComponent(tableId)}/export?${params.toString()}`,
    { responseType: "blob" },
  );
  const disposition = String(response.headers?.["content-disposition"] ?? "");
  const match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  const filename = match ? decodeURIComponent(match[1]) : `extraction-${tableId}.csv`;
  const blob =
    response.data instanceof Blob
      ? response.data
      : new Blob([response.data as BlobPart], { type: "text/csv;charset=utf-8" });
  return { blob, filename };
}
