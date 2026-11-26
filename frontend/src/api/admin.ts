import api from "./client";
import type { ByokOverview } from "./types";

/** 管理员侧 BYOK 渗透率看板（admin only）。 */
export async function getByokOverview(days: number): Promise<ByokOverview> {
  const response = await api.get<ByokOverview>("/admin/byok/overview", {
    params: { days },
  });
  return response.data;
}

// ===== 后台管理控制台（Issue #12）=====

export interface AdminOverview {
  days: number;
  users: { total: number; new_in_window: number; active_in_window: number };
  tasks: {
    total: number;
    created_in_window: number;
    by_status: Record<string, number>;
    failed_in_window: number;
    finished_in_window: number;
    failure_rate: number;
    pending_action: number;
  };
  tokens_used_in_window: number;
  byok_tasks_in_window: number;
  byok_ratio: number;
  active_credentials: number;
}

export interface AdminTaskRow {
  task_id: string;
  title: string;
  status: string;
  owner_id: string;
  owner_username: string;
  is_byok: boolean;
  model_label: string | null;
  used_tokens: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface AdminTaskPage {
  total: number;
  items: AdminTaskRow[];
}

export interface AdminUserRow {
  user_id: string;
  username: string;
  email: string;
  role: string;
  is_active: boolean;
  created_at: string | null;
  plan_name: string | null;
  task_count: number;
  credential_count: number;
}

export interface AdminUserPage {
  total: number;
  items: AdminUserRow[];
}

export interface AdminFunnelOut {
  days: number;
  landing_visitors: number;
  demo_views: number;
  register_views: number;
  registered_users: number;
  task_creators: number;
  task_completed_users: number;
  demo_task_clicks: number;
}

export interface AdminFeedbackRow {
  id: string;
  user_id: string;
  username: string;
  email: string;
  category: string;
  content: string;
  page_path: string | null;
  status: string;
  created_at: string | null;
}

export interface AdminFeedbackPage {
  total: number;
  items: AdminFeedbackRow[];
}

/** 后台 KPI 概览（窗口内聚合，一次请求拿全） */
export async function getAdminOverview(days: number): Promise<AdminOverview> {
  const response = await api.get<AdminOverview>("/admin/console/overview", {
    params: { days },
  });
  return response.data;
}

/** 跨用户任务列表（只读；筛选/分页都在服务端） */
export async function listAdminTasks(params: {
  status?: string;
  q?: string;
  limit?: number;
  offset?: number;
}): Promise<AdminTaskPage> {
  const response = await api.get<AdminTaskPage>("/admin/console/tasks", {
    params: {
      ...(params.status ? { status: params.status } : {}),
      ...(params.q ? { q: params.q } : {}),
      limit: params.limit ?? 20,
      offset: params.offset ?? 0,
    },
  });
  return response.data;
}

/** 用户列表（含套餐与用量计数） */
export async function listAdminUsers(params: {
  q?: string;
  role?: "admin" | "user";
  limit?: number;
  offset?: number;
}): Promise<AdminUserPage> {
  const response = await api.get<AdminUserPage>("/admin/console/users", {
    params: {
      ...(params.q ? { q: params.q } : {}),
      ...(params.role ? { role: params.role } : {}),
      limit: params.limit ?? 20,
      offset: params.offset ?? 0,
    },
  });
  return response.data;
}

/** 推广漏斗转化统计 */
export async function getAdminFunnel(days: number): Promise<AdminFunnelOut> {
  const response = await api.get<AdminFunnelOut>("/admin/console/funnel", {
    params: { days },
  });
  return response.data;
}

/** 站内用户反馈列表 */
export async function listAdminFeedback(params?: {
  limit?: number;
  offset?: number;
}): Promise<AdminFeedbackPage> {
  const response = await api.get<AdminFeedbackPage>("/admin/console/feedback", {
    params: {
      limit: params?.limit ?? 20,
      offset: params?.offset ?? 0,
    },
  });
  return response.data;
}
