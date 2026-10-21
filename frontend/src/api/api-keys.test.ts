import { describe, expect, it, vi } from "vitest";

import { createApiKey, listApiKeys, revokeApiKey } from "@/api/api-keys";

vi.mock("@/api/client", () => ({
  default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));

import api from "@/api/client";

const KEY_INFO = {
  key_id: "k1",
  name: "我的同步脚本",
  prefix: "maa_7Kx9mQ2p",
  scopes: ["tasks:read", "tasks:write"],
  last_used_at: null,
  expires_at: null,
  revoked_at: null,
  created_at: "2026-10-21T10:00:00Z",
};

describe("api-keys api", () => {
  it("listApiKeys 默认不含已吊销", async () => {
    vi.mocked(api.get).mockResolvedValue({ status: 200, data: [KEY_INFO] });

    const result = await listApiKeys();

    expect(api.get).toHaveBeenCalledWith("/api-keys");
    expect(result).toEqual([KEY_INFO]);
  });

  it("listApiKeys(true) 带 include_revoked 参数", async () => {
    vi.mocked(api.get).mockResolvedValue({ status: 200, data: [] });

    await listApiKeys(true);

    expect(api.get).toHaveBeenCalledWith("/api-keys?include_revoked=true");
  });

  it("createApiKey 返回含明文的响应", async () => {
    vi.mocked(api.post).mockResolvedValue({
      status: 201,
      data: { ...KEY_INFO, key: "maa_FULL_PLAINTEXT" },
    });

    const created = await createApiKey({
      name: "我的同步脚本",
      scopes: ["tasks:read"],
    });

    expect(api.post).toHaveBeenCalledWith("/api-keys", {
      name: "我的同步脚本",
      scopes: ["tasks:read"],
    });
    // 明文只在这一刻能拿到
    expect(created.key).toBe("maa_FULL_PLAINTEXT");
  });

  it("revokeApiKey 用 encodeURIComponent 拼路径", async () => {
    vi.mocked(api.delete).mockResolvedValue({ status: 204, data: null });

    await revokeApiKey("k/1");

    expect(api.delete).toHaveBeenCalledWith("/api-keys/k%2F1");
  });
});
