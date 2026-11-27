import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

import TaskCreateForm from "@/components/tasks/TaskCreateForm.vue";
import type { CitationStyleOut } from "@/api/types";

// 样式下拉走真实 api 模块（列表接口由 client mock 提供），与 DocumentUpload 测试同风格
vi.mock("@/api/client", () => ({
    default: {
        get: vi.fn(),
        post: vi.fn(),
        put: vi.fn(),
        delete: vi.fn(),
    },
}));

import api from "@/api/client";
import { saveTaskDraft, taskDraftStorageKey } from "@/lib/task-draft";

// 表单只负责校验与 payload 组装：提交动作由父组件（TaskListView）发起
function mountForm(props: Record<string, unknown> = {}) {
    return mount(TaskCreateForm, {
        props: { pending: false, error: null, documents: [], ...props },
    });
}

function apiStyleRow(
    overrides: Partial<CitationStyleOut> = {},
): CitationStyleOut {
    return {
        style_id: "gbt7714-2025-numeric",
        title: "GB/T 7714—2025（顺序编码制）",
        numbering: "citation-number",
        ordering: "citation-order",
        default_locale: "zh-CN",
        source: "preset",
        is_default: true,
        ...overrides,
    };
}

async function fillValidForm(wrapper: ReturnType<typeof mountForm>) {
    await wrapper
        .find('[data-test="topic"]')
        .setValue("检索增强生成与多智能体协作的质量评估");
    await wrapper.find('[data-test="keyword-input"]').setValue("RAG");
    await wrapper.find('[data-test="keyword-input"]').trigger("keydown.enter");
    await wrapper.find('[data-test="keyword-input"]').setValue("multi-agent");
    await wrapper.find('[data-test="keyword-input"]').trigger("keydown.enter");
}

describe("TaskCreateForm", () => {
    beforeEach(() => {
        // 默认让样式列表不可用：既有用例因此始终看到内置两项的降级形态
        vi.mocked(api.get).mockRejectedValue(new Error("样式列表不可用"));
    });

    it("rejects a topic shorter than 5 characters", async () => {
        const wrapper = mountForm();
        await wrapper.find('[data-test="topic"]').setValue("RAG");
        const keywordInput = wrapper.find('[data-test="keyword-input"]');
        await keywordInput.setValue("RAG");
        await keywordInput.trigger("keydown.enter");
        await wrapper.find('[data-test="submit"]').trigger("click");

        expect(wrapper.emitted("submit")).toBeUndefined();
        expect(wrapper.find('[data-test="validation-error"]').text()).toContain(
            "主题",
        );
    });

    it("requires between 1 and 10 keywords", async () => {
        const wrapper = mountForm();
        await wrapper
            .find('[data-test="topic"]')
            .setValue("检索增强生成系统综述");
        await wrapper.find('[data-test="submit"]').trigger("click");
        expect(wrapper.emitted("submit")).toBeUndefined();
        expect(wrapper.find('[data-test="validation-error"]').text()).toContain(
            "关键词",
        );

        await fillValidForm(wrapper);
        for (let index = 0; index < 10; index += 1) {
            const input = wrapper.find('[data-test="keyword-input"]');
            await input.setValue(`关键词${index}`);
            await input.trigger("keydown.enter");
        }
        await wrapper.find('[data-test="submit"]').trigger("click");

        expect(wrapper.emitted("submit")).toBeUndefined();
        expect(wrapper.find('[data-test="validation-error"]').text()).toContain(
            "最多 10",
        );
    });

    it("rejects max documents outside 1..50", async () => {
        const wrapper = mountForm();
        await fillValidForm(wrapper);
        await wrapper.find('[data-test="max-documents"]').setValue("0");
        await wrapper.find('[data-test="submit"]').trigger("click");
        expect(wrapper.emitted("submit")).toBeUndefined();

        await wrapper.find('[data-test="max-documents"]').setValue("51");
        await wrapper.find('[data-test="submit"]').trigger("click");
        expect(wrapper.emitted("submit")).toBeUndefined();
        expect(wrapper.find('[data-test="validation-error"]').text()).toContain(
            "文献数",
        );
    });

    it("rejects a date range whose start is after the end", async () => {
        const wrapper = mountForm();
        await fillValidForm(wrapper);
        await wrapper.find('[data-test="date-from"]').setValue("2026-05-01");
        await wrapper.find('[data-test="date-to"]').setValue("2026-01-01");
        await wrapper.find('[data-test="submit"]').trigger("click");

        expect(wrapper.emitted("submit")).toBeUndefined();
        expect(wrapper.find('[data-test="validation-error"]').text()).toContain(
            "日期",
        );
    });

    it("emits the exact TaskCreate payload once", async () => {
        const wrapper = mountForm({
            documents: [
                { id: "doc-1", title: "RAG 综述" },
                { id: "doc-2", title: "智能体评估" },
            ],
        });
        await fillValidForm(wrapper);
        await wrapper.find('[data-test="max-documents"]').setValue("15");
        await wrapper.find('[data-test="date-from"]').setValue("2024-01-01");
        await wrapper.find('[data-test="date-to"]').setValue("2025-12-31");
        await wrapper.find('[data-test="lang-en"]').trigger("click");
        await wrapper.find('[data-test="style-apa"]').trigger("click");
        await wrapper.find('[data-test="offline"]').setValue(true);
        await wrapper.find('[data-test="doc-doc-1"]').setValue(true);
        await wrapper.find('[data-test="submit"]').trigger("click");

        const emitted = wrapper.emitted("submit");
        expect(emitted).toHaveLength(1);
        expect(emitted?.[0][0]).toEqual({
            topic: "检索增强生成与多智能体协作的质量评估",
            keywords: ["RAG", "multi-agent"],
            workflow_preset: "literature_review",
            max_documents: 15,
            date_from: "2024-01-01",
            date_to: "2025-12-31",
            output_language: "en",
            citation_style: "apa",
            offline_dataset: true,
            // 设计变更（2026-10-26 引用库计划）：新增文献源开关，默认 false
            reference_library: false,
            document_ids: ["doc-1"],
            verify_references: false,
        });
    });

    it("disables submit while pending", async () => {
        const wrapper = mountForm({ pending: true });
        await flushPromises();
        const submitBtn = wrapper.get('[data-test="submit"]');
        expect(submitBtn.attributes("disabled")).toBeDefined();
        await wrapper.find('[data-test="topic"]').setValue("检索增强生成重复提交守卫测试");
        await wrapper.find('[data-test="keyword-input"]').setValue("RAG");
        await wrapper.find('[data-test="keyword-add-btn"]').trigger("click");
        // 即使移除按钮 disabled 属性直接触发点击，handleSubmit 仍必须因 props.pending 拦截重入
        submitBtn.element.removeAttribute("disabled");
        await submitBtn.trigger("click");
        await flushPromises();
        expect(wrapper.emitted("submit")).toBeUndefined();
    });

    it("shows the server error message near the form", () => {
        const wrapper = mountForm({ error: "后端不可用" });
        expect(wrapper.find('[data-test="form-error"]').text()).toContain(
            "后端不可用",
        );
    });

    // ===== 动态样式下拉（Issue #4 M2 §1.5）=====

    it("loads the style list and preselects default_style_id", async () => {
        vi.mocked(api.get).mockResolvedValueOnce({
            data: {
                styles: [
                    apiStyleRow(),
                    apiStyleRow({
                        style_id: "apa",
                        title: "APA 7th",
                        is_default: false,
                    }),
                    apiStyleRow({
                        style_id: "my-style",
                        title: "我的国标样式",
                        source: "custom",
                        is_default: false,
                    }),
                ],
                default_style_id: "gbt7714-2025-numeric",
            },
        });
        const wrapper = mountForm();

        await flushPromises();

        const select = wrapper.find('[data-test="style-select"]');
        expect(select.exists()).toBe(true);
        expect(select.findAll("option")).toHaveLength(3);
        expect((select.element as HTMLSelectElement).value).toBe(
            "gbt7714-2025-numeric",
        );
        // 自定义样式同样出现在下拉里
        expect(select.text()).toContain("我的国标样式");

        await fillValidForm(wrapper);
        await wrapper.find('[data-test="submit"]').trigger("click");

        expect(wrapper.emitted("submit")?.[0][0]).toMatchObject({
            citation_style: "gbt7714-2025-numeric",
        });
    });

    it("submits the style chosen from the dynamic dropdown", async () => {
        vi.mocked(api.get).mockResolvedValueOnce({
            data: {
                styles: [
                    apiStyleRow(),
                    apiStyleRow({
                        style_id: "my-style",
                        title: "我的国标样式",
                        source: "custom",
                        is_default: false,
                    }),
                ],
                default_style_id: "gbt7714-2025-numeric",
            },
        });
        const wrapper = mountForm();
        await flushPromises();

        await wrapper.find('[data-test="style-select"]').setValue("my-style");
        await fillValidForm(wrapper);
        await wrapper.find('[data-test="submit"]').trigger("click");

        expect(wrapper.emitted("submit")?.[0][0]).toMatchObject({
            citation_style: "my-style",
        });
    });

    it("falls back to the built-in styles when the list request fails", async () => {
        const wrapper = mountForm();

        await flushPromises();

        // 列表不可用时保留内置两项，表单必须仍然可用
        expect(wrapper.find('[data-test="style-select"]').exists()).toBe(false);
        expect(wrapper.find('[data-test="style-gbt7714"]').exists()).toBe(true);
        expect(wrapper.find('[data-test="style-apa"]').exists()).toBe(true);

        await fillValidForm(wrapper);
        await wrapper.find('[data-test="submit"]').trigger("click");

        expect(wrapper.emitted("submit")?.[0][0]).toMatchObject({
            citation_style: "gbt7714",
        });
    });
});

