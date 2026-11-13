import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

import ModelSettingsView from "@/views/ModelSettingsView.vue";

vi.mock("@/api/llm-credentials", () => ({
  listLLMCredentials: vi.fn(),
  listLLMProviders: vi.fn(),
  createLLMCredential: vi.fn(),
  updateLLMCredential: vi.fn(),
  deleteLLMCredential: vi.fn(),
}));

import {
  createLLMCredential,
  deleteLLMCredential,
  listLLMCredentials,
  listLLMProviders,
  updateLLMCredential,
} from "@/api/llm-credentials";

const PROVIDERS = [
  {
    provider: "deepseek",
    label: "DeepSeek",
    base_url: "https://api.deepseek.com/v1",
    requires_base_url: false,
  },
  {
    provider: "openai",
    label: "OpenAI",
    base_url: "https://api.openai.com/v1",
    requires_base_url: false,
  },
  {
    provider: "custom",
    label: "自定义（OpenAI 兼容）",
    base_url: null,
    requires_base_url: true,
  },
];

function credential(overrides: Record<string, unknown> = {}) {
  return {
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
    ...overrides,
  };
}

function mountView() {
  return mount(ModelSettingsView);
}

async function openCreate(wrapper: ReturnType<typeof mountView>) {
  await wrapper
    .findAll("button")
    .find((button) => button.text().includes("添加模型"))!
    .trigger("click");
}

function findButton(wrapper: ReturnType<typeof mountView>, text: string) {
  return wrapper.findAll("button").find((button) => button.text().includes(text));
}

beforeEach(() => {
  vi.mocked(listLLMCredentials).mockReset();
  vi.mocked(listLLMProviders).mockReset();
  vi.mocked(createLLMCredential).mockReset();
  vi.mocked(updateLLMCredential).mockReset();
  vi.mocked(deleteLLMCredential).mockReset();
  vi.mocked(listLLMProviders).mockResolvedValue(PROVIDERS as never);
});

