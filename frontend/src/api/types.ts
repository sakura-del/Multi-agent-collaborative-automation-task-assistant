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
  citation_style?: "gbt7714" | "apa";
  document_ids?: string[];
  offline_dataset?: boolean;
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