// ===== 引用核验开关（Issue #3 遗留项 / 计划 §1.3）=====

describe("TaskCreateForm · 引用核验开关", () => {
    it("keeps reference verification off by default", async () => {
        const wrapper = mountForm();
        const checkbox = wrapper.find('[data-test="verify-references"]');

        expect(checkbox.exists()).toBe(true);
        expect((checkbox.element as HTMLInputElement).checked).toBe(false);

        await fillValidForm(wrapper);
        await wrapper.find('[data-test="submit"]').trigger("click");

        expect(wrapper.emitted("submit")?.[0][0]).toMatchObject({
            verify_references: false,
        });
    });

    it("submits verify_references: true when the switch is enabled", async () => {
        const wrapper = mountForm();
        await wrapper.find('[data-test="verify-references"]').setValue(true);
        await fillValidForm(wrapper);
        await wrapper.find('[data-test="submit"]').trigger("click");

        expect(wrapper.emitted("submit")?.[0][0]).toMatchObject({
            verify_references: true,
        });
    });

    it("explains the external comparison and the extra latency", () => {
        const wrapper = mountForm();
        const label = wrapper
            .find('[data-test="verify-references-label"]')
            .text();

        expect(label).toContain("CrossRef");
        expect(label).toContain("OpenAlex");
        expect(label).toContain("耗时");
    });
});

/**
 * 引用库作为文献源（2026-10-26 引用库计划 §3.6）。
 *
 * 这是整个引用库功能对用户可见的**最后一环**：导入 → 保存 → 创建任务时选它。
 * 之前这条链路完全不存在（导入页解析完什么也做不了）。
 */
describe("TaskCreateForm 引用库文献源", () => {
    beforeEach(() => {
        vi.clearAllMocks();
        vi.mocked(api.get).mockImplementation((url: string) => {
            if (url === "/citation-styles") {
                return Promise.resolve({
                    data: {
                        styles: [apiStyleRow()],
                        default_style_id: "gbt7714-2025-numeric",
                    },
                });
            }
            if (url === "/reference-library/summary") {
                return Promise.resolve({
                    data: { total: 7, usable_as_source: 7 },
                });
            }
            return Promise.reject(new Error(`unexpected url ${url}`));
        });
    });

    it("展示引用库条数（用户需要知道将有多少篇可用）", async () => {
        const wrapper = mountForm();
        await flushPromises();

        const label = wrapper.find('[data-test="reference-library-label"]');
        expect(label.exists()).toBe(true);
        expect(label.text()).toContain("7");
    });

    it("摘要加载中显示加载态，不误报 0 篇或引用库为空（Issue #55）", async () => {
        let resolveSummary!: (value: {
            data: { total: number; usable_as_source: number };
        }) => void;
        const summaryPromise = new Promise<{
            data: { total: number; usable_as_source: number };
        }>((resolve) => {
            resolveSummary = resolve;
        });

        vi.mocked(api.get).mockImplementation((url: string) => {
            if (url === "/citation-styles") {
                return Promise.resolve({
                    data: {
                        styles: [apiStyleRow()],
                        default_style_id: "gbt7714-2025-numeric",
                    },
                });
            }
            if (url === "/reference-library/summary") {
                return summaryPromise;
            }
            if (url === "/llm-credentials") {
                return Promise.resolve({ data: [] });
            }
            return Promise.reject(new Error(`unexpected url ${url}`));
        });

        const wrapper = mountForm();
        const label = wrapper.find('[data-test="reference-library-label"]');
        expect(label.text()).toContain("加载中");
        expect(label.text()).not.toContain("0 篇");
        expect(label.text()).not.toContain("引用库为空");

        resolveSummary({ data: { total: 2, usable_as_source: 2 } });
        await flushPromises();
        expect(label.text()).toContain("2 篇");
        expect(label.text()).not.toContain("引用库为空");
    });

    it("引用样式返回空列表时仍并行加载引用库条数与自带模型（Issue #55）", async () => {
        vi.mocked(api.get).mockImplementation((url: string) => {
            if (url === "/citation-styles") {
                return Promise.resolve({
                    data: { styles: [], default_style_id: "" },
                });
            }
            if (url === "/reference-library/summary") {
                return Promise.resolve({
                    data: { total: 2, usable_as_source: 2 },
                });
            }
            if (url === "/llm-credentials") {
                return Promise.resolve({ data: [] });
            }
            return Promise.reject(new Error(`unexpected url ${url}`));
        });

        const wrapper = mountForm();
        await flushPromises();

        expect(
            wrapper.find('[data-test="reference-library-label"]').text(),
        ).toContain("2 篇");
    });

    it("引用库为空时给出引导", async () => {
        vi.mocked(api.get).mockImplementation((url: string) => {
            if (url === "/citation-styles") {
                return Promise.resolve({
                    data: {
                        styles: [apiStyleRow()],
                        default_style_id: "gbt7714-2025-numeric",
                    },
                });
            }
            return Promise.resolve({ data: { total: 0, usable_as_source: 0 } });
        });

        const wrapper = mountForm();
        await flushPromises();

        expect(
            wrapper.find('[data-test="reference-library-label"]').text(),
        ).toMatch(/导入文献/);
    });

    it("默认不启用引用库", async () => {
        const wrapper = mountForm();
        await flushPromises();

        const checkbox = wrapper.find('[data-test="reference-library"]');
        expect((checkbox.element as HTMLInputElement).checked).toBe(false);
    });

    it("勾选后提交的 payload 带 reference_library: true", async () => {
        const wrapper = mountForm();
        await flushPromises();

        await wrapper
            .find('[data-test="topic"]')
            .setValue("检索增强生成的质量评估方法");
        await wrapper.find('[data-test="keyword-input"]').setValue("RAG");
        await wrapper
            .find('[data-test="keyword-input"]')
            .trigger("keydown.enter");
        await wrapper.find('[data-test="reference-library"]').setValue(true);
        await wrapper.find('[data-test="submit"]').trigger("click");

        const payload = wrapper.emitted("submit")?.[0]?.[0] as Record<
            string,
            unknown
        >;
        expect(payload.reference_library).toBe(true);
    });

    it("不勾选时 payload 里 reference_library 为 false", async () => {
        const wrapper = mountForm();
        await flushPromises();

        await wrapper
            .find('[data-test="topic"]')
            .setValue("检索增强生成的质量评估方法");
        await wrapper.find('[data-test="keyword-input"]').setValue("RAG");
        await wrapper
            .find('[data-test="keyword-input"]')
            .trigger("keydown.enter");
        await wrapper.find('[data-test="submit"]').trigger("click");

        const payload = wrapper.emitted("submit")?.[0]?.[0] as Record<
            string,
            unknown
        >;
        expect(payload.reference_library).toBe(false);
    });

    it("摘要接口失败不影响表单（局部失败不连坐）", async () => {
        vi.mocked(api.get).mockImplementation((url: string) => {
            if (url === "/citation-styles") {
                return Promise.resolve({
                    data: {
                        styles: [apiStyleRow()],
                        default_style_id: "gbt7714-2025-numeric",
                    },
                });
            }
            return Promise.reject(new Error("summary 500"));
        });

        const wrapper = mountForm();
        await flushPromises();

        // 表单仍可提交
        expect(wrapper.find('[data-test="submit"]').exists()).toBe(true);
        expect(wrapper.find('[data-test="reference-library"]').exists()).toBe(
            true,
        );
    });

    it("启用引用库时禁用离线语料（两者互斥，避免配置冲突）", async () => {
        const wrapper = mountForm();
        await flushPromises();

        await wrapper.find('[data-test="reference-library"]').setValue(true);
        const offline = wrapper.find('[data-test="offline"]');
        expect((offline.element as HTMLInputElement).disabled).toBe(true);
    });

    it("选择自带模型时 payload 带上 llm_credential_id", async () => {
        vi.mocked(api.get).mockImplementation((url: string) => {
            if (url === "/llm-credentials") {
                return Promise.resolve({
                    data: [
                        {
                            credential_id: "cred-1",
                            name: "我的 DeepSeek",
                            provider: "deepseek",
                            provider_label: "DeepSeek",
                            model: "deepseek-chat",
                            status: "active",
                        },
                    ],
                });
            }
            return Promise.reject(new Error("其它接口不可用"));
        });

        const wrapper = mountForm();
        await flushPromises();
        await fillValidForm(wrapper);
        // 显式二选一：先切到「自带模型」再选凭据
        await wrapper.find('[data-test="model-source-byok"]').trigger("click");
        await wrapper.find('[data-test="model-select"]').setValue("cred-1");
        await wrapper.find('[data-test="submit"]').trigger("click");

        const payload = wrapper.emitted("submit")?.[0]?.[0] as Record<
            string,
            unknown
        >;
        expect(payload.llm_credential_id).toBe("cred-1");
    });

    it("不选模型时 payload 里没有 llm_credential_id（保持旧契约）", async () => {
        const wrapper = mountForm();
        await flushPromises();
        await fillValidForm(wrapper);
        await wrapper.find('[data-test="submit"]').trigger("click");

        const payload = wrapper.emitted("submit")?.[0]?.[0] as Record<
            string,
            unknown
        >;
        expect("llm_credential_id" in payload).toBe(false);
    });
});