describe("ModelSettingsView", () => {
  it("空态说明「不添加也能用平台默认模型」", async () => {
    vi.mocked(listLLMCredentials).mockResolvedValue([] as never);

    const wrapper = mountView();
    await flushPromises();

    const empty = wrapper.find('[data-test="models-empty"]');
    expect(empty.exists()).toBe(true);
    expect(empty.text()).toContain("平台默认模型");
  });

  it("列表展示名称、厂商、模型、脱敏 key 与默认标记", async () => {
    vi.mocked(listLLMCredentials).mockResolvedValue([credential()] as never);

    const wrapper = mountView();
    await flushPromises();

    const row = wrapper.find('[data-test="model-row-c1"]');
    expect(row.exists()).toBe(true);
    expect(row.text()).toContain("我的 DeepSeek");
    expect(row.text()).toContain("DeepSeek");
    expect(row.text()).toContain("deepseek-chat");
    expect(row.text()).toContain("sk-…cdef");
    expect(row.text()).toContain("默认");
  });

  it("加载失败显示错误，而不是空态", async () => {
    vi.mocked(listLLMCredentials).mockRejectedValue(new Error("后端 500"));

    const wrapper = mountView();
    await flushPromises();

    expect(wrapper.find('[data-test="models-error"]').text()).toContain("后端 500");
    expect(wrapper.find('[data-test="models-empty"]').exists()).toBe(false);
  });

  it("新增凭据：首条自动勾选「设为默认」并提交明文", async () => {
    vi.mocked(listLLMCredentials).mockResolvedValue([] as never);
    vi.mocked(createLLMCredential).mockResolvedValue(credential() as never);

    const wrapper = mountView();
    await flushPromises();
    await openCreate(wrapper);

    await wrapper.get('[data-test="model-name"]').setValue("实验室共享 key");
    await wrapper.get('[data-test="model-id"]').setValue("deepseek-chat");
    await wrapper.get('[data-test="model-key"]').setValue("sk-live-0123456789abcdef");
    await wrapper.get('[data-test="model-form"]').trigger("submit");
    await flushPromises();

    expect(createLLMCredential).toHaveBeenCalledWith({
      name: "实验室共享 key",
      provider: "deepseek",
      model: "deepseek-chat",
      api_key: "sk-live-0123456789abcdef",
      is_default: true,
    });
  });

  it("自定义端点缺 base_url 时前端就拦住（不发请求）", async () => {
    vi.mocked(listLLMCredentials).mockResolvedValue([] as never);

    const wrapper = mountView();
    await flushPromises();
    await openCreate(wrapper);

    await wrapper.get('[data-test="model-name"]').setValue("中继站");
    await wrapper.get('[data-test="model-provider"]').setValue("custom");
    await wrapper.get('[data-test="model-id"]').setValue("gpt-4o-mini");
    await wrapper.get('[data-test="model-key"]').setValue("sk-live-0123456789abcdef");
    await wrapper.get('[data-test="model-form"]').trigger("submit");
    await flushPromises();

    expect(wrapper.find('[data-test="models-error"]').text()).toContain("base_url");
    expect(createLLMCredential).not.toHaveBeenCalled();
  });

  it("设为默认调用 PATCH is_default", async () => {
    vi.mocked(listLLMCredentials).mockResolvedValue([
      credential({ credential_id: "c2", is_default: false, name: "备用" }),
    ] as never);
    vi.mocked(updateLLMCredential).mockResolvedValue(credential() as never);

    const wrapper = mountView();
    await flushPromises();

    await wrapper.get('[data-test="model-set-default-c2"]').trigger("click");
    await flushPromises();

    expect(updateLLMCredential).toHaveBeenCalledWith("c2", { is_default: true });
  });

  it("删除需要二次确认：第一次点击不发请求", async () => {
    vi.mocked(listLLMCredentials).mockResolvedValue([credential()] as never);
    vi.mocked(deleteLLMCredential).mockResolvedValue(undefined);

    const wrapper = mountView();
    await flushPromises();

    await wrapper.get('[data-test="model-delete-c1"]').trigger("click");
    await flushPromises();
    expect(deleteLLMCredential).not.toHaveBeenCalled();
    expect(wrapper.get('[data-test="model-confirm-delete-c1"]').text()).toContain("确认删除");

    await wrapper.get('[data-test="model-confirm-delete-c1"]').trigger("click");
    await flushPromises();

    expect(deleteLLMCredential).toHaveBeenCalledWith("c1");
  });

  it("编辑时厂商不可改、留空 key 表示不更换", async () => {
    vi.mocked(listLLMCredentials).mockResolvedValue([credential()] as never);
    vi.mocked(updateLLMCredential).mockResolvedValue(credential() as never);

    const wrapper = mountView();
    await flushPromises();

    await wrapper.get('[data-test="model-edit-c1"]').trigger("click");
    expect(wrapper.get('[data-test="model-provider"]').attributes("disabled")).toBeDefined();

    await wrapper.get('[data-test="model-name"]').setValue("改名后的 key");
    await wrapper.get('[data-test="model-form"]').trigger("submit");
    await flushPromises();

    const payload = vi.mocked(updateLLMCredential).mock.calls[0][1];
    expect(payload).toMatchObject({ name: "改名后的 key", model: "deepseek-chat" });
    expect("api_key" in payload).toBe(false);
  });

  it("厂商目录加载失败不影响已有凭据展示", async () => {
    vi.mocked(listLLMCredentials).mockResolvedValue([credential()] as never);
    vi.mocked(listLLMProviders).mockRejectedValue(new Error("目录不可用"));

    const wrapper = mountView();
    await flushPromises();

    expect(wrapper.find('[data-test="model-row-c1"]').exists()).toBe(true);
    // 凭据列表正常时不该弹错误横幅（局部失败不连坐）
    expect(wrapper.find('[data-test="models-error"]').exists()).toBe(false);
  });
});
