import api from "./client";
import type { BillingPlan, BillingUsage, SubscriptionInfo } from "./types";

/**
 * 计费套餐 API（Phase 2 最后一项）。
 *
 * ⚠️ 后端区分两类 429：
 * - `PLAN_QUOTA_EXCEEDED` → 本月篇数用尽（**升级套餐**或等下月）
 * - `TOKEN_BUDGET_EXCEEDED` → 今日 token 预算用尽（**明天再试**，买套餐也没用）
 *
 * 前端必须按 `error_code` 分别提示，不能都显示「额度不足」。
 */

/** 我的套餐与本月用量。 */
export async function getBillingUsage(): Promise<BillingUsage> {
  const response = await api.get<BillingUsage>("/billing/usage");
  return response.data;
}

/** 套餐列表（**公开**，登录前也可调用）。 */
export async function listBillingPlans(): Promise<BillingPlan[]> {
  const response = await api.get<BillingPlan[]>("/billing/plans");
  return response.data;
}

/** 手工开通/变更套餐（**仅管理员**）。 */
export async function subscribePlan(payload: {
  user_id: string;
  plan_id: string;
  note?: string;
  days?: number;
  seats?: number;
}): Promise<SubscriptionInfo> {
  const response = await api.post<SubscriptionInfo>("/billing/subscribe", payload);
  return response.data;
}