/**
 * 默认模型选择（2026-11-17 线上故障）。
 *
 * 用户在「我的模型」里把某把凭据标成默认，建任务时却仍走平台默认模型；
 * 平台密钥缺失时任务直接卡在规划阶段（Missing credentials），
 * 用户只看到「任务已暂停，等你介入」。这里锁住：有默认凭据就选它。
 */
describe("TaskCreateForm 默认模型", () => {
    const baseCredential = {
        credential_id: "cred-default",
        name: "Qwen",
        provider: "custom",
        provider_label: "自定义",
        model: "qwen3.8-27b",
        base_url: "https://model.example.com/v1",
        masked_key: "sk-…abcd",
        key_version: 1,
        is_default: false,
        status: "active",
        capabilities: {},
        last_verified_at: null,
    };

    function mockApis(credentials: unknown[]) {
        vi.mocked(api.get).mockImplementation((url: string) => {
            if (url === "/citation-styles") {
                return Promise.resolve({
                    data: {
                        styles: [apiStyleRow()],
                        default_style_id: "gbt7714-2025-numeric",
                    },
                });
            }
            if (url === "/reference-library/summary") {
                return Promise.resolve({
                    data: { total: 0, usable_as_source: 0 },
                });
            }
            if (url === "/llm-credentials") {
                return Promise.resolve({ data: credentials });
            }
            return Promise.reject(new Error(`unexpected url ${url}`));
        });
    }

    beforeEach(() => {
        vi.clearAllMocks();
    });

    it("默认选中用户标记为默认的可用凭据（显式停在自带模型）", async () => {
        mockApis([
            {
                ...baseCredential,
                credential_id: "cred-other",
                is_default: false,
            },
            { ...baseCredential, is_default: true },
        ]);

        const wrapper = mountForm();
        await flushPromises();

        const byokBtn = wrapper.get('[data-test="model-source-byok"]')
            .element as HTMLButtonElement;
        expect(byokBtn.getAttribute("aria-pressed")).toBe("true");
        const select = wrapper.get('[data-test="model-select"]')
            .element as HTMLSelectElement;
        expect(select.value).toBe("cred-default");
    });

    it("默认凭据不可用时退回平台模型（不选中必失败的凭据）", async () => {
        mockApis([{ ...baseCredential, is_default: true, status: "invalid" }]);

        const wrapper = mountForm();
        await flushPromises();

        const platformBtn = wrapper.get('[data-test="model-source-platform"]')
            .element as HTMLButtonElement;
        expect(platformBtn.getAttribute("aria-pressed")).toBe("true");
        // 平台模式下没有凭据下拉
        expect(wrapper.find('[data-test="model-select"]').exists()).toBe(false);
    });

    it("没有默认凭据时保持平台模型", async () => {
        mockApis([{ ...baseCredential, is_default: false }]);

        const wrapper = mountForm();
        await flushPromises();

        const platformBtn = wrapper.get('[data-test="model-source-platform"]')
            .element as HTMLButtonElement;
        expect(platformBtn.getAttribute("aria-pressed")).toBe("true");
    });

    it("有默认凭据时显式切回平台模型，payload 不带 llm_credential_id", async () => {
        mockApis([
            {
                ...baseCredential,
                credential_id: "cred-other",
                is_default: false,
            },
            { ...baseCredential, is_default: true },
        ]);

        const wrapper = mountForm();
        await flushPromises();
        await fillValidForm(wrapper);
        // 初始被默认凭据带成自带模型，用户显式切回平台
        await wrapper
            .find('[data-test="model-source-platform"]')
            .trigger("click");
        await wrapper.find('[data-test="submit"]').trigger("click");

        const payload = wrapper.emitted("submit")?.[0]?.[0] as Record<
            string,
            unknown
        >;
        expect("llm_credential_id" in payload).toBe(false);
    });

    it("选了自带模型但没选具体凭据 → 校验拦截（不静默回退平台）", async () => {
        mockApis([
            { ...baseCredential, credential_id: "cred-a", is_default: false },
        ]);

        const wrapper = mountForm();
        await flushPromises();
        await fillValidForm(wrapper);
        await wrapper.find('[data-test="model-source-byok"]').trigger("click");
        await wrapper.find('[data-test="submit"]').trigger("click");

        const banner = wrapper.find('[data-test="validation-error"]');
        expect(banner.exists()).toBe(true);
        expect(banner.text()).toContain("请从列表中选出一把可用凭据");
        expect(wrapper.emitted("submit")).toBeUndefined();
    });

    it("没有任何可用凭据时「自带模型」按钮禁用", async () => {
        mockApis([{ ...baseCredential, is_default: true, status: "disabled" }]);

        const wrapper = mountForm();
        await flushPromises();

        const byok = wrapper.find('[data-test="model-source-byok"]');
        expect((byok.element as HTMLButtonElement).disabled).toBe(true);
    });

    // ===== 预设工作流（Issue #32 Phase 1）=====

    it("切换到 research_gaps 预设会联动默认参数并在提交时带上 workflow_preset", async () => {
        const wrapper = mountForm();
        await flushPromises();

        const gapsBtn = wrapper.find('[data-test="preset-research_gaps"]');
        expect(gapsBtn.exists()).toBe(true);
        await gapsBtn.trigger("click");

        expect(gapsBtn.attributes("aria-checked")).toBe("true");
        expect(
            (wrapper.find('[data-test="max-documents"]').element as HTMLInputElement)
                .value,
        ).toBe("25");
        expect(
            (
                wrapper.find('[data-test="verify-references"]')
                    .element as HTMLInputElement
            ).checked,
        ).toBe(true);
        expect(wrapper.find('[data-test="topic"]').attributes("placeholder")).toContain(
            "研究空白",
        );

        await fillValidForm(wrapper);
        await wrapper.find('[data-test="submit"]').trigger("click");

        expect(wrapper.emitted("submit")?.[0][0]).toMatchObject({
            workflow_preset: "research_gaps",
            max_documents: 25,
            verify_references: true,
        });
    });

    it("切换到 verify_citations 预设：隐藏文献数与关键词，空草稿且无文档时拦截，粘贴草稿后正常提交", async () => {
        const wrapper = mountForm({
            documents: [{ id: "doc-1", title: "RAG 原文" }],
        });
        await flushPromises();

        await wrapper.find('[data-test="preset-verify_citations"]').trigger("click");
        expect(wrapper.find('[data-test="draft-text"]').exists()).toBe(true);
        expect(wrapper.find('[data-test="max-documents"]').exists()).toBe(false);
        expect(wrapper.find('[data-test="keyword-input"]').exists()).toBe(false);

        // 草稿与知识库均为空 → 拦截
        await wrapper.find('[data-test="submit"]').trigger("click");
        expect(wrapper.emitted("submit")).toBeUndefined();
        expect(wrapper.find('[data-test="validation-error"]').text()).toContain(
            "待核验草稿",
        );

        // 粘贴草稿 + 勾选知识库文档 → 提交成功
        await wrapper
            .find('[data-test="draft-text"]')
            .setValue("检索增强生成显著降低了事实错误 [E1]。\n\n## 参考文献\n- [1] RAG 2020");
        await wrapper.find('[data-test="doc-doc-1"]').setValue(true);
        await wrapper.find('[data-test="submit"]').trigger("click");

        expect(wrapper.emitted("submit")?.[0][0]).toMatchObject({
            workflow_preset: "verify_citations",
            draft_text:
                "检索增强生成显著降低了事实错误 [E1]。\n\n## 参考文献\n- [1] RAG 2020",
            document_ids: ["doc-1"],
            verify_references: true,
        });
    });

    it("切换到 citation_snowballing 预设：支持填写种子 DOI 与扩展方向并提交", async () => {
        const wrapper = mountForm();
        await flushPromises();

        await wrapper
            .find('[data-test="preset-citation_snowballing"]')
            .trigger("click");
        expect(wrapper.find('[data-test="seed-doi-input"]').exists()).toBe(true);

        await wrapper
            .find('[data-test="topic"]')
            .setValue("基于经典论文的 RAG 引文滚雪球综述");
        const seedInput = wrapper.find('[data-test="seed-doi-input"]');
        await seedInput.setValue("10.1000/rag-seed");
        await seedInput.trigger("keydown.enter");
        await wrapper
            .find('[data-test="snowball-dir-forward"]')
            .trigger("click");
        await wrapper.find('[data-test="submit"]').trigger("click");

        expect(wrapper.emitted("submit")?.[0][0]).toMatchObject({
            workflow_preset: "citation_snowballing",
            seed_dois: ["10.1000/rag-seed"],
            snowball_direction: "forward",
            max_documents: 30,
            verify_references: true,
        });
    });

    it("关键词输入框按 Enter 只添加标签并阻止默认事件，绝不误触整表提交（Issue #58）", async () => {
        const wrapper = mountForm();
        await flushPromises();
        await wrapper
            .find('[data-test="topic"]')
            .setValue("检索增强生成引用库闭环综述");

        const keywordInput = wrapper.find('[data-test="keyword-input"]');
        await keywordInput.setValue("RAG");
        const enterEvent = new KeyboardEvent("keydown", {
            key: "Enter",
            bubbles: true,
            cancelable: true,
        });
        keywordInput.element.dispatchEvent(enterEvent);
        await flushPromises();

        expect(enterEvent.defaultPrevented).toBe(true);
        expect(wrapper.emitted("submit")).toBeUndefined();
        expect(wrapper.text()).toContain("RAG");
        expect((keywordInput.element as HTMLInputElement).value).toBe("");
        // 提交按钮必须为 type="button"（彻底关闭多字段表单的浏览器隐式回车提交）
        expect(wrapper.find('[data-test="submit"]').attributes("type")).toBe("button");
        // 即使外层 form 收到原生 submit 事件也不得误触提交（只有显式点击提交按钮才提交）
        await wrapper.find("form").trigger("submit");
        expect(wrapper.emitted("submit")).toBeUndefined();

        // 显式点击「创建任务」按钮仍可正常提交
        await wrapper.find('[data-test="submit"]').trigger("click");
        expect(wrapper.emitted("submit")).toHaveLength(1);
    });

    it("勾选使用我的引用库时，自动将文献数上限同步为引用库可用条数（Issue #58）", async () => {
        vi.mocked(api.get).mockImplementation((url: string) => {
            if (url === "/citation-styles") {
                return Promise.resolve({
                    data: {
                        styles: [apiStyleRow()],
                        default_style_id: "gbt7714-2025-numeric",
                    },
                });
            }
            if (url === "/reference-library/summary") {
                return Promise.resolve({
                    data: { total: 2, usable_as_source: 2 },
                });
            }
            if (url === "/llm-credentials") {
                return Promise.resolve({ data: [] });
            }
            return Promise.reject(new Error(`unexpected url ${url}`));
        });

        const wrapper = mountForm();
        await flushPromises();

        await wrapper.find('[data-test="reference-library"]').setValue(true);
        await flushPromises();

        expect(
            (wrapper.find('[data-test="max-documents"]').element as HTMLInputElement)
                .value,
        ).toBe("2");
    });

    it("指定知识库文档列表对超长无空格文件名应用 min-w-0 与 break-all 防止移动端横向溢出（Issue #62）", async () => {
        const longTitle = "10_EVOR_Evolving_Retrieval_CodeGen__2402.12317v2";
        const wrapper = mountForm({
            documents: [{ id: "doc-long", title: longTitle }],
        });
        await flushPromises();

        const fieldset = wrapper.find('[data-test="task-documents-fieldset"]');
        expect(fieldset.exists()).toBe(true);
        expect(fieldset.classes()).toContain("min-w-0");

        const titleSpan = fieldset.find("label span");
        expect(titleSpan.text()).toBe(longTitle);
        expect(titleSpan.classes()).toContain("min-w-0");
        expect(titleSpan.classes()).toContain("break-all");
    });

    it("关键词与种子 DOI 支持分隔符批量拆分及显式「添加」按钮", async () => {
        const wrapper = mountForm();
        await flushPromises();

        // 关键词批量拆分 + 显式添加按钮
        const kwInput = wrapper.find('[data-test="keyword-input"]');
        await kwInput.setValue("RAG, Agent；检索，RAG");
        await wrapper.find('[data-test="keyword-add-btn"]').trigger("click");
        expect(wrapper.text()).toContain("RAG");
        expect(wrapper.text()).toContain("Agent");
        expect(wrapper.text()).toContain("检索");
        expect((kwInput.element as HTMLInputElement).value).toBe("");

        // 滚雪球模式下种子 DOI 批量拆分 + 显式添加按钮
        await wrapper.find('[data-test="preset-citation_snowballing"]').trigger("click");
        const seedInput = wrapper.find('[data-test="seed-doi-input"]');
        await seedInput.setValue("10.1000/a; 10.1000/b，10.1000/a");
        await wrapper.find('[data-test="seed-doi-add-btn"]').trigger("click");
        expect(wrapper.text()).toContain("10.1000/a");
        expect(wrapper.text()).toContain("10.1000/b");
    });

    it("提供文献数预设芯片、引用库互斥清零与安全原生跳转链接", async () => {
        vi.mocked(api.get).mockImplementation((url: string) => {
            if (url === "/citation-styles") {
                return Promise.resolve({
                    data: { styles: [apiStyleRow()], default_style_id: "gbt7714-2025-numeric" },
                });
            }
            if (url === "/reference-library/summary") {
                return Promise.resolve({ data: { total: 0, usable_as_source: 0 } });
            }
            if (url === "/llm-credentials") {
                return Promise.resolve({ data: [] });
            }
            return Promise.reject(new Error(`unexpected url ${url}`));
        });

        const wrapper = mountForm();
        await flushPromises();

        // 预设芯片 5 / 10 / 20
        await wrapper.find('[data-test="max-docs-chip-20"]').trigger("click");
        expect((wrapper.find('[data-test="max-documents"]').element as HTMLInputElement).value).toBe("20");

        // 引用库为空时复选框禁用且提供原生 <a> 跳转
        const libCheckbox = wrapper.find('[data-test="reference-library"]').element as HTMLInputElement;
        expect(libCheckbox.disabled).toBe(true);
        const importLink = wrapper.find('a[href="/references/import"]');
        expect(importLink.exists()).toBe(true);

        // 无可用 BYOK 凭据时提供前往我的模型原生 <a> 入口
        const modelLink = wrapper.find('a[href="/settings/models"]');
        expect(modelLink.exists()).toBe(true);
    });

    it("启用引用库时自动重置 offlineDataset 为 false", async () => {
        vi.mocked(api.get).mockImplementation((url: string) => {
            if (url === "/citation-styles") {
                return Promise.resolve({
                    data: { styles: [apiStyleRow()], default_style_id: "gbt7714-2025-numeric" },
                });
            }
            if (url === "/reference-library/summary") {
                return Promise.resolve({ data: { total: 5, usable_as_source: 5 } });
            }
            if (url === "/llm-credentials") {
                return Promise.resolve({ data: [] });
            }
            return Promise.reject(new Error(`unexpected url ${url}`));
        });

        const wrapper = mountForm();
        await flushPromises();

        await wrapper.find('[data-test="offline"]').setValue(true);
        expect((wrapper.find('[data-test="offline"]').element as HTMLInputElement).checked).toBe(true);

        await wrapper.find('[data-test="reference-library"]').setValue(true);
        await flushPromises();

        expect((wrapper.find('[data-test="offline"]').element as HTMLInputElement).checked).toBe(false);
    });

    it("日期校验失败时自动展开高级折叠区并高亮出错字段", async () => {
        const wrapper = mountForm();
        await flushPromises();
        await fillValidForm(wrapper);

        const toggleBtn = wrapper.find('[data-test="toggle-advanced-options"]');
        expect(toggleBtn.attributes("aria-expanded")).toBe("false");

        await wrapper.find('[data-test="date-from"]').setValue("2025-05-01");
        await wrapper.find('[data-test="date-to"]').setValue("2024-01-01");
        await wrapper.find('[data-test="submit"]').trigger("click");
        await flushPromises();

        expect(toggleBtn.attributes("aria-expanded")).toBe("true");
        expect(wrapper.find('[data-test="date-to"]').classes()).toContain("border-destructive");
    });

    it("粘贴超限批量关键词且未失焦直接提交时应拆分拦截并标红（Challenger 1.3b）", async () => {
        const wrapper = mountForm();
        await flushPromises();
        await wrapper.find('[data-test="topic"]').setValue("检索增强生成批量关键词校验测试");

        const kwInput = wrapper.find('[data-test="keyword-input"]');
        await kwInput.setValue("a,b，c;d；e,f,g,h,i,j,k,l");
        await wrapper.find('[data-test="submit"]').trigger("click");
        await flushPromises();

        expect(wrapper.emitted("submit")).toBeUndefined();
        expect(wrapper.find('[data-test="validation-error"]').text()).toContain("最多 10");
        expect(kwInput.classes()).toContain("border-destructive");
    });

    it("已有 9 词时未失焦输入 2 个逗号分隔关键词直接提交应拦截并标红（Challenger 1.3c）", async () => {
        const wrapper = mountForm();
        await flushPromises();
        await wrapper.find('[data-test="topic"]').setValue("检索增强生成边界叠加校验测试");

        const kwInput = wrapper.find('[data-test="keyword-input"]');
        for (let index = 1; index <= 9; index += 1) {
            await kwInput.setValue(`k${index}`);
            await kwInput.trigger("keydown.enter");
        }
        await kwInput.setValue("k10,k11");
        await wrapper.find('[data-test="submit"]').trigger("click");
        await flushPromises();

        expect(wrapper.emitted("submit")).toBeUndefined();
        expect(wrapper.find('[data-test="validation-error"]').text()).toContain("最多 10");
        expect(kwInput.classes()).toContain("border-destructive");
    });

    it("滚雪球模式输入 6 个逗号分隔种子 DOI 未失焦直接提交应拦截并标红（Challenger 1.3d）", async () => {
        const wrapper = mountForm();
        await flushPromises();
        await wrapper.find('[data-test="preset-citation_snowballing"]').trigger("click");
        await wrapper.find('[data-test="topic"]').setValue("引文滚雪球种子数量上限校验测试");

        const seedInput = wrapper.find('[data-test="seed-doi-input"]');
        await seedInput.setValue("10.1/a,10.1/b,10.1/c,10.1/d,10.1/e,10.1/f");
        await wrapper.find('[data-test="submit"]').trigger("click");
        await flushPromises();

        expect(wrapper.emitted("submit")).toBeUndefined();
        expect(wrapper.find('[data-test="validation-error"]').text()).toContain("最多 5");
        expect(seedInput.classes()).toContain("border-destructive");
    });

    it("未失焦输入合法批量关键词直接提交时自动拆分为独立条目并去重", async () => {
        const wrapper = mountForm();
        await flushPromises();
        await wrapper.find('[data-test="topic"]').setValue("未失焦合法批量拆分提交测试");

        const kwInput = wrapper.find('[data-test="keyword-input"]');
        await kwInput.setValue("a, b");
        await kwInput.trigger("keydown.enter");
        await kwInput.setValue("c, d；b");
        await wrapper.find('[data-test="submit"]').trigger("click");
        await flushPromises();

        const emitted = wrapper.emitted("submit");
        expect(emitted).toHaveLength(1);
        expect(emitted?.[0][0]).toMatchObject({
            keywords: ["a", "b", "c", "d"],
        });
    });

    it("关闭后端错误提示条时透传 dismissError 事件", async () => {
        const wrapper = mountForm({ error: "服务器暂时不可用" });
        const closeBtn = wrapper.find('[data-test="form-error"] button');
        expect(closeBtn.exists()).toBe(true);
        await closeBtn.trigger("click");

        expect(wrapper.emitted("dismissError")).toEqual([[]]);
    });

    it("空提交使关键词输入框标红后，补添合法数量关键词立即清除红框；超限保留并在删减回上限内清除（W5）", async () => {
        const wrapper = mountForm();
        await flushPromises();
        await wrapper.find('[data-test="topic"]').setValue("关键词校验红框清除测试");

        // 1. 关键词为空直接提交 -> keyword-input 标红
        await wrapper.find('[data-test="submit"]').trigger("click");
        await flushPromises();
        const kwInput = wrapper.find('[data-test="keyword-input"]');
        expect(kwInput.classes()).toContain("border-destructive");

        // 2. 点击添加补添 1 个关键词（<= 10）-> 立即清除红框
        await kwInput.setValue("RAG");
        await wrapper.find('[data-test="keyword-add-btn"]').trigger("click");
        await flushPromises();
        expect(kwInput.classes()).not.toContain("border-destructive");

        // 3. 批量添加使关键词超过 10 个并提交触发超限标红 -> 保留红框；删减到 <=10 后清除
        await kwInput.setValue("k1,k2,k3,k4,k5,k6,k7,k8,k9,k10");
        await wrapper.find('[data-test="keyword-add-btn"]').trigger("click");
        await wrapper.find('[data-test="submit"]').trigger("click");
        await flushPromises();
        expect(kwInput.classes()).toContain("border-destructive");

        // 删除 1 个关键词回到 10 个（<= KEYWORD_MAX）-> 红框清除
        await wrapper.find('button[aria-label="移除 RAG"]').trigger("click");
        await flushPromises();
        expect(kwInput.classes()).not.toContain("border-destructive");
    });
});

