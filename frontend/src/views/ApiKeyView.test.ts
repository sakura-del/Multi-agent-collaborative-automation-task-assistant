import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

import ApiKeyView from "@/views/ApiKeyView.vue";

vi.mock("@/api/api-keys", () => ({
  listApiKeys: vi.fn(),
  createApiKey: vi.fn(),
  revokeApiKey: vi.fn(),
}));

import { createApiKey, listApiKeys, revokeApiKey } from "@/api/api-keys";

function keyInfo(overrides: Record<string, unknown> = {}) {
  return {
    key_id: "k1",
    name: "我的同步脚本",
    prefix: "maa_7Kx9mQ2p",
    scopes: ["tasks:read", "tasks:write"],
    last_used_at: null,
    expires_at: null,
    revoked_at: null,
    created_at: "2026-10-21T10:00:00Z",
    ...overrides,
  };
}

function mountView() {
  return mount(ApiKeyView);
}

beforeEach(() => {
  vi.mocked(listApiKeys).mockReset();
  vi.mocked(createApiKey).mockReset();
  vi.mocked(revokeApiKey).mockReset();
});

describe("ApiKeyView", () => {
  it("展示 Key 列表（前缀 + scope + 状态）", async () => {
    vi.mocked(listApiKeys).mockResolvedValue([keyInfo()] as never);

    const wrapper = mountView();
    await flushPromises();

    const row = wrapper.find('[data-test="apikey-row-k1"]');
    expect(row.exists()).toBe(true);
    expect(row.text()).toContain("我的同步脚本");
    expect(row.text()).toContain("maa_7Kx9mQ2p");
    expect(row.text()).toContain("tasks:read");
    expect(row.text()).toContain("可用");
  });

  it("空态给出「生成一个 Key」的引导", async () => {
    vi.mocked(listApiKeys).mockResolvedValue([] as never);

    const wrapper = mountView();
    await flushPromises();

    const empty = wrapper.find('[data-test="apikeys-empty"]');
    expect(empty.exists()).toBe(true);
    expect(empty.text()).toContain("生成");
  });

  it("加载失败显示错误而非空态", async () => {
    vi.mocked(listApiKeys).mockRejectedValue(new Error("后端 500"));

    const wrapper = mountView();
    await flushPromises();

    expect(wrapper.find('[data-test="apikeys-error"]').text()).toContain("后端 500");
    expect(wrapper.find('[data-test="apikeys-empty"]').exists()).toBe(false);
  });

  it("生成后展示明文并明确「只显示一次」", async () => {
    vi.mocked(listApiKeys).mockResolvedValue([] as never);
    vi.mocked(createApiKey).mockResolvedValue({
      ...keyInfo(),
      key: "maa_FULL_PLAINTEXT_VALUE_HERE",
    } as never);

    const wrapper = mountView();
    await flushPromises();

    await wrapper
      .findAll("button")
      .find((b) => b.text().includes("生成 Key"))!
      .trigger("click");

    await wrapper.get('[data-test="apikey-name"]').setValue("我的同步脚本");
    await wrapper.get('[data-test="apikey-create-form"]').trigger("submit");
    await flushPromises();

    const created = wrapper.find('[data-test="apikey-created"]');
    expect(created.exists()).toBe(true);
    expect(created.text()).toContain("maa_FULL_PLAINTEXT_VALUE_HERE");
    // 必须明确告知「只显示一次」，否则用户关掉后会反复寻找
    expect(created.text()).toContain("只显示这一次");
  });

  it("点「我已保存」后明文消失（此后无法再取）", async () => {
    vi.mocked(listApiKeys).mockResolvedValue([] as never);
    vi.mocked(createApiKey).mockResolvedValue({
      ...keyInfo(),
      key: "maa_FULL_PLAINTEXT_VALUE_HERE",
    } as never);

    const wrapper = mountView();
    await flushPromises();

    await wrapper
      .findAll("button")
      .find((b) => b.text().includes("生成 Key"))!
      .trigger("click");
    await wrapper.get('[data-test="apikey-name"]').setValue("k");
    await wrapper.get('[data-test="apikey-create-form"]').trigger("submit");
    await flushPromises();

    await wrapper.get('[data-test="apikey-dismiss"]').trigger("click");
    await flushPromises();

    expect(wrapper.find('[data-test="apikey-created"]').exists()).toBe(false);
    expect(wrapper.text()).not.toContain("maa_FULL_PLAINTEXT_VALUE_HERE");
  });

  it("默认勾选 tasks:read + tasks:write", async () => {
    vi.mocked(listApiKeys).mockResolvedValue([] as never);
    vi.mocked(createApiKey).mockResolvedValue({ ...keyInfo(), key: "maa_x" } as never);

    const wrapper = mountView();
    await flushPromises();

    await wrapper
      .findAll("button")
      .find((b) => b.text().includes("生成 Key"))!
      .trigger("click");

    expect(
      (wrapper.get('[data-test="apikey-scope-tasks:read"]').element as HTMLInputElement).checked
    ).toBe(true);
    expect(
      (wrapper.get('[data-test="apikey-scope-tasks:write"]').element as HTMLInputElement).checked
    ).toBe(true);
    // 默认不给产物下载权限（最小权限）
    expect(
      (wrapper.get('[data-test="apikey-scope-artifacts:read"]').element as HTMLInputElement).checked
    ).toBe(false);
  });

  it("取消勾选后提交的 scope 不含该项", async () => {
    vi.mocked(listApiKeys).mockResolvedValue([] as never);
    vi.mocked(createApiKey).mockResolvedValue({ ...keyInfo(), key: "maa_x" } as never);

    const wrapper = mountView();
    await flushPromises();

    await wrapper
      .findAll("button")
      .find((b) => b.text().includes("生成 Key"))!
      .trigger("click");
    await wrapper.get('[data-test="apikey-name"]').setValue("只读脚本");
    // 取消 tasks:write
    await wrapper.get('[data-test="apikey-scope-tasks:write"]').trigger("change");
    await wrapper.get('[data-test="apikey-create-form"]').trigger("submit");
    await flushPromises();

    expect(createApiKey).toHaveBeenCalledWith({
      name: "只读脚本",
      scopes: ["tasks:read"],
    });
  });

  it("一个 scope 都不选时拒绝提交", async () => {
    vi.mocked(listApiKeys).mockResolvedValue([] as never);

    const wrapper = mountView();
    await flushPromises();

    await wrapper
      .findAll("button")
      .find((b) => b.text().includes("生成 Key"))!
      .trigger("click");
    await wrapper.get('[data-test="apikey-name"]').setValue("k");
    await wrapper.get('[data-test="apikey-scope-tasks:read"]').trigger("change");
    await wrapper.get('[data-test="apikey-scope-tasks:write"]').trigger("change");
    await wrapper.get('[data-test="apikey-create-form"]').trigger("submit");
    await flushPromises();

    expect(createApiKey).not.toHaveBeenCalled();
    expect(wrapper.find('[data-test="apikeys-error"]').text()).toContain("权限范围");
  });

  it("吊销后刷新列表", async () => {
    vi.mocked(listApiKeys).mockResolvedValue([keyInfo()] as never);
    vi.mocked(revokeApiKey).mockResolvedValue(undefined as never);

    const wrapper = mountView();
    await flushPromises();

    vi.mocked(listApiKeys).mockResolvedValue([] as never);
    await wrapper.get('[data-test="apikey-revoke-k1"]').trigger("click");
    await flushPromises();

    expect(revokeApiKey).toHaveBeenCalledWith("k1");
    expect(wrapper.find('[data-test="apikey-row-k1"]').exists()).toBe(false);
  });

  it("已吊销的 Key 显示「已吊销」且不显示吊销按钮", async () => {
    vi.mocked(listApiKeys).mockResolvedValue([
      keyInfo({ revoked_at: "2026-10-21T12:00:00Z" }),
    ] as never);

    const wrapper = mountView();
    await flushPromises();

    const row = wrapper.find('[data-test="apikey-row-k1"]');
    expect(row.text()).toContain("已吊销");
    expect(wrapper.find('[data-test="apikey-revoke-k1"]').exists()).toBe(false);
  });

  it("过期的 Key 显示「已过期」", async () => {
    vi.mocked(listApiKeys).mockResolvedValue([
      keyInfo({ expires_at: "2020-01-01T00:00:00Z" }),
    ] as never);

    const wrapper = mountView();
    await flushPromises();

    expect(wrapper.find('[data-test="apikey-row-k1"]').text()).toContain("已过期");
  });
});
