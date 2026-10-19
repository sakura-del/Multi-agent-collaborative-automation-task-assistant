// 后端契约类型：与 FastAPI 的 Pydantic 输出字段保持一致（snake_case）
export interface UserOut {
  id: string;
  username: string;
  email: string;
  role: string;
  is_active: boolean;
  created_at: string;
}

export interface TokenOut {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface LoginInput {
  account: string;
  password: string;
}

export interface RegisterInput {
  username: string;
  email: string;
  password: string;
}

// ===== 任务契约（与后端 app/orchestration/contracts.py 对齐）=====

export type TaskStatus =
  | "created"
  | "planning"
  | "waiting_approval"
  | "running"
  | "reviewing"
  | "completed"
  | "paused"
  | "retrying"
  | "waiting_human"
  | "failed"
  | "cancelled";

export type SubtaskStatus =
  | "pending"
  | "ready"
  | "running"
  | "validating"
  | "completed"
  | "failed"
  | "retrying"
  | "waiting_human"
  | "paused"
  | "terminated"
  | "cancelled";

export type TaskCommand = "pause" | "resume" | "cancel" | "retry_node" | "supply_input";

export type PlanNodeType = "search" | "extract" | "write" | "validate" | "review";

export interface TaskCreate {
  topic: string;
  keywords: string[];
  date_from?: string | null;
  date_to?: string | null;
  max_documents?: number;
  output_language?: "zh" | "en";
  /** M2 起样式是一等实体：取值可以是预置样式或用户上传的 style_id */
  citation_style?: string;
  document_ids?: string[];
  offline_dataset?: boolean;
  /** 本任务是否做外部引用核验：true 时即使离线语料也与 CrossRef/OpenAlex 比对（Issue #3 遗留项） */
  verify_references?: boolean;
}

export interface TaskCreateOut {
  task_id: string;
  status: string;
}

export interface TaskSummary {
  task_id: string;
  status: TaskStatus;
  title: string;
  plan_version: number;
  revision: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface PlanNode {
  id: string;
  type: PlanNodeType;
  title: string;
  dependencies: string[];
  input: Record<string, unknown>;
  required_evidence: number;
}

export interface TaskPlan {
  version: number;
  topic: string;
  research_questions: string[];
  search_queries: string[];
  acceptance_criteria: string[];
  nodes: PlanNode[];
}

export interface SubtaskSnapshot {
  node_id: string;
  title: string;
  status: SubtaskStatus;
  retry_count: number;
  started_at: string | null;
  completed_at: string | null;
  output: Record<string, unknown> | null;
  error: string | null;
}

export interface Artifact {
  artifact_id: string;
  kind: "markdown" | "docx";
  filename: string;
  download_name: string;
  size: number;
  sha256: string;
  created_at: string;
}

export interface ReviewReport {
  final_markdown: string;
  bibliography: Array<Record<string, unknown>>;
  scores: Record<string, number>;
  issues: string[];
  stats: Record<string, unknown>;
}

export interface TaskSnapshot {
  task_id: string;
  status: TaskStatus;
  title: string;
  plan_version: number;
  revision: number;
  plan: TaskPlan | null;
  subtasks: SubtaskSnapshot[];
  artifacts: Artifact[];
  review_report: ReviewReport | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface TaskEvent {
  event_id: number;
  task_id: string;
  subtask_id: string | null;
  event_type: string;
  status: string;
  occurred_at: string;
  payload: Record<string, unknown>;
}

export interface TaskEventPage {
  events: TaskEvent[];
  next_event_id: number;
}

export interface TaskControl {
  command: TaskCommand;
  node_id?: string | null;
  subtask_id?: string | null;
  input?: Record<string, unknown> | null;
}

export interface WsTicket {
  ticket: string;
  expires_in: number;
}

// ===== 知识库契约（与后端 app/schemas/knowledge.py 对齐）=====

export type ChunkStrategy = "recursive" | "fixed" | "semantic";

export interface KnowledgeDocument {
  id: string;
  title: string;
  doc_type: string;
  index_status: string;
  chunk_count: number;
  created_at: string;
  source: string;
  original_name: string | null;
  failure_reason: string | null;
}

export interface KnowledgeDocumentList {
  documents: KnowledgeDocument[];
}

export interface KnowledgeStats {
  total_docs: number;
  total_chunks: number;
  vector_index_ready: boolean;
  bm25_index_ready: boolean;
  last_update?: string | null;
}

export interface KnowledgeSearchInput {
  query: string;
  top_k?: number;
  skip_cache?: boolean;
  skip_rerank?: boolean;
}

export interface KnowledgeSearchItem {
  chunk_id: string;
  doc_id: string;
  text: string;
  score: number;
  source: string;
}

export interface KnowledgeSearchOut {
  query: string;
  total: number;
  results: KnowledgeSearchItem[];
}

// ===== 引用样式契约（与后端 app/schemas/citation_style.py 对齐，Issue #4 M2 §1.3）=====

/** `preset` = 系统预置（不可删）；`custom` = 当前用户上传（可删） */
export type CitationStyleSource = "preset" | "custom";

export interface CitationStyleOut {
  style_id: string;
  title: string;
  numbering: "citation-number" | "none";
  ordering: "citation-order" | "author-date";
  default_locale: string;
  source: CitationStyleSource;
  is_default: boolean;
}

export interface CitationStyleList {
  styles: CitationStyleOut[];
  default_style_id: string;
}

// ===== 引用真实性核验契约（Issue #3 前端展示；计划 2026-10-19-03b §1.2）=====

/** 单条参考文献的核验结论（与后端 `ReferenceVerdict` 对齐）。 */
export type VerificationStatus = "verified" | "metadata_mismatch" | "not_found" | "unverified";

/**
 * 核验统计汇总（`review_report.stats.reference_verification`）。
 *
 * 离线任务默认不做核验：该键**不存在** → 展示层必须走明确空态，不得臆造 0 值。
 */
export interface ReferenceVerificationSummary {
  total: number;
  verified: number;
  metadata_mismatch: number;
  not_found: number;
  unverified: number;
  cached_hits: number;
  dropped: number;
  intercepted: boolean;
}

/** 结果页参考文献条目；`verification_status` 为 `null` 表示未核验或值非法。 */
export interface BibliographyEntry {
  index: number;
  title: string;
  url: string | null;
  verification_status: VerificationStatus | null;
}