// 草稿保留（2026-11-26 用户反馈）：新用户去导入文献 / 收起表单 / 误刷新后，
// 建任务页已填内容不能丢。表单自己负责存取，父组件负责自动展开与成功后清理。
describe("TaskCreateForm 草稿保留", () => {
    beforeEach(() => {
        vi.mocked(api.get).mockRejectedValue(new Error("样式列表不可用"));
    });

    it("真实输入后自动落盘到该用户的草稿命名空间", async () => {
        localStorage.clear();
        const wrapper = mountForm({ draftUserId: "user-draft-1" });

        await wrapper
            .find('[data-test="topic"]')
            .setValue("导入文献后回来草稿不能丢的范围综述研究");
        await wrapper.find('[data-test="keyword-input"]').setValue("draft");
        await wrapper.find('[data-test="keyword-add-btn"]').trigger("click");
        await flushPromises();

        const raw = localStorage.getItem(taskDraftStorageKey("user-draft-1"));
        expect(raw).toBeTruthy();
        expect(JSON.parse(raw!).values.topic).toBe(
            "导入文献后回来草稿不能丢的范围综述研究",
        );
    });

    it("挂载时恢复已有草稿并显示恢复提示", async () => {
        localStorage.clear();
        saveTaskDraft("user-draft-restore", {
            workflowPreset: "literature_review",
            topic: "上次没提交的综述主题草稿",
            draftText: "",
            seedDois: [],
            seedDraft: "",
            snowballDirection: "both",
            keywords: ["RAG", "agent"],
            keywordDraft: "",
            maxDocuments: 10,
            dateFrom: "",
            dateTo: "",
            language: "en",
            citationStyle: "apa",
            offlineDataset: false,
            useReferenceLibrary: false,
            verifyReferences: false,
            modelSource: "platform",
            selectedCredential: "",
            selectedDocuments: [],
            literatureSources: [],
        });

        const wrapper = mountForm({ draftUserId: "user-draft-restore" });
        await flushPromises();

        expect(
            (wrapper.find('[data-test="topic"]').element as HTMLInputElement).value,
        ).toBe("上次没提交的综述主题草稿");
        expect(wrapper.find('[data-test="draft-restored"]').exists()).toBe(true);
        expect(wrapper.text()).toContain("已恢复上次未提交的草稿");
    });

    it("点「清除草稿」后存储被删除、表单回到默认状态、提示消失", async () => {
        localStorage.clear();
        saveTaskDraft("user-draft-discard", {
            workflowPreset: "literature_review",
            topic: "要被清除的草稿主题",
            draftText: "",
            seedDois: [],
            seedDraft: "",
            snowballDirection: "both",
            keywords: ["RAG"],
            keywordDraft: "",
            maxDocuments: 15,
            dateFrom: "",
            dateTo: "",
            language: "zh",
            citationStyle: "gbt7714",
            offlineDataset: false,
            useReferenceLibrary: false,
            verifyReferences: false,
            modelSource: "platform",
            selectedCredential: "",
            selectedDocuments: [],
            literatureSources: [],
        });

        const wrapper = mountForm({ draftUserId: "user-draft-discard" });
        await flushPromises();
        await wrapper.find('[data-test="draft-discard"]').trigger("click");
        await flushPromises();

        expect(localStorage.getItem(taskDraftStorageKey("user-draft-discard"))).toBeNull();
        expect(wrapper.find('[data-test="draft-restored"]').exists()).toBe(false);
        expect(
            (wrapper.find('[data-test="topic"]').element as HTMLInputElement).value,
        ).toBe("");
    });

    it("没有草稿时不显示恢复提示（不能凭空出现「已恢复」）", async () => {
        localStorage.clear();
        const wrapper = mountForm({ draftUserId: "user-without-draft" });
        await flushPromises();

        expect(wrapper.find('[data-test="draft-restored"]').exists()).toBe(false);
    });

    it("父组件提交成功后调用 clearDraft 会清掉草稿", async () => {
        localStorage.clear();
        const wrapper = mountForm({ draftUserId: "user-clear-after-submit" });
        await wrapper
            .find('[data-test="topic"]')
            .setValue("提交成功后应当清理草稿的主题");
        await flushPromises();
        expect(
            localStorage.getItem(taskDraftStorageKey("user-clear-after-submit")),
        ).toBeTruthy();

        (wrapper.vm as unknown as { clearDraft: () => void }).clearDraft();
        await flushPromises();

        expect(
            localStorage.getItem(taskDraftStorageKey("user-clear-after-submit")),
        ).toBeNull();
    });

    it("引用库为空时，恢复出的「使用我的引用库」勾选会被自动取消", async () => {
        localStorage.clear();
        saveTaskDraft("user-draft-empty-library", {
            workflowPreset: "literature_review",
            topic: "引用库为空时草稿里的勾选要自动取消",
            draftText: "",
            seedDois: [],
            seedDraft: "",
            snowballDirection: "both",
            keywords: ["RAG"],
            keywordDraft: "",
            maxDocuments: 5,
            dateFrom: "",
            dateTo: "",
            language: "zh",
            citationStyle: "gbt7714",
            offlineDataset: false,
            useReferenceLibrary: true,
            verifyReferences: false,
            modelSource: "platform",
            selectedCredential: "",
            selectedDocuments: [],
            literatureSources: [],
        });

        const wrapper = mountForm({ draftUserId: "user-draft-empty-library" });
        await flushPromises();

        const checkbox = wrapper.find('[data-test="reference-library"]');
        // api.get 全部 reject → 摘要失败 → libraryCount = 0 → 禁用且取消勾选
        expect((checkbox.element as HTMLInputElement).checked).toBe(false);
    });
});

