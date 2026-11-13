import api from "./client";
import type {
  LLMCredentialCreate,
  LLMCredentialInfo,
  LLMCredentialUpdate,
  LLMProviderOption,
} from "./types";

// 用户自带模型凭据（Issue #15 BYOK）。
//
// 安全契约：
// - 明文 api_key 只在 create/update 的**请求体**里出现，任何响应都不含它；
// - 列表只返回 masked_key（`sk-…abcd`）；
// - 全部端点强制 JWT，不接受开放 API Key（凭据能花用户自己的钱）。

/** 厂商目录（下拉框数据源；不含任何密钥） */
export async function listLLMProviders(): Promise<LLMProviderOption[]> {
  const response = await api.get<LLMProviderOption[]>("/llm-credentials/providers");
  return response.data;
}

/** 列出我的凭据（默认在前） */
export async function listLLMCredentials(): Promise<LLMCredentialInfo[]> {
  const response = await api.get<LLMCredentialInfo[]>("/llm-credentials");
  return response.data;
}

/** 新增凭据；首条会自动成为默认 */
export async function createLLMCredential(
  payload: LLMCredentialCreate
): Promise<LLMCredentialInfo> {
  const response = await api.post<LLMCredentialInfo>("/llm-credentials", payload);
  return response.data;
}

/** 局部更新（改名/换模型/换 key/设默认/停用） */
export async function updateLLMCredential(
  credentialId: string,
  payload: LLMCredentialUpdate
): Promise<LLMCredentialInfo> {
  const response = await api.patch<LLMCredentialInfo>(
    `/llm-credentials/${encodeURIComponent(credentialId)}`,
    payload
  );
  return response.data;
}

/** 删除凭据（物理删除密文；历史任务仍保留模型快照） */
export async function deleteLLMCredential(credentialId: string): Promise<void> {
  await api.delete(`/llm-credentials/${encodeURIComponent(credentialId)}`);
}
