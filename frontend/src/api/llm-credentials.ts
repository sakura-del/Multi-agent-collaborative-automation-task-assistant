import api from "./client";
import type {
  LLMCredentialCreate,
  LLMCredentialInfo,
  LLMCredentialUpdate,
  LLMGenerationProbeResult,
  LLMProbeRequest,
  LLMProbeResult,
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

/**
 * 测试连接并拉取厂商模型列表。
 *
 * 两种用法：
 * - 传 `credential_id`：测已保存的凭据（前端只有密文句柄，明文不出后端）；
 * - 传 `provider + api_key (+ base_url)`：测还没保存的配置。
 *
 * 这是唯一会用用户 key 主动外呼的接口，后端必须走 pin 传输层。
 */
export async function probeLLMCredential(payload: LLMProbeRequest): Promise<LLMProbeResult> {
  const response = await api.post<LLMProbeResult>("/llm-credentials/probe", payload);
  return response.data;
}

/**
 * 生成能力探测（Issue #28）：验证能否按指令输出可解析 JSON。
 *
 * ⚠️ 会用用户的 key 发一次真实请求（max_tokens=64，约 100 token），
 * UI 必须在按钮旁说明。结果写入 capabilities.json_mode，
 * planner 路由保存时据此放行。
 */
export async function probeGeneration(credentialId: string): Promise<LLMGenerationProbeResult> {
  const response = await api.post<LLMGenerationProbeResult>(
    `/llm-credentials/${encodeURIComponent(credentialId)}/probe-generation`
  );
  return response.data;
}

/** 测试已保存凭据（列表页「测试」按钮；失败会累加计数，达阈值自动停用） */
export async function testLLMCredential(credentialId: string): Promise<LLMProbeResult> {
  const response = await api.post<LLMProbeResult>(
    `/llm-credentials/${encodeURIComponent(credentialId)}/test`
  );
  return response.data;
}