// ===== 检索文献源（Issue #67 万方 BYOK）=====
// 万方需要用户自带 App Key：没有 Key 时芯片禁用并给出设置入口，
// 有 Key 时勾选要把 literature_sources 透传给后端。
function mockDataSourceApi(options: { wanfangConfigured: boolean }) {
    vi.mocked(api.get).mockImplementation((async (url: string) => {
        if (url === "/data-source-credentials") {
            return {
                data: [
                    {
                        provider: "wanfang",
                        configured: options.wanfangConfigured,
                        key_mask: options.wanfangConfigured ? "38704_****02cc" : null,
                        status: options.wanfangConfigured ? "active" : null,
                        updated_at: null,
                    },
                ],
            };
        }
        throw new Error("接口不可用");
    }) as never);
}

describe("TaskCreateForm · 检索文献源（万方 BYOK）", () => {
    beforeEach(() => {
        vi.mocked(api.get).mockRejectedValue(new Error("样式列表不可用"));
    });

    it("未配置万方 Key 时芯片禁用，并给出「去设置」与「获取 Key」入口", async () => {
        const wrapper = mountForm();
        await flushPromises();

        const wanfangChip = wrapper.find('[data-test="source-option-wanfang"]');
        expect((wanfangChip.element as HTMLButtonElement).disabled).toBe(true);
        // 免费源仍然可选
        expect(
            (wrapper.find('[data-test="source-option-openalex"]').element as HTMLButtonElement)
                .disabled,
        ).toBe(false);

        const missing = wrapper.find('[data-test="wanfang-key-missing"]');
        expect(missing.exists()).toBe(true);
        expect(missing.text()).toContain("去设置填写 Key");

        const getKeyLink = wrapper.find('[data-test="wanfang-get-key-link"]');
        expect(getKeyLink.attributes("href")).toBe(
            "https://www.wfdata.com/skill-api?tab=mine",
        );
        expect(getKeyLink.attributes("target")).toBe("_blank");
        expect(getKeyLink.attributes("rel")).toContain("noopener");
    });

    it("已配置万方 Key 时芯片可选，提交时透传 literature_sources", async () => {
        mockDataSourceApi({ wanfangConfigured: true });
        const wrapper = mountForm();
        await flushPromises();

        expect(wrapper.find('[data-test="wanfang-key-hint"]').exists()).toBe(true);
        const wanfangChip = wrapper.find('[data-test="source-option-wanfang"]');
        expect((wanfangChip.element as HTMLButtonElement).disabled).toBe(false);

        await wanfangChip.trigger("click");
        expect(wanfangChip.attributes("aria-pressed")).toBe("true");

        await fillValidForm(wrapper);
        await wrapper.find('[data-test="submit"]').trigger("click");

        const payload = wrapper.emitted("submit")?.[0]?.[0] as {
            literature_sources?: string[];
        };
        expect(payload.literature_sources).toEqual(["wanfang"]);
    });

    it("没有勾选检索源时 payload 不携带 literature_sources（交给后端自动决定）", async () => {
        mockDataSourceApi({ wanfangConfigured: true });
        const wrapper = mountForm();
        await flushPromises();

        await fillValidForm(wrapper);
        await wrapper.find('[data-test="submit"]').trigger("click");

        const payload = wrapper.emitted("submit")?.[0]?.[0] as Record<string, unknown>;
        expect(payload.literature_sources).toBeUndefined();
    });
});

