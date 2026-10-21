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

// ===== Word 模板契约（与后端 app/api/v1/endpoints/templates.py 对齐，Phase 1 P1-A）=====

/** 模板中的一个 `{{ key }}` 占位符（UI 上映射到综述章节） */
export interface TemplatePlaceholder {
  key: string;
  label: string;
  required: boolean;
  sample: string;
}

export interface TemplateOut {
  template_id: string;
  name: string;
  /** 源 docx 字节的 sha256（完整性/去重） */
  checksum: string;
  size: number;
  placeholders: TemplatePlaceholder[];
  /** Word **styleId** → 语义角色（`h1`/`body`/`reference` 等） */
  style_map: Record<string, string>;
}

export interface TemplateListResponse {
  items: TemplateOut[];
}

/** 渲染请求：章节内容 + **已 CSL 渲染好的**参考文献行 */
export interface TemplateRenderRequest {
  sections: Record<string, string>;
  bibliography: string[];
  filename?: string;
}

// ===== 参考文献导入契约（与后端 app/api/v1/endpoints/references.py 对齐，Phase 1 P1-E）=====

export type ReferenceImportFormat = "bibtex" | "ris" | "auto";

export interface ImportedReferenceItem {
  id: string | null;
  type: string;
  title: string;
  authors: string[];
  year: number | null;
  container_title: string | null;
  volume: string | null;
  issue: string | null;
  page: string | null;
  publisher: string | null;
  doi: string | null;
  url: string | null;
}

export interface ReferenceImportResponse {
  /** 实际采用的解析格式（`auto` 会被解析成具体值） */
  format: Exclude<ReferenceImportFormat, "auto">;
  count: number;
  items: ImportedReferenceItem[];
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

/* ===== Phase 2 · PRISMA 筛选工作台 ===== */

/** 一次检索的可复现日志 */
export interface SearchLogOut {
  search_log_id: string;
  task_id: string;
  query: string;
  sources: string[];
  hits_total: number;
  hits_by_source: Record<string, number>;
  hits_deduped: number;
  executed_at: string;
}

/** 检索提交载荷（跨源去重在后端完成） */
export interface SearchLogCreatePayload {
  task_id: string;
  query: string;
  sources: string[];
  hits_by_source?: Record<string, number>;
  hits_total?: number;
  records: Array<{
    source_id: string;
    title: string;
    authors: string[];
    abstract: string;
    doi?: string | null;
  }>;
}

/** 候选文献快照 */
export interface ScreeningRecord {
  record_id: string;
  source: string;
  source_id: string;
  title: string;
  authors: string[];
  abstract: string;
  doi: string | null;
  fulltext_status: string;
  current_stage: string;
}

export type ScreeningStage = "title_abstract" | "fulltext" | "included";
export type ScreeningVerdict = "include" | "exclude" | "maybe";

export interface ScreeningDecisionPayload {
  stage: ScreeningStage;
  verdict: ScreeningVerdict;
  exclusion_reason?: string | null;
  note?: string;
}

/** PRISMA 2020 计数 + 可直接进论文的渲染结果 */
export interface PrismaCounts {
  identified_by_source: Record<string, number>;
  identified_total: number;
  duplicates_removed: number;
  records_screened: number;
  title_abstract: { screened: number; excluded: number };
  fulltext: { assessed: number; excluded: number };
  included: number;
  exclusion_reasons: Record<string, number>;
  markdown: string;
  mermaid: string;
}
