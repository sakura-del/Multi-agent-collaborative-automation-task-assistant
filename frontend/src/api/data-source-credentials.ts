import api from "./client";
import type { DataSourceCredentialInfo } from "./types";

// 用户自带数据源凭据（Issue #67 万方 BYOK）。
//
// 安全契约（与 LLM BYOK 一致）：
// - 明文 api_key 只出现在 PUT 的**请求体**里，任何响应都不含它；
// - GET 只返回脱敏掩码 `38704_****02cc`；
// - 全部端点强制 JWT（Key 能花用户自己的钱）。

/** 列出全部受支持数据源的配置状态（未配置也会返回，configured=false） */
export async function listDataSourceCredentials(): Promise<DataSourceCredentialInfo[]> {
  const response = await api.get<DataSourceCredentialInfo[]>("/data-source-credentials");
  return response.data;
}

/** 保存/覆盖某个数据源的 App Key */
export async function saveDataSourceCredential(
  provider: string,
  apiKey: string,
): Promise<DataSourceCredentialInfo> {
  const response = await api.put<DataSourceCredentialInfo>(
    `/data-source-credentials/${encodeURIComponent(provider)}`,
    { api_key: apiKey },
  );
  return response.data;
}

/** 删除某个数据源的 App Key（幂等） */
export async function deleteDataSourceCredential(provider: string): Promise<void> {
  await api.delete(`/data-source-credentials/${encodeURIComponent(provider)}`);
}