describe("AI 智能拆分关键词（Issue #70）", () => {
    beforeEach(() => {
        vi.mocked(api.get).mockRejectedValue(new Error("样式列表不可用"));
        vi.mocked(api.post).mockReset();
    });

    it("主题少于 5 字时禁用「AI 拆分关键词」按钮，达到 5 字后启用", async () => {
        const wrapper = mountForm();
        const btn = wrapper.get('[data-test="suggest-keywords-btn"]');
        expect((btn.element as HTMLButtonElement).disabled).toBe(true);

        await wrapper.get('[data-test="topic"]').setValue("RAG");
        expect((btn.element as HTMLButtonElement).disabled).toBe(true);

        await wrapper.get('[data-test="topic"]').setValue("检索增强生成综述");
        expect((btn.element as HTMLButtonElement).disabled).toBe(false);
    });

    it("点击拆词展示候选芯片，支持单选填入与再次取消", async () => {
        vi.mocked(api.post).mockResolvedValueOnce({
            data: {
                suggestions: [
                    { term: "检索增强生成", lang: "zh" },
                    { term: "retrieval augmented generation", lang: "en" },
                ],
                source: "llm",
            },
        });

        const wrapper = mountForm();
        await wrapper.get('[data-test="topic"]').setValue("检索增强生成评估系统综述");
        await wrapper.get('[data-test="suggest-keywords-btn"]').trigger("click");
        await flushPromises();

        expect(api.post).toHaveBeenCalledWith("/tasks/suggest-keywords", {
            topic: "检索增强生成评估系统综述",
            output_language: "zh",
            existing_keywords: [],
            max_keywords: 8,
        });

        const panel = wrapper.find('[data-test="suggested-keywords-panel"]');
        expect(panel.exists()).toBe(true);

        const chips = wrapper.findAll('[data-test="suggested-keyword-chip"]');
        expect(chips).toHaveLength(2);
        expect(chips[0].text()).toContain("检索增强生成");
        expect(chips[1].text()).toContain("EN");

        await chips[0].trigger("click");
        expect(chips[0].attributes("aria-pressed")).toBe("true");

        await wrapper.get('[data-test="submit"]').trigger("click");
        const firstSubmit = wrapper.emitted("submit")?.[0]?.[0] as { keywords: string[] };
        expect(firstSubmit.keywords).toEqual(["检索增强生成"]);

        // 再次点击同一芯片取消选中
        await chips[0].trigger("click");
        expect(chips[0].attributes("aria-pressed")).toBe("false");
    });

    it("「全部填入」遵守 10 个关键词上限且「换一批」去重传参不超过 10 个", async () => {
        const twelveTerms = Array.from({ length: 8 }, (_, idx) => ({
            term: `候选词${idx + 1}`,
            lang: idx % 2 === 0 ? ("zh" as const) : ("en" as const),
        }));
        vi.mocked(api.post)
            .mockResolvedValueOnce({ data: { suggestions: twelveTerms, source: "llm" } })
            .mockResolvedValueOnce({
                data: {
                    suggestions: [{ term: "新词A", lang: "zh" }],
                    source: "llm",
                },
            });

        const wrapper = mountForm();
        await wrapper.get('[data-test="topic"]').setValue("多智能体协作文献综述与质量评价");

        // 先手动填入 4 个关键词
        for (let i = 1; i <= 4; i += 1) {
            const input = wrapper.get('[data-test="keyword-input"]');
            await input.setValue(`已有词${i}`);
            await input.trigger("keydown.enter");
        }

        await wrapper.get('[data-test="suggest-keywords-btn"]').trigger("click");
        await flushPromises();

        // 再点「全部填入」：4 + 8 -> 截断至 10 个
        await wrapper.get('[data-test="apply-all-suggestions"]').trigger("click");
        await wrapper.get('[data-test="submit"]').trigger("click");
        const submitted = wrapper.emitted("submit")?.[0]?.[0] as { keywords: string[] };
        expect(submitted.keywords).toHaveLength(10);

        // 点击「换一批」：existing_keywords 应去重且长度 <= 10
        await wrapper.get('[data-test="refresh-suggestions"]').trigger("click");
        await flushPromises();

        expect(api.post).toHaveBeenLastCalledWith(
            "/tasks/suggest-keywords",
            expect.objectContaining({
                topic: "多智能体协作文献综述与质量评价",
                max_keywords: 8,
            }),
        );
        const lastPayload = vi.mocked(api.post).mock.calls[1][1] as {
            existing_keywords: string[];
        };
        expect(lastPayload.existing_keywords.length).toBeLessThanOrEqual(10);
        expect(new Set(lastPayload.existing_keywords).size).toBe(
            lastPayload.existing_keywords.length,
        );
    });
});

