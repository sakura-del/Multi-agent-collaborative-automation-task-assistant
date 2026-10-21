import api from "./client";
import type { ApiKeyCreated, ApiKeyInfo, ApiKeyScope } from "./types";

// 开放 API Key 管理（Phase 2）
// Bearer 鉴权由 client.ts 的请求拦截器统一注入。
//
// ⚠️ 这些端点**不接受 X-API-Key**（Key 不能生成新 Key，否则是权限提升）。

/** 列出我的 Key（默认不含已吊销） */
export async function listApiKeys(includeRevoked = false): Promise<ApiKeyInfo[]> {
  const params = includeRevoked ? "?include_revoked=true" : "";
  const response = await api.get<ApiKeyInfo[]>(`/api-keys${params}`);
  return response.data;
}

/**
 * 生成 Key。
 *
 * 响应里的 `key` 字段**只出现这一次** —— 调用方必须立刻展示给用户并提示保存，
 * 之后任何接口都拿不到明文（数据库只有哈希）。
 */
export async function createApiKey(payload: {
  name: string;
  scopes?: ApiKeyScope[];
  expires_in_days?: number;
}): Promise<ApiKeyCreated> {
  const response = await api.post<ApiKeyCreated>("/api-keys", payload);
  return response.data;
}

/** 吊销 Key（软删除，重复吊销幂等） */
export async function revokeApiKey(keyId: string): Promise<void> {
  await api.delete(`/api-keys/${encodeURIComponent(keyId)}`);
}
