import { describe, expect, it, vi } from "vitest";

import {
  createLLMCredential,
  deleteLLMCredential,
  listLLMCredentials,
  listLLMProviders,
  updateLLMCredential,
} from "@/api/llm-credentials";

vi.mock("@/api/client", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

import api from "@/api/client";

const CREDENTIAL = {
  credential_id: "c1",
  name: "我的 DeepSeek",
  provider: "deepseek",
  provider_label: "DeepSeek",
  model: "deepseek-chat",
  base_url: "https://api.deepseek.com/v1",
  masked_key: "sk-…cdef",
  key_version: 1,
  is_default: true,
  status: "active",
  capabilities: {},
  last_verified_at: null,
  last_error: null,
  created_at: "2026-11-13T10:00:00Z",
  updated_at: "2026-11-13T10:00:00Z",
};

describe("llm-credentials api", () => {
  it("listLLMProviders 走独立目录端点", async () => {
    vi.mocked(api.get).mockResolvedValue({ status: 200, data: [] });

    await listLLMProviders();

    expect(api.get).toHaveBeenCalledWith("/llm-credentials/providers");
  });

  it("listLLMCredentials 只返回元数据（无明文）", async () => {
    vi.mocked(api.get).mockResolvedValue({ status: 200, data: [CREDENTIAL] });

    const items = await listLLMCredentials();

    expect(api.get).toHaveBeenCalledWith("/llm-credentials");
    expect(items[0].masked_key).toBe("sk-…cdef");
    expect("api_key" in items[0]).toBe(false);
  });

  it("createLLMCredential 把明文放在请求体里", async () => {
    vi.mocked(api.post).mockResolvedValue({ status: 201, data: CREDENTIAL });

    await createLLMCredential({
      name: "我的 DeepSeek",
      provider: "deepseek",
      model: "deepseek-chat",
      api_key: "sk-live-0123456789abcdef",
      is_default: true,
    });

    expect(api.post).toHaveBeenCalledWith("/llm-credentials", {
      name: "我的 DeepSeek",
      provider: "deepseek",
      model: "deepseek-chat",
      api_key: "sk-live-0123456789abcdef",
      is_default: true,
    });
  });

  it("updateLLMCredential 用 PATCH，且 id 经过编码", async () => {
    vi.mocked(api.patch).mockResolvedValue({ status: 200, data: CREDENTIAL });

    await updateLLMCredential("c 1", { is_default: true });

    expect(api.patch).toHaveBeenCalledWith("/llm-credentials/c%201", { is_default: true });
  });

  it("deleteLLMCredential 用 DELETE", async () => {
    vi.mocked(api.delete).mockResolvedValue({ status: 204, data: null });

    await deleteLLMCredential("c1");

    expect(api.delete).toHaveBeenCalledWith("/llm-credentials/c1");
  });
});