describe("任务创建草稿跨设备云端同步（Issue #68）", () => {
    beforeEach(() => {
        localStorage.clear();
        vi.useRealTimers();
        vi.mocked(api.get).mockRejectedValue(new Error("样式列表不可用"));
        vi.mocked(api.post).mockReset();
        vi.mocked(api.put).mockReset();
        vi.mocked(api.delete).mockReset();
    });

    it("本地无草稿时拉取云端草稿恢复，展示「已恢复云端草稿」提示并回填内容", async () => {
        vi.mocked(api.get).mockImplementation(async (url: string) => {
            if (url === "/tasks/draft") {
                return {
                    data: {
                        payload: {
                            workflow_preset: "literature_review",
                            topic: "云端同步过来的综述主题",
                            keywords: ["CloudDraft", "RAG"],
                            output_language: "en",
                            max_documents: 25,
                        },
                        revision: 3,
                        updated_at: "2026-11-26T09:00:00Z",
                    },
                };
            }
            throw new Error("other endpoint unavailable");
        });

        const wrapper = mountForm({ draftUserId: "user-cloud-restore" });
        await flushPromises();

        const banner = wrapper.find('[data-test="draft-restored"]');
        expect(banner.exists()).toBe(true);
        expect(banner.text()).toContain("已恢复云端草稿");
        expect((wrapper.get('[data-test="topic"]').element as HTMLInputElement).value).toBe(
            "云端同步过来的综述主题",
        );

        const local = JSON.parse(
            localStorage.getItem(taskDraftStorageKey("user-cloud-restore"))!,
        ) as { syncedRevision: number; dirty: boolean };
        expect(local.syncedRevision).toBe(3);
        expect(local.dirty).toBe(false);
    });

    it("离线本地修改后联网挂载时自动补发 PUT 同步云端并记录版本号", async () => {
        saveTaskDraft("user-offline-sync", {
            workflowPreset: "literature_review",
            topic: "离线期间修改的本地主题",
            draftText: "",
            seedDois: [],
            seedDraft: "",
            snowballDirection: "both",
            keywords: ["Offline"],
            keywordDraft: "",
            maxDocuments: 15,
            dateFrom: "",
            dateTo: "",
            language: "zh",
            citationStyle: "gbt7714",
            offlineDataset: false,
            useReferenceLibrary: false,
            verifyReferences: false,
            modelSource: "platform",
            selectedCredential: "",
            selectedDocuments: [],
            literatureSources: [],
        }, { syncedRevision: 1, dirty: true });

        vi.mocked(api.get).mockImplementation(async (url: string) => {
            if (url === "/tasks/draft") {
                return {
                    data: {
                        payload: { topic: "旧云端主题", keywords: ["Old"] },
                        revision: 1,
                        updated_at: "2026-11-26T08:00:00Z",
                    },
                };
            }
            throw new Error("unavailable");
        });
        vi.mocked(api.put).mockResolvedValueOnce({
            data: {
                payload: { topic: "离线期间修改的本地主题", keywords: ["Offline"] },
                revision: 2,
                updated_at: "2026-11-26T09:05:00Z",
            },
        });

        mountForm({ draftUserId: "user-offline-sync" });
        await flushPromises();

        expect(api.put).toHaveBeenCalledWith(
            "/tasks/draft",
            expect.objectContaining({ base_revision: 1 }),
        );
        const local = JSON.parse(
            localStorage.getItem(taskDraftStorageKey("user-offline-sync"))!,
        ) as { syncedRevision: number; dirty: boolean };
        expect(local.syncedRevision).toBe(2);
        expect(local.dirty).toBe(false);
    });

    it("云端版本超前且本地有未同步改动时展示冲突提示，支持「载入云端」与「覆盖云端」", async () => {
        saveTaskDraft(
            "user-conflict",
            {
                workflowPreset: "literature_review",
                topic: "本地设备改过的主题",
                draftText: "",
                seedDois: [],
                seedDraft: "",
                snowballDirection: "both",
                keywords: ["LocalKey"],
                keywordDraft: "",
                maxDocuments: 15,
                dateFrom: "",
                dateTo: "",
                language: "zh",
                citationStyle: "gbt7714",
                offlineDataset: false,
                useReferenceLibrary: false,
                verifyReferences: false,
                modelSource: "platform",
                selectedCredential: "",
                selectedDocuments: [],
                literatureSources: [],
            },
            { syncedRevision: 1, dirty: true },
        );

        vi.mocked(api.get).mockImplementation(async (url: string) => {
            if (url === "/tasks/draft") {
                return {
                    data: {
                        payload: {
                            workflow_preset: "literature_review",
                            topic: "另一台设备保存的最新主题",
                            keywords: ["CloudKey"],
                        },
                        revision: 2,
                        updated_at: "2026-11-26T09:10:00Z",
                    },
                };
            }
            throw new Error("unavailable");
        });

        const wrapper = mountForm({ draftUserId: "user-conflict" });
        await flushPromises();

        // 展示冲突横幅且保留当前本地输入
        const banner = wrapper.find('[data-test="draft-conflict-banner"]');
        expect(banner.exists()).toBe(true);
        expect((wrapper.get('[data-test="topic"]').element as HTMLInputElement).value).toBe(
            "本地设备改过的主题",
        );

        // 点击「载入云端版本」
        await wrapper.get('[data-test="draft-conflict-use-cloud"]').trigger("click");
        await flushPromises();
        expect(wrapper.find('[data-test="draft-conflict-banner"]').exists()).toBe(false);
        expect((wrapper.get('[data-test="topic"]').element as HTMLInputElement).value).toBe(
            "另一台设备保存的最新主题",
        );

        // 模拟本地再次改动并触发 PUT 409 DRAFT_CONFLICT → 点击「用当前覆盖云端」
        vi.useFakeTimers();
        vi.mocked(api.put)
            .mockRejectedValueOnce({
                code: "DRAFT_CONFLICT",
                details: {
                    cloud: {
                        payload: { topic: "云端 v3 主题", keywords: ["V3"] },
                        revision: 3,
                        updated_at: "2026-11-26T09:12:00Z",
                    },
                },
            })
            .mockResolvedValueOnce({
                data: {
                    payload: { topic: "我最终决定覆盖的主题" },
                    revision: 4,
                    updated_at: "2026-11-26T09:15:00Z",
                },
            });

        await wrapper.get('[data-test="topic"]').setValue("我最终决定覆盖的主题");
        await vi.advanceTimersByTimeAsync(1600);
        await flushPromises();

        expect(wrapper.find('[data-test="draft-conflict-banner"]').exists()).toBe(true);

        await wrapper.get('[data-test="draft-conflict-use-local"]').trigger("click");
        await flushPromises();
        expect(api.put).toHaveBeenLastCalledWith(
            "/tasks/draft",
            expect.objectContaining({ base_revision: null }),
        );
        expect(wrapper.find('[data-test="draft-conflict-banner"]').exists()).toBe(false);
    });

    it("挂载时自动清洗已失效的 document_ids，清空内容防抖后发 DELETE /tasks/draft", async () => {
        saveTaskDraft(
            "user-prune-delete",
            {
                workflowPreset: "literature_review",
                topic: "清洗失效文档并清空测试",
                draftText: "",
                seedDois: [],
                seedDraft: "",
                snowballDirection: "both",
                keywords: ["Cleanup"],
                keywordDraft: "",
                maxDocuments: 15,
                dateFrom: "",
                dateTo: "",
                language: "zh",
                citationStyle: "gbt7714",
                offlineDataset: false,
                useReferenceLibrary: false,
                verifyReferences: false,
                modelSource: "platform",
                selectedCredential: "",
                selectedDocuments: ["doc-valid", "doc-deleted"],
                literatureSources: [],
            },
            { syncedRevision: 2, dirty: false },
        );

        vi.useFakeTimers();
        vi.mocked(api.get).mockRejectedValue(new Error("unavailable"));
        vi.mocked(api.delete).mockResolvedValueOnce({});

        const wrapper = mountForm({
            draftUserId: "user-prune-delete",
            documents: [{ id: "doc-valid", title: "有效文档" }],
        });
        await flushPromises();

        // 验证已失效文档 doc-deleted 已被清洗：提交时只剩 doc-valid
        await wrapper.get('[data-test="submit"]').trigger("click");
        const submitted = wrapper.emitted("submit")?.[0]?.[0] as { document_ids?: string[] };
        expect(submitted.document_ids).toEqual(["doc-valid"]);

        // 主动清除草稿或清空全部核心内容后触发 DELETE /tasks/draft
        await wrapper.get('[data-test="draft-discard"]').trigger("click");
        await flushPromises();
        expect(api.delete).toHaveBeenCalledWith("/tasks/draft");
    });
});

