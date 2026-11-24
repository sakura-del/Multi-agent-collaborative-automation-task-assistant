// 后端契约类型：与 FastAPI 的 Pydantic 输出字段保持一致（snake_case）
export interface UserOut {
  id: string;
  username: string;
  email: string;
  role: string;
  is_active: boolean;
  email_verified_at?: string | null;
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
  /** ALTCHA PoW payload（后端 CAPTCHA__ENABLED=true 时必填） */
  captcha?: string;
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

/**
 * 人工控制命令。
 * `replan`：退回规划阶段重新生成规划 —— 规划失败时没有节点，`retry_node` 用不了。
 */
export type TaskCommand =
  | "pause"
  | "resume"
  | "cancel"
  | "retry_node"
  | "supply_input"
  | "replan";

export type PlanNodeType = "search" | "extract" | "write" | "validate" | "review";

/** 预设工作流（Issue #32）：文献综述 / 研究空白分析 / 验证引用 / 引文滚雪球。 */
export type WorkflowPreset =
  | "literature_review"
  | "research_gaps"
  | "verify_citations"
  | "citation_snowballing";

export type SnowballDirection = "both" | "backward" | "forward";

export interface TaskCreate {
  topic: string;
  keywords: string[];
  /** 预设工作流标识；缺省时后端按 literature_review 处理 */
  workflow_preset?: WorkflowPreset;
  /** 验证引用模式（verify_citations）下的待核验草稿文本 */
  draft_text?: string | null;
  /** 引文滚雪球模式（citation_snowballing）下的种子论文 DOI 或标题（最多 5 篇） */
  seed_dois?: string[];
  snowball_direction?: SnowballDirection;
  date_from?: string | null;
  date_to?: string | null;
  max_documents?: number;
  output_language?: "zh" | "en";
  /** M2 起样式是一等实体：取值可以是预置样式或用户上传的 style_id */
  citation_style?: string;
  document_ids?: string[];
  offline_dataset?: boolean;
  /**
   * 用用户自己的引用库作为文献源（2026-10-26 引用库计划）。
   * `true` 时检索节点从引用库取文献、完全不外呼。
   */
  reference_library?: boolean;
  /** 本任务是否做外部引用核验：true 时即使离线语料也与 CrossRef/OpenAlex 比对（Issue #3 遗留项） */
  verify_references?: boolean;
  /**
   * 本任务使用的模型凭据（Issue #15 BYOK）。
   * 不传 = 平台默认模型；传了则规划与写作都走该凭据。
   */
  llm_credential_id?: string | null;
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

/** 论断核验判定（Issue #31 Phase 2）。 */
export type ClaimVerdict =
  | "supported"
  | "weakly_supported"
  | "overstated"
  | "misrepresented"
  | "contradicted"
  | "unverifiable";

/** 原文页码/章节/图表定位（Issue #35）。 */
export interface EvidenceLocator {
  page_start?: number | null;
  page_end?: number | null;
  section_title?: string | null;
  table_ref?: string | null;
}

/** 单条论断的核验结论（后端 `ClaimCheckResult`）。 */
export interface ClaimCheckResult {
  claim_id: string;
  sentence_text: string;
  /** 全局引用编号（`E3` 形式，与正文 `[E3]` 对应） */
  cited_ref_ids: string[];
  verdict: ClaimVerdict;
  confidence: number;
  /** 原文中最相关的精确子句，用于高亮 */
  evidence_quote?: string | null;
  rationale: string;
  suggested_fix?: string | null;
  evidence_scope?: "full_text" | "abstract_only" | null;
  /** 命中 quote 所在的完整片段 */
  evidence_passage?: string | null;
  /** 命中片段的页码/章节/图表定位（Issue #35） */
  locator?: EvidenceLocator | null;
}

export interface ClaimStats {
  total?: number;
  supported?: number;
  flagged?: number;
  supported_rate?: number | null;
  by_verdict?: Record<string, number>;
}

/** 研究空白矩阵证据强度桶（Issue #32 Phase 3）。 */
export type EvidenceStrengthBucket =
  | "strong"
  | "weak_or_caveat"
  | "conflicting"
  | "gap";

export interface EvidenceMatrixCell {
  question_index: number;
  bucket: EvidenceStrengthBucket;
  claim_ids: string[];
  ref_indices: number[];
  evidence_kind: "excerpt" | "metadata" | "none";
  summary: string;
  suggested_query?: string | null;
}

export interface EvidenceMatrix {
  questions: string[];
  cells: EvidenceMatrixCell[];
}

export interface MatrixFillGapInput {
  question_index: number;
  query?: string | null;
}

export interface MatrixFillGapOut {
  task_id: string;
  question_index: number;
  query: string;
  added_count: number;
  duplicate_count: number;
  summary_markdown: string;
  evidence_matrix?: EvidenceMatrix | null;
  bibliography: Array<Record<string, unknown>>;
}

export interface ReviewReport {
  final_markdown: string;
  bibliography: Array<Record<string, unknown>>;
  scores: Record<string, number>;
  issues: string[];
  stats: Record<string, unknown>;
  /** 论断级核验明细（Issue #31 Phase 2）；缺省 = 未执行 */
  claim_checks?: ClaimCheckResult[];
  claim_stats?: ClaimStats;
  /** 研究空白矩阵（Issue #32 Phase 3）；旧报告缺省为 null */
  evidence_matrix?: EvidenceMatrix | null;
}

/** 任务创建时选择的模型快照（Issue #15 BYOK）；凭据删除后仍保留。 */
export interface ModelSelectionSnapshot {
  credential_id: string | null;
  name: string;
  provider: string;
  model: string;
  base_url: string | null;
  /** 创建任务时的上下文窗口声明；null = 当时未填写 */
  context_window?: number | null;
  /** 创建任务时的单次输出上限；null = 沿用厂商默认 */
  max_output_tokens?: number | null;
  is_byok: boolean;
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
  /** null = 平台默认模型（存量任务） */
  model?: ModelSelectionSnapshot | null;
  /** 回退平台后实际使用的厂商；null = 未发生过回退（Issue #28） */
  actual_provider?: string | null;
  actual_model?: string | null;
  created_at: string | null;
  updated_at: string | null;
}

/** 定时重跑排期（Issue #27）：每任务最多一条。 */
/** 定时增量监控的跨期状态记忆（Issue #42）。 */
export interface RoutineMemoryBrief {
  topic_summary?: string;
  open_gaps?: string[];
  recent_updates?: string[];
  stale_queries?: string[];
  updated_at?: string | null;
}

export interface TaskRoutine {
  task_id: string;
  enabled: boolean;
  cadence: "weekly" | "monthly";
  notify: "none" | "email";
  next_run_at: string;
  last_run_at: string | null;
  memory_brief?: RoutineMemoryBrief | null;
}

/** 保存排期的入参。 */
export interface TaskRoutineInput {
  enabled: boolean;
  cadence: "weekly" | "monthly";
  notify: "none" | "email";
}

/**
 * 一次增量刷新记录。
 *
 * `new_count=0 && status="completed"` 是「本期无新增」，不是失败；
 * `error` 非空但状态是 completed，说明部分检索式失败（其余照常汇总）。
 */
export interface TaskRefreshRun {
  id: string;
  trigger: "manual" | "schedule";
  status: "running" | "completed" | "failed";
  window_from: string | null;
  window_to: string | null;
  hits_total: number;
  new_count: number;
  duplicate_count: number;
  sources: Record<string, number>;
  summary_markdown: string | null;
  citation_ok: boolean;
  tokens_used: number;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string | null;
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
  /**
   * 向量索引是否**真的**完整（2026-10-23 起由后端实际计算）。
   *
   * 此前后端把它硬编码为 `true` —— 本会话已因同类谎言（数据库说就绪、
   * Chroma 只有 4 条向量）导致向量检索对全体用户静默失效。
   */
  vector_index_ready: boolean;
  /** 进程内 BM25 索引是否就绪（无法确认时后端返回 false）。 */
  bm25_index_ready: boolean;
  last_update?: string | null;
  /** 索引失败的文档数（需要重新上传）。 */
  index_failed_count?: number;
  /** 正在索引的文档数（只需等待）。 */
  indexing_count?: number;
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

/**
 * 单个样式的**真实渲染**预览（2026-10-26 样式页重设计）。
 *
 * 渲染在后端完成（与导出同一个 `CitationRenderer`），前端只负责展示。
 * 前端自写格式化器会与导出结果漂移 —— 用户会看到「预览长这样、下载出来不是」。
 */
export interface CitationStylePreview {
  style_id: string;
  title: string;
  source: CitationStyleSource;
  is_default: boolean;
  /** 按该样式渲染的参考文献条目（固定样例文献） */
  entries: string[];
  /** 正文行内标记：ref_id → 标记文本 */
  markers: Record<string, string>;
  /**
   * 渲染告警。**非空即表示走了兜底格式化器** —— 此时输出不代表该样式，
   * 前端必须显式提示，不能当正常预览展示。
   */
  warnings: string[];
}

export interface CitationStylePreviews {
  /** 顺序与 `CitationStyleList.styles` 一致 */
  previews: CitationStylePreview[];
  /** 样例文献条数（用于说明预览口径） */
  sample_count: number;
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

// ===== 引用库契约（与后端 app/api/v1/endpoints/reference_library.py 对齐，2026-10-26）=====

export interface LibraryItemOut {
  item_id: string;
  ref_id: string;
  title: string;
  authors: string[];
  year: number | null;
  container_title: string | null;
  doi: string | null;
  type: string;
}

export interface LibraryListOut {
  items: LibraryItemOut[];
  total: number;
}

export interface LibrarySummaryOut {
  total: number;
  /** 可作文献源的条数（入库准入已保证与 total 相等，分两字段为将来放宽留口） */
  usable_as_source: number;
}

export interface LibraryRejectedItem {
  ref_id: string;
  title: string;
  reason: string;
}

export interface LibrarySaveResponse {
  created: number;
  skipped: number;
  /**
   * 按归一化 DOI 合并进已有条目的数量（2026-10-26 遗留计划 §3.4）。
   *
   * 与 `skipped` 是**两种不同的去重**：
   * - `skipped`：`ref_id` 完全相同（重复导入同一份文件）；
   * - `merged`：`ref_id` 不同但归一化 DOI 相同（同一文献的不同写法）。
   */
  merged: number;
  /** 逐条拒绝原因 —— 必须展示，否则用户不知道哪些没进去 */
  rejected: LibraryRejectedItem[];
}


// ===== 引用真实性核验契约（Issue #3 前端展示；计划 2026-10-19-03b §1.2）=====

/** 单条参考文献的核验结论（与后端 `ReferenceVerdict` 对齐）。 */
export type VerificationStatus = "verified" | "metadata_mismatch" | "not_found" | "unverified";

/** 来源质量标记（Issue #31）：三项可同时存在。 */
export type QualityFlag = "retracted" | "preprint" | "low_citation";

/** 来源类型：同行评审 / 预印本 / 其他。 */
export type SourceType = "peer_reviewed" | "preprint" | "other";

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
  /** 来源质量计数（Issue #31）：旧报告可能没有这三个键，展示层按「未知」处理。 */
  retracted?: number;
  preprint?: number;
  low_citation?: number;
  intercepted: boolean;
}

/** 结果页参考文献条目；`verification_status` 为 `null` 表示未核验或值非法。 */
export interface BibliographyEntry {
  index: number;
  title: string;
  url: string | null;
  verification_status: VerificationStatus | null;
  /**
   * 撤稿状态（与四态核验正交）：true=已撤稿、false=来源未标记撤稿、null/缺失=未知。
   *
   * ⚠️ `false` 不等于「安全」：它只说明来源没有撤稿标记；`null` 表示来源不可用或无该字段。
   */
  is_retracted?: boolean | null;
  /** 来源质量标记（Issue #31）；缺省 = 未标记。 */
  quality_flags?: QualityFlag[];
  source_type?: SourceType;
  cited_by_count?: number | null;
  retraction_reason?: string | null;
  retraction_notice_doi?: string | null;
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
  /** 当前筛选者在本阶段的最新结论（`null` = 还没筛过） */
  current_verdict: string | null;
  /** 当前筛选者给出的排除理由 */
  current_exclusion_reason: string | null;
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

/**
 * 主动排序状态（与后端 `ScreeningPriorityOut.status` 对齐）。
 *
 * - `ready`：可以给出推荐顺序；
 * - `insufficient_labels`：任务内纳入/排除标签不足，后端不给排序；
 * - `no_candidates`：没有待筛候选。
 */
export type ScreeningPriorityStatus = "ready" | "insufficient_labels" | "no_candidates";

/** 推荐条目：只有记录 id 与名次，**不含模型分数/概率**。 */
export interface ScreeningPriorityItem {
  record_id: string;
  rank: number;
}

/** `GET /screening/priority` 响应：任务内主动排序（不自动纳入/排除）。 */
export interface ScreeningPriority {
  status: ScreeningPriorityStatus;
  algorithm: string;
  stage: string;
  items: ScreeningPriorityItem[];
  labels: { positive: number; negative: number; ignored: number };
  candidates: number;
  message: string;
}

// ===== 可观测性看板（Phase 2）=====

/** 最近任务条目（看板列表用） */
export interface ObservabilityRecentTask {
  task_id: string;
  /** 任务标题（没有标题时前端退回显示短 UUID）。2026-10-26 重设计新增。 */
  title?: string;
  status: string;
  used_tokens: number;
  duration_seconds: number;
  /** ISO 时间戳；判断「哪次运行」用。2026-10-26 重设计新增。 */
  created_at?: string | null;
}

/** 我的任务健康与成本概览（owner-scoped，只含自己的数据） */
export interface ObservabilityOverview {
  tasks: {
    total: number;
    completed: number;
    failed: number;
    cancelled: number;
    waiting_human: number;
  };
  /**
   * **全状态**计数（含 `waiting_approval` / `running` / `planning` 等）。
   *
   * 2026-10-26 重设计新增：旧的 `tasks` 只有 4 个固定状态，实测 demo 账号
   * 有 1 个 `waiting_approval` 不在其中 → 看板出现「总数 27 / 分布合计 26」，
   * 差值用户永远看不见。计数之和**恒等于** `tasks.total`。
   */
  by_status?: Record<string, number>;
  /**
   * 已进入终态的任务数（成功率的分母）。
   *
   * 2026-10-26 重设计新增：不给分母，用户无法核对 76.9% 是怎么来的。
   */
  terminal_count?: number;
  /** 成功率 = completed / 已进入终态的任务数（0 表示暂无终态任务） */
  success_rate: number;
  tokens: {
    total_used: number;
    avg_per_task: number;
    /**
     * 有实际用量记录（`used_tokens > 0`）的任务数。
     *
     * 2026-10-26 重设计新增：修复前落库的用量恒为 0，前端要靠它区分
     * 「真没用过」与「老数据没记」。平均值也只在有记录的任务之间计算。
     */
    recorded_task_count?: number;
  };
  recent: ObservabilityRecentTask[];
}

// ===== 团队工作区 + 席位（Phase 2）=====

/** 工作区角色（权限从高到低） */
export type WorkspaceRole = "owner" | "admin" | "member" | "viewer";

/** 工作区 */
export interface Workspace {
  workspace_id: string;
  name: string;
  slug: string;
  owner_id: string;
  seat_limit: number;
  /** 已用席位（owner 也占一个） */
  seat_used: number;
  /** 我在该工作区的角色；非成员时为 null */
  my_role: WorkspaceRole | null;
  created_at: string;
}

/** 成员 = 一个席位 */
export interface WorkspaceMember {
  user_id: string;
  username: string;
  email: string;
  role: WorkspaceRole;
  joined_at: string;
}

/** 一次性邀请码（只在创建时返回） */
export interface WorkspaceInvite {
  invite_id: string;
  code: string;
  role: WorkspaceRole;
  expires_at: string;
  workspace_id: string;
}

// ===== 导师批注（Phase 2）=====

export type AnnotationAnchorType = "task" | "paragraph" | "citation";

/** 批注 */
export interface Annotation {
  annotation_id: string;
  task_id: string;
  author_id: string;
  author_name: string | null;
  anchor_type: AnnotationAnchorType;
  anchor_key: string | null;
  content: string;
  resolved_at: string | null;
  created_at: string;
}

/** 段落批注的锚点匹配状态；matched=false 表示原文已变更 */
export interface ParagraphMatch {
  annotation_id: string;
  fingerprint: string;
  content: string;
  author_id: string;
  matched: boolean;
  paragraph_index: number | null;
  resolved: boolean;
}

// ===== 开放 API 与 MCP（Phase 2）=====

/** API Key 权限范围 */
export type ApiKeyScope =
  | 'tasks:read'
  | 'tasks:write'
  | 'artifacts:read'
  | 'annotations:write';

/** Key 元数据（**绝不含明文**） */
export interface ApiKeyInfo {
  key_id: string;
  name: string;
  /** 明文前缀（如 maa_7Kx9mQ2p），用于辨认是哪个脚本的 Key */
  prefix: string;
  scopes: ApiKeyScope[];
  last_used_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
  created_at: string;
}

/** 创建响应：**唯一**含明文的模型，key 只出现一次 */
export interface ApiKeyCreated extends ApiKeyInfo {
  key: string;
}

// ===== 计费套餐（Phase 2 最后一项）=====

/** 套餐（公开信息）。 */
export interface BillingPlan {
  plan_id: string;
  name: string;
  /** 每月篇数；-1 = 不限（机构版） */
  monthly_reviews: number;
  max_workspaces: number;
  max_seats_per_workspace: number;
  allow_api_access: boolean;
  allow_team: boolean;
  /** 月费（**分**）；0 = 免费。前端展示时除以 100 */
  price_cents: number;
  description: string | null;
}

/**
 * 我的用量 + **当前套餐权益**。
 *
 * ⚠️ 进度条直接用这三个字段，**不要自己算**：
 * 口径（UTC 自然月）在服务端定义，前端重算必然漂移。
 *
 * ## `period_key` 与 `period_end` 不是一回事（别混用）
 *
 * | 字段 | 含义 |
 * | --- | --- |
 * | `period_key` | **用量**周期（自然月，篇数按月重置），如 `2026-10` |
 * | `period_end` | **订阅**到期日（购买日 + N 天） |
 *
 * 只显示 `period_key` 会让用户以为「10 月底才续费」，而订阅可能 11 月到期。
 */
/** 单一来源（平台 / 自带）的本月任务数与 token 数。 */
export interface ModelUsageBucket {
  tasks: number;
  tokens: number;
}

/** 平台模型与自带模型的用量分账（Issue #15 P2）。 */
export interface ModelUsage {
  period_key: string;
  platform: ModelUsageBucket;
  byok: ModelUsageBucket;
  total: ModelUsageBucket;
}

/** 单个模型的估算成本（USD）。`total_cost` 为 null = 未收录单价。 */
export interface ModelCostRow {
  provider: string;
  model: string;
  is_byok: boolean;
  model_source: "task_snapshot" | "platform_default";
  tasks: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  input_cost: string | null;
  output_cost: string | null;
  total_cost: string | null;
  input_price_per_mtok: string | null;
  output_price_per_mtok: string | null;
  priced: boolean;
  split_estimated: boolean;
}

/** 按模型单价估算的成本（估算值，不是结算账单）。 */
export interface ModelCost {
  period_key: string;
  currency: string;
  total_cost: string;
  unpriced_tokens: number;
  split_estimated_tokens: number;
  models: ModelCostRow[];
}

export interface BillingUsage {
  /** 用量周期 YYYY-MM（UTC 年月） */
  period_key: string;
  reviews_used: number;
  /** 本月额度；-1 = 不限 */
  monthly_reviews: number;
  /** 剩余篇数；null = 不限 */
  reviews_remaining: number | null;
  plan_id: string;
  plan_name: string;

