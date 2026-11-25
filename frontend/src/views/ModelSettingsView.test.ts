import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

import ModelSettingsView from "@/views/ModelSettingsView.vue";

vi.mock("@/api/llm-credentials", () => ({
    listLLMCredentials: vi.fn(),
    listLLMProviders: vi.fn(),
    createLLMCredential: vi.fn(),
    updateLLMCredential: vi.fn(),
    deleteLLMCredential: vi.fn(),
    probeLLMCredential: vi.fn(),
    testLLMCredential: vi.fn(),
}));

const { updateNodeRouting } = vi.hoisted(() => ({
    updateNodeRouting: vi.fn(),
}));
vi.mock("@/api/llm-routing", async (importOriginal) => {
    const actual = await importOriginal<typeof import("@/api/llm-routing")>();
    return {
        ...actual,
        listNodeRouting: vi.fn().mockResolvedValue([]),
        updateNodeRouting,
    };
});

import {
    createLLMCredential,
    deleteLLMCredential,
    listLLMCredentials,
    listLLMProviders,
    probeLLMCredential,
    testLLMCredential,
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
    return wrapper
        .findAll("button")
        .find((button) => button.text().includes(text));
}

beforeEach(() => {
    vi.mocked(listLLMCredentials).mockReset();
    vi.mocked(listLLMProviders).mockReset();
    vi.mocked(createLLMCredential).mockReset();
    vi.mocked(updateLLMCredential).mockReset();
    vi.mocked(deleteLLMCredential).mockReset();
    vi.mocked(probeLLMCredential).mockReset();
    vi.mocked(testLLMCredential).mockReset();
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
        vi.mocked(listLLMCredentials).mockResolvedValue([
            credential(),
        ] as never);

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

        expect(wrapper.find('[data-test="models-error"]').text()).toContain(
            "后端 500",
        );
        expect(wrapper.find('[data-test="models-empty"]').exists()).toBe(false);
    });

    it("新增凭据：首条自动勾选「设为默认」并提交明文", async () => {
        vi.mocked(listLLMCredentials).mockResolvedValue([] as never);
        vi.mocked(createLLMCredential).mockResolvedValue(credential() as never);

        const wrapper = mountView();
        await flushPromises();
        await openCreate(wrapper);

        await wrapper
            .get('[data-test="model-name"]')
            .setValue("实验室共享 key");
        await wrapper.get('[data-test="model-id"]').setValue("deepseek-chat");
        await wrapper
            .get('[data-test="model-key"]')
            .setValue("sk-live-0123456789abcdef");
        await wrapper.get('[data-test="model-form"]').trigger("submit");
        await flushPromises();

        expect(createLLMCredential).toHaveBeenCalledWith({
            name: "实验室共享 key",
            provider: "deepseek",
            model: "deepseek-chat",
            api_key: "sk-live-0123456789abcdef",
            is_default: true,
            // 能力声明始终随创建提交（仅文本、无推理档位）；json_mode 由探测写入，未探测时为 false
            capabilities: { text: true, image: false, json_mode: false, reasoning_tiers: [] },
            // 未填容量 = 显式传 null（空串在 schema 层也按未填处理）
            context_window: null,
            max_output_tokens: null,
            // Issue #28：创建时始终带上平台回退开关，默认关闭
            allow_platform_fallback: false,
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
        await wrapper
            .get('[data-test="model-key"]')
            .setValue("sk-live-0123456789abcdef");
        await wrapper.get('[data-test="model-form"]').trigger("submit");
        await flushPromises();

        expect(wrapper.find('[data-test="models-error"]').text()).toContain(
            "base_url",
        );
        expect(createLLMCredential).not.toHaveBeenCalled();
    });

    it("设为默认调用 PATCH is_default", async () => {
        vi.mocked(listLLMCredentials).mockResolvedValue([
            credential({
                credential_id: "c2",
                is_default: false,
                name: "备用",
            }),
        ] as never);
        vi.mocked(updateLLMCredential).mockResolvedValue(credential() as never);

        const wrapper = mountView();
        await flushPromises();

        await wrapper
            .get('[data-test="model-set-default-c2"]')
            .trigger("click");
        await flushPromises();

        expect(updateLLMCredential).toHaveBeenCalledWith("c2", {
            is_default: true,
        });
    });

    it("删除需要二次确认：第一次点击不发请求", async () => {
        vi.mocked(listLLMCredentials).mockResolvedValue([
            credential(),
        ] as never);
        vi.mocked(deleteLLMCredential).mockResolvedValue(undefined);

        const wrapper = mountView();
        await flushPromises();

        await wrapper.get('[data-test="model-delete-c1"]').trigger("click");
        await flushPromises();
        expect(deleteLLMCredential).not.toHaveBeenCalled();
        expect(
            wrapper.get('[data-test="model-confirm-delete-c1"]').text(),
        ).toContain("确认删除");

        await wrapper
            .get('[data-test="model-confirm-delete-c1"]')
            .trigger("click");
        await flushPromises();

        expect(deleteLLMCredential).toHaveBeenCalledWith("c1");
    });

    it("编辑时厂商不可改、留空 key 表示不更换", async () => {
        vi.mocked(listLLMCredentials).mockResolvedValue([
            credential(),
        ] as never);
        vi.mocked(updateLLMCredential).mockResolvedValue(credential() as never);

        const wrapper = mountView();
        await flushPromises();

        await wrapper.get('[data-test="model-edit-c1"]').trigger("click");
        expect(
            wrapper.get('[data-test="model-provider"]').attributes("disabled"),
        ).toBeDefined();

        await wrapper.get('[data-test="model-name"]').setValue("改名后的 key");
        await wrapper.get('[data-test="model-form"]').trigger("submit");
        await flushPromises();

        const payload = vi.mocked(updateLLMCredential).mock.calls[0][1];
        expect(payload).toMatchObject({
            name: "改名后的 key",
            model: "deepseek-chat",
        });
        expect("api_key" in payload).toBe(false);
    });

    it("厂商目录加载失败不影响已有凭据展示", async () => {
        vi.mocked(listLLMCredentials).mockResolvedValue([
            credential(),
        ] as never);
        vi.mocked(listLLMProviders).mockRejectedValue(new Error("目录不可用"));

        const wrapper = mountView();
        await flushPromises();

        expect(wrapper.find('[data-test="model-row-c1"]').exists()).toBe(true);
        // 凭据列表正常时不该弹错误横幅（局部失败不连坐）
        expect(wrapper.find('[data-test="models-error"]').exists()).toBe(false);
    });

    it("表单测试连接成功后填充模型候选，并替用户填第一个模型", async () => {
        vi.mocked(listLLMCredentials).mockResolvedValue([] as never);
        vi.mocked(probeLLMCredential).mockResolvedValue({
            ok: true,
            latency_ms: 42,
            models: ["deepseek-chat", "deepseek-reasoner"],
            error: null,
            credential_status: null,
        });

        const wrapper = mountView();
        await flushPromises();
        await openCreate(wrapper);

        await wrapper
            .get('[data-test="model-key"]')
            .setValue("sk-live-0123456789abcdef");
        await wrapper.get('[data-test="model-probe"]').trigger("click");
        await flushPromises();

        // 只测配置，不落库
        expect(probeLLMCredential).toHaveBeenCalledWith({
            provider: "deepseek",
            api_key: "sk-live-0123456789abcdef",
        });
        expect(
            wrapper.get('[data-test="model-probe-result"]').text(),
        ).toContain("42ms");
        // 拉到模型后「模型名」变成真下拉，并替用户选第一个（省得手打错名字 404）
        const select = wrapper.get('[data-test="model-select"]')
            .element as HTMLSelectElement;
        expect(select.value).toBe("deepseek-chat");
        // 1 个「请选择」+ 2 个真实模型 + 1 个「自定义…」
        expect(
            wrapper.findAll('[data-test="model-select"] option'),
        ).toHaveLength(4);
        expect(createLLMCredential).not.toHaveBeenCalled();
    });

    it("下拉里选「自定义」切回手填，可返回下拉", async () => {
        vi.mocked(listLLMCredentials).mockResolvedValue([] as never);
        vi.mocked(probeLLMCredential).mockResolvedValue({
            ok: true,
            latency_ms: 42,
            models: ["deepseek-chat"],
            error: null,
            credential_status: null,
        });

        const wrapper = mountView();
        await flushPromises();
        await openCreate(wrapper);

        await wrapper
            .get('[data-test="model-key"]')
            .setValue("sk-live-0123456789abcdef");
        await wrapper.get('[data-test="model-probe"]').trigger("click");
        await flushPromises();

        // 厂商 /models 列不全时选「自定义」手填
        await wrapper.get('[data-test="model-select"]').setValue("__custom__");
        const input = wrapper.get('[data-test="model-id"]');
        await input.setValue("my-relay-model");
        expect(
            wrapper.find('[data-test="model-back-to-select"]').exists(),
        ).toBe(true);

        await wrapper.get('[data-test="model-name"]').setValue("中继模型");
        await wrapper
            .get('[data-test="model-key"]')
            .setValue("sk-live-0123456789abcdef");
        await wrapper.get('[data-test="model-form"]').trigger("submit");
        await flushPromises();

        expect(createLLMCredential).toHaveBeenCalledWith(
            expect.objectContaining({ model: "my-relay-model" }),
        );
    });

    it("预设按钮一键填上下文与最大输出，随凭据一起提交", async () => {
        vi.mocked(listLLMCredentials).mockResolvedValue([] as never);
        vi.mocked(createLLMCredential).mockResolvedValue(credential() as never);

        const wrapper = mountView();
        await flushPromises();
        await openCreate(wrapper);

        await wrapper.get('[data-test="model-name"]').setValue("长上下文模型");
        await wrapper
            .get('[data-test="model-id"]')
            .setValue("long-context-model");
        await wrapper
            .get('[data-test="model-key"]')
            .setValue("sk-live-0123456789abcdef");
        await wrapper.get('[data-test="model-preset-0"]').trigger("click");

        // 预设落到两个输入框（用户可再手改）
        expect(
            (
                wrapper.get('[data-test="model-context"]')
                    .element as HTMLInputElement
            ).value,
        ).toBe("1000000");
        expect(
            (
                wrapper.get('[data-test="model-output"]')
                    .element as HTMLInputElement
            ).value,
        ).toBe("128000");

        await wrapper.get('[data-test="model-form"]').trigger("submit");
        await flushPromises();

        expect(createLLMCredential).toHaveBeenCalledWith(
            expect.objectContaining({
                context_window: 1_000_000,
                max_output_tokens: 128_000,
            }),
        );
    });

    it("清空容量输入 = 显式清除（update 传 null）", async () => {
        vi.mocked(listLLMCredentials).mockResolvedValue([
            credential({ context_window: 200_000, max_output_tokens: 64_000 }),
        ] as never);
        vi.mocked(updateLLMCredential).mockResolvedValue(credential() as never);

        const wrapper = mountView();
        await flushPromises();
        await wrapper.get('[data-test="model-edit-c1"]').trigger("click");

        await wrapper.get('[data-test="model-context"]').setValue("");
        await wrapper.get('[data-test="model-output"]').setValue("");
        await wrapper.get('[data-test="model-form"]').trigger("submit");
        await flushPromises();

        expect(updateLLMCredential).toHaveBeenCalledWith(
            "c1",
            expect.objectContaining({
                context_window: null,
                max_output_tokens: null,
            }),
        );
    });

    it("表单测试连接失败时展示厂商错误，不填模型", async () => {
        vi.mocked(listLLMCredentials).mockResolvedValue([] as never);
        vi.mocked(probeLLMCredential).mockResolvedValue({
            ok: false,
            latency_ms: 30,
            models: [],
            error: "401 密钥被拒绝",
            credential_status: null,
        });

        const wrapper = mountView();
        await flushPromises();
        await openCreate(wrapper);

        await wrapper
            .get('[data-test="model-key"]')
            .setValue("sk-live-0123456789abcdef");
        await wrapper.get('[data-test="model-probe"]').trigger("click");
        await flushPromises();

        expect(
            wrapper.get('[data-test="model-probe-result"]').text(),
        ).toContain("401 密钥被拒绝");
        expect(
            (wrapper.get('[data-test="model-id"]').element as HTMLInputElement)
                .value,
        ).toBe("");
    });

    it("没填 key 就点测试：本地拦住，不发请求", async () => {
        vi.mocked(listLLMCredentials).mockResolvedValue([] as never);

        const wrapper = mountView();
        await flushPromises();
        await openCreate(wrapper);

        await wrapper.get('[data-test="model-probe"]').trigger("click");
        await flushPromises();

        expect(probeLLMCredential).not.toHaveBeenCalled();
        expect(wrapper.get('[data-test="model-probe-error"]').text()).toContain(
            "API Key",
        );
    });

    it("编辑态留空 key 时按 credential_id 测试（不发明文）", async () => {
        vi.mocked(listLLMCredentials).mockResolvedValue([
            credential(),
        ] as never);
        vi.mocked(probeLLMCredential).mockResolvedValue({
            ok: true,
            latency_ms: 20,
            models: ["deepseek-chat"],
            error: null,
            credential_status: "active",
        });

        const wrapper = mountView();
        await flushPromises();
        await wrapper.get('[data-test="model-edit-c1"]').trigger("click");
        await wrapper.get('[data-test="model-probe"]').trigger("click");
        await flushPromises();

        expect(probeLLMCredential).toHaveBeenCalledWith({
            credential_id: "c1",
        });
    });

    it("列表「测试」失败：报错并刷新（状态可能被标为 invalid）", async () => {
        vi.mocked(listLLMCredentials)
            .mockResolvedValueOnce([credential()] as never)
            .mockResolvedValueOnce([
                credential({ status: "invalid", last_error: "密钥被拒绝" }),
            ] as never);
        vi.mocked(testLLMCredential).mockResolvedValue({
            ok: false,
            latency_ms: 25,
            models: [],
            error: "密钥被拒绝",
            credential_status: "invalid",
        });

        const wrapper = mountView();
        await flushPromises();

        await wrapper.get('[data-test="model-test-c1"]').trigger("click");
        await flushPromises();

        expect(testLLMCredential).toHaveBeenCalledWith("c1");
        expect(wrapper.get('[data-test="models-error"]').text()).toContain(
            "密钥被拒绝",
        );
        expect(wrapper.get('[data-test="model-row-c1"]').text()).toContain(
            "校验失败",
        );
    });

    it("搜索按名称/厂商/模型过滤，并显示总数", async () => {
        vi.mocked(listLLMCredentials).mockResolvedValue([
            credential(),
            credential({
                credential_id: "c2",
                name: "备用 OpenAI",
                provider: "openai",
                provider_label: "OpenAI",
                model: "gpt-4o-mini",
                is_default: false,
            }),
        ] as never);

        const wrapper = mountView();
        await flushPromises();

        expect(wrapper.get('[data-test="model-count"]').text()).toContain(
            "共 2 把",
        );
        expect(wrapper.find('[data-test="model-row-c1"]').exists()).toBe(true);
        expect(wrapper.find('[data-test="model-row-c2"]').exists()).toBe(true);

        // 按厂商搜
        await wrapper.get('[data-test="model-search"]').setValue("openai");
        expect(wrapper.find('[data-test="model-row-c1"]').exists()).toBe(false);
        expect(wrapper.find('[data-test="model-row-c2"]').exists()).toBe(true);

        // 按模型名搜
        await wrapper.get('[data-test="model-search"]').setValue("deepseek");
        expect(wrapper.find('[data-test="model-row-c1"]').exists()).toBe(true);
        expect(wrapper.find('[data-test="model-row-c2"]').exists()).toBe(false);
    });

    it("搜索无结果时给出明确空态，而不是空白列表", async () => {
        vi.mocked(listLLMCredentials).mockResolvedValue([
            credential(),
        ] as never);

        const wrapper = mountView();
        await flushPromises();

        await wrapper
            .get('[data-test="model-search"]')
            .setValue("不存在的模型");

        expect(wrapper.find('[data-test="model-row-c1"]').exists()).toBe(false);
        const hint = wrapper.get('[data-test="models-no-match"]');
        expect(hint.text()).toContain("不存在的模型");
        // 总数仍显示真实条数，便于用户知道「不是被删了」
        expect(wrapper.get('[data-test="model-count"]').text()).toContain(
            "共 1 把",
        );
    });

    it("路由覆盖卡片使用全中文文案，且加载态下节点路由下拉框不为空白（Issue #56）", async () => {
        vi.mocked(listLLMCredentials).mockReturnValue(
            new Promise(() => undefined) as never,
        );
        const wrapper = mountView();

        // 加载态下每个节点路由下拉框默认选中空值「跟随任务勾选的模型」
        const firstSelect = wrapper.get('[data-test="routing-planner"] select')
            .element as HTMLSelectElement;
        expect(firstSelect.value).toBe("");

        const meter = wrapper.get('[data-test="context-meter"]').text();
        expect(meter).toContain("节点路由覆盖");
        expect(meter).toContain("已路由");
        expect(meter).toContain("个跟随任务");
        expect(meter).not.toContain("Routing coverage");
        expect(meter).not.toContain("nodes routed");
        expect(meter).not.toContain("follow task");
    });

    it("删除确认条位于 model-row 内部，支持取消且删除失败时不关闭", async () => {
        vi.mocked(listLLMCredentials).mockResolvedValue([credential()] as never);
        vi.mocked(deleteLLMCredential).mockRejectedValueOnce(new Error("删除受限"));

        const wrapper = mountView();
        await flushPromises();

        await wrapper.get('[data-test="model-delete-c1"]').trigger("click");
        await flushPromises();

        const row = wrapper.get('[data-test="model-row-c1"]');
        const confirmBtn = row.find('[data-test="model-confirm-delete-c1"]');
        const cancelBtn = row.find('[data-test="model-cancel-delete-c1"]');
        expect(confirmBtn.exists()).toBe(true);
        expect(cancelBtn.exists()).toBe(true);
        expect(row.text()).toContain("不影响历史任务");

        // 点击取消销毁确认条
        await cancelBtn.trigger("click");
        await flushPromises();
        expect(row.find('[data-test="model-confirm-delete-c1"]').exists()).toBe(false);
        expect(deleteLLMCredential).not.toHaveBeenCalled();

        // 再次点击删除并确认，失败时保留确认条且展示 ErrorBanner
        await wrapper.get('[data-test="model-delete-c1"]').trigger("click");
        await flushPromises();
        await wrapper.get('[data-test="model-confirm-delete-c1"]').trigger("click");
        await flushPromises();
        expect(wrapper.find('[data-test="models-error"]').text()).toContain("删除受限");
        expect(row.find('[data-test="model-confirm-delete-c1"]').exists()).toBe(true);
    });

    it("推理档位删除按钮不再使用裸 ✕ 文案", async () => {
        vi.mocked(listLLMCredentials).mockResolvedValue([] as never);
        const wrapper = mountView();
        await flushPromises();
        await openCreate(wrapper);

        await wrapper.get('[data-test="model-tier-add"]').trigger("click");
        const removeBtn = wrapper.get('button[aria-label="删除档位 1"]');
        expect(removeBtn.text()).not.toContain("✕");
    });

    it("保存节点路由失败显示 ErrorBanner(routing-error) 并可关闭，不再渲染成灰字 routing-message", async () => {
        vi.mocked(listLLMCredentials).mockResolvedValue([credential()] as never);
        updateNodeRouting.mockRejectedValueOnce(new Error("路由保存异常"));

        const wrapper = mountView();
        await flushPromises();

        await wrapper.get('[data-test="routing-save"]').trigger("click");
        await flushPromises();

        expect(wrapper.find('[data-test="routing-message"]').exists()).toBe(false);
        const err = wrapper.get('[data-test="routing-error"]');
        expect(err.text()).toContain("路由保存异常");

        await err.get("button").trigger("click");
        await flushPromises();
        updateNodeRouting.mockRejectedValueOnce(new Error("路由保存异常"));
        await wrapper.get('[data-test="routing-save"]').trigger("click");
        await flushPromises();
        expect(wrapper.get('[data-test="routing-error"]').text()).toContain("路由保存异常");
    });
});