describe("TaskCreateForm · 开题报告衍生（Phase 2）", () => {
    it("按预设与来源任务预填，提交携带 source_task_id 且允许关键词为空", async () => {
        const wrapper = mountForm({
            initialPreset: "thesis_proposal",
            initialSourceTaskId: "src-task-1",
            initialTopic: "基于 STM32 的低功耗运动手环心率监测系统设计",
        });
        await flushPromises();

        expect(
            wrapper.get('[data-test="preset-thesis_proposal"]').attributes("aria-checked"),
        ).toBe("true");
        expect(
            (wrapper.get('[data-test="topic"]').element as HTMLInputElement).value,
        ).toContain("STM32");
        expect(wrapper.find('[data-test="proposal-inherit-hint"]').exists()).toBe(true);

        await wrapper.get('[data-test="submit"]').trigger("click");

        const submitted = wrapper.emitted("submit")?.[0]?.[0] as {
            workflow_preset?: string;
            source_task_id?: string;
            keywords?: string[];
        };
        expect(submitted.workflow_preset).toBe("thesis_proposal");
        expect(submitted.source_task_id).toBe("src-task-1");
        expect(submitted.keywords).toEqual([]);
    });

    it("取消继承后关键词恢复必填校验", async () => {
        const wrapper = mountForm({
            initialPreset: "thesis_proposal",
            initialSourceTaskId: "src-task-1",
            initialTopic: "基于 STM32 的低功耗运动手环心率监测系统设计",
        });
        await flushPromises();

        await wrapper.get('[data-test="proposal-detach"]').trigger("click");
        expect(wrapper.find('[data-test="proposal-inherit-hint"]').exists()).toBe(false);

        await wrapper.get('[data-test="submit"]').trigger("click");

        expect(wrapper.emitted("submit")).toBeUndefined();
        expect(wrapper.text()).toContain("关键词至少 1 个");
    });

    it("没有来源任务时按普通关键词规则校验", async () => {
        const wrapper = mountForm({ initialPreset: "thesis_proposal" });
        await flushPromises();

        expect(wrapper.find('[data-test="proposal-inherit-hint"]').exists()).toBe(false);
        await wrapper.get('[data-test="submit"]').trigger("click");

        expect(wrapper.emitted("submit")).toBeUndefined();
        expect(wrapper.text()).toContain("关键词至少 1 个");
    });
});