  // ===== 当前套餐权益（2026-10-26 重设计新增）=====
  // 「当前套餐」卡直接渲染这些字段，**不要**拿 plan_id 去 plans 列表里找：
  // 那等于前端维护第二份套餐配置，后端改权益时必然漂移。
  allow_team?: boolean;
  allow_api_access?: boolean;
  /** 可创建工作区上限 */
  max_workspaces?: number;
  /** 单工作区席位上限 */
  max_seats_per_workspace?: number;
  /** 月费（分）；0 = 免费 */
  price_cents?: number;

  // ===== 订阅信息（与用量周期区分）=====
  /** 订阅到期时间；免费档为 null（不是假日期） */
  period_end?: string | null;
  /** 已购席位数；免费档为 null */
  seats_purchased?: number | null;

  /** 模型用量分账；旧后端可能不返回 */
  model_usage?: ModelUsage | null;
}

/** 订阅信息（管理员开通后返回）。 */
export interface SubscriptionInfo {  subscription_id: string;
  user_id: string;
  plan_id: string;
  status: string;
  period_start: string;
  period_end: string;
  seats_purchased: number;
  /** manual / payment */
  source: string;
  note: string | null;
}

// ===== 用户自带模型凭据（Issue #15 BYOK）=====

/** 后端支持的厂商键；`custom` 表示任意 OpenAI 兼容端点。 */
export type LLMProviderKey =
  | "openai"
  | "deepseek"
  | "glm"
  | "qwen"
  | "siliconflow"
  | "moonshot"
  | "custom";

/** 厂商目录项（`GET /llm-credentials/providers`）。 */
export interface LLMProviderOption {
  provider: LLMProviderKey;
  label: string;
  /** 预置厂商由服务端写死；custom 为 null */
  base_url: string | null;
  requires_base_url: boolean;
}

/** 凭据元数据。**后端永不返回明文**，只有 masked_key。 */
export interface LLMCredentialInfo {
  credential_id: string;
  name: string;
  provider: LLMProviderKey;
  provider_label: string;
  model: string;
  base_url: string;
  /** 上下文窗口（token）；null = 未声明，界面显示「未设置」 */
  context_window: number | null;
  /** 单次输出上限（token）；null = 不传 max_tokens，沿用厂商默认 */
  max_output_tokens: number | null;
  /** 如 `sk-…abcd` */
  masked_key: string;
  /** 换 key 时 +1（前端不需要用，但可用于排查） */
  key_version: number;
  is_default: boolean;
  /** 凭据不可用（401/402/403）时是否回退平台模型（Issue #28） */
  allow_platform_fallback: boolean;
  status: "active" | "invalid" | "disabled";
  capabilities: Record<string, unknown>;
  last_verified_at: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}

/** 推理等级档位（label 展示 / value 下发给模型） */
export interface ReasoningTier {
  label: string;
  value: string;
}

/** 模型能力声明（凭据级，参照 DeepSeek Harness 模型目录的设计） */
export interface ModelCapabilities {
  text: boolean;
  /** 视觉模型才勾选；勾了才允许聊天里传图 */
  image: boolean;
  /** 生成能力探测（Issue #28）写入：true = 已验证能按指令输出可解析 JSON */
  json_mode: boolean;
  /** 空 = 不支持推理参数 */
  reasoning_tiers: ReasoningTier[];
}

export interface LLMCredentialCreate {
  name: string;
  provider: LLMProviderKey;
  model: string;
  /** 明文只进不出：提交后任何响应都不再包含它 */
  api_key: string;
  base_url?: string | null;
  /** 上下文窗口（token）；不填则声明未知 */
  context_window?: number | null;
  /** 单次输出上限（token）；不填则沿用厂商默认 */
  max_output_tokens?: number | null;
  is_default?: boolean;
  allow_platform_fallback?: boolean;
  capabilities?: ModelCapabilities;
}

export interface LLMCredentialUpdate {
  name?: string;
  model?: string;
  /** 留空表示不更换 */
  api_key?: string;
  base_url?: string;
  /** 传 null 表示清除该声明 */
  context_window?: number | null;
  max_output_tokens?: number | null;
  is_default?: boolean;
  allow_platform_fallback?: boolean;
  status?: "active" | "disabled";
  /** 整体替换能力声明；不传 = 不改 */
  capabilities?: ModelCapabilities;
}

/** 生成能力探测（Issue #28）：用用户 key 发一次 max_tokens=64 的真实请求。 */
export interface LLMGenerationProbeResult {
  ok: boolean;
  latency_ms: number;
  json_mode: boolean;
  error: string | null;
  raw: string | null;
  credential_status: "active" | "invalid" | "disabled" | null;
}

/** 测试连接：二选一——已保存凭据 id，或未保存的配置。 */
export interface LLMProbeRequest {
  credential_id?: string;
  provider?: LLMProviderKey;
  api_key?: string;
  base_url?: string | null;
}

/** 测试结果。**不含密钥**，只有状态、延迟与厂商模型 id 列表。 */
export interface LLMProbeResult {
  ok: boolean;
  latency_ms: number;
  models: string[];
  error: string | null;
  /** 测已保存凭据时回传最新状态；未保存配置为 null */
  credential_status: "active" | "invalid" | "disabled" | null;
}

/** 管理员侧 BYOK 渗透率看板（Issue #15 P2）。 */
export interface ByokTrendPoint {
  date: string;
  tasks: number;
  tokens: number;
  byok_tasks: number;
  byok_tokens: number;
  /** 当天全部任务的估算成本（USD） */
  cost: string;
  /** 当天 BYOK 任务的估算成本（USD） */
  byok_cost: string;
}

export interface ByokProviderRow {
  provider: string;
  model: string;
  tasks: number;
  tokens: number;
  /** BYOK 估算成本；null = 全部未收录单价 */
  cost: string | null;
  unpriced_tokens: number;
}

export interface ByokTopUser {
  user_id: string;
  username: string;
  tasks: number;
  tokens: number;
  byok_tasks: number;
  byok_tokens: number;
  byok_token_share: number;
  /** BYOK 估算成本；null = 全部未收录单价 */
  byok_cost: string | null;
}

export interface ByokCost {
  currency: string;
  byok: string;
  platform: string;
  total: string;
  unpriced_byok_tokens: number;
  unpriced_platform_tokens: number;
  split_estimated_tokens: number;
}

export interface ByokDormantUser {
  user_id: string;
  username: string;
  credentials: number;
  active_credentials: number;
  latest_credential_at: string | null;
  days_since_credential: number | null;
  platform_tasks: number;
  platform_tokens: number;
}

export interface ByokOverview {
  window_days: number;
  period_start: string;
  period_end: string;
  totals: {
    tasks: number;
    tokens: number;
    byok_tasks: number;
    byok_tokens: number;
    byok_task_share: number;
    byok_token_share: number;
  };
  users: {
    active: number;
    byok_active: number;
    with_credentials: number;
    with_active_credentials: number;
    byok_penetration: number;
    credential_penetration: number;
  };
  credentials: {
    total: number;
    active: number;
    invalid: number;
    disabled: number;
  };
  cost: ByokCost;
  trend: ByokTrendPoint[];
  providers: ByokProviderRow[];
  top_users: ByokTopUser[];
  dormant_count: number;
  dormant_users: ByokDormantUser[];
}
