import api from "./client";
import type { ObservabilityOverview } from "./types";

// 可观测性看板 API（Phase 2）
// Bearer 鉴权由 client.ts 的请求拦截器统一注入；后端 owner-scoped，
// 返回的永远是当前用户自己的数据。

/** 取当前用户的健康与成本概览。 */
export async function getObservabilityOverview(): Promise<ObservabilityOverview> {
  const response = await api.get<ObservabilityOverview>("/observability/overview");
  return response.data;
}
