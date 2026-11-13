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
  /**
   * 用用户自己的引用库作为文献源（2026-10-26 引用库计划）。
   * `true` 时检索节点从引用库取文献、完全不外呼。
   */
  reference_library?: boolean;
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
  /**
   * 撤稿状态（与四态核验正交）：true=已撤稿、false=来源未标记撤稿、null/缺失=未知。
   *
   * ⚠️ `false` 不等于「安全」：它只说明来源没有撤稿标记；`null` 表示来源不可用或无该字段。
   */
  is_retracted?: boolean | null;
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
