import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

import TaskCreateForm from "@/components/tasks/TaskCreateForm.vue";
import type { CitationStyleOut } from "@/api/types";

// 样式下拉走真实 api 模块（列表接口由 client mock 提供），与 DocumentUpload 测试同风格
vi.mock("@/api/client", () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    delete: vi.fn(),
  },
}));

import api from "@/api/client";

// 表单只负责校验与 payload 组装：提交动作由父组件（TaskListView）发起
function mountForm(props: Record<string, unknown> = {}) {
  return mount(TaskCreateForm, {
    props: { pending: false, error: null, documents: [], ...props },
  });
}

function apiStyleRow(overrides: Partial<CitationStyleOut> = {}): CitationStyleOut {
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
  await wrapper.find('[data-test="topic"]').setValue("检索增强生成与多智能体协作的质量评估");
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
    await wrapper.find("form").trigger("submit");

    expect(wrapper.emitted("submit")).toBeUndefined();
    expect(wrapper.find('[data-test="validation-error"]').text()).toContain("主题");
  });

  it("requires between 1 and 10 keywords", async () => {
    const wrapper = mountForm();
    await wrapper.find('[data-test="topic"]').setValue("检索增强生成系统综述");
    await wrapper.find("form").trigger("submit");
    expect(wrapper.emitted("submit")).toBeUndefined();
    expect(wrapper.find('[data-test="validation-error"]').text()).toContain("关键词");

    await fillValidForm(wrapper);
    for (let index = 0; index < 10; index += 1) {
      const input = wrapper.find('[data-test="keyword-input"]');
      await input.setValue(`关键词${index}`);
      await input.trigger("keydown.enter");
    }
    await wrapper.find("form").trigger("submit");

    expect(wrapper.emitted("submit")).toBeUndefined();
    expect(wrapper.find('[data-test="validation-error"]').text()).toContain("最多 10");
  });

  it("rejects max documents outside 1..50", async () => {
    const wrapper = mountForm();
    await fillValidForm(wrapper);
    await wrapper.find('[data-test="max-documents"]').setValue("0");
    await wrapper.find("form").trigger("submit");
    expect(wrapper.emitted("submit")).toBeUndefined();

    await wrapper.find('[data-test="max-documents"]').setValue("51");
    await wrapper.find("form").trigger("submit");
    expect(wrapper.emitted("submit")).toBeUndefined();
    expect(wrapper.find('[data-test="validation-error"]').text()).toContain("文献数");
  });

  it("rejects a date range whose start is after the end", async () => {
    const wrapper = mountForm();
    await fillValidForm(wrapper);
    await wrapper.find('[data-test="date-from"]').setValue("2026-05-01");
    await wrapper.find('[data-test="date-to"]').setValue("2026-01-01");
    await wrapper.find("form").trigger("submit");

    expect(wrapper.emitted("submit")).toBeUndefined();
    expect(wrapper.find('[data-test="validation-error"]').text()).toContain("日期");
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
    await wrapper.find("form").trigger("submit");

    const emitted = wrapper.emitted("submit");
    expect(emitted).toHaveLength(1);
    expect(emitted?.[0][0]).toEqual({
      topic: "检索增强生成与多智能体协作的质量评估",
      keywords: ["RAG", "multi-agent"],
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

  it("disables submit while pending", () => {
    const wrapper = mountForm({ pending: true });
    expect(wrapper.find('[data-test="submit"]').attributes("disabled")).toBeDefined();
  });

  it("shows the server error message near the form", () => {
    const wrapper = mountForm({ error: "后端不可用" });
    expect(wrapper.find('[data-test="form-error"]').text()).toContain("后端不可用");
  });

  // ===== 动态样式下拉（Issue #4 M2 §1.5）=====

  it("loads the style list and preselects default_style_id", async () => {
    vi.mocked(api.get).mockResolvedValueOnce({
      data: {
        styles: [
          apiStyleRow(),
          apiStyleRow({ style_id: "apa", title: "APA 7th", is_default: false }),
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
    expect((select.element as HTMLSelectElement).value).toBe("gbt7714-2025-numeric");
    // 自定义样式同样出现在下拉里
    expect(select.text()).toContain("我的国标样式");

    await fillValidForm(wrapper);
    await wrapper.find("form").trigger("submit");

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
    await wrapper.find("form").trigger("submit");

    expect(wrapper.emitted("submit")?.[0][0]).toMatchObject({ citation_style: "my-style" });
  });

  it("falls back to the built-in styles when the list request fails", async () => {
    const wrapper = mountForm();

    await flushPromises();

    // 列表不可用时保留内置两项，表单必须仍然可用
    expect(wrapper.find('[data-test="style-select"]').exists()).toBe(false);
    expect(wrapper.find('[data-test="style-gbt7714"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="style-apa"]').exists()).toBe(true);

    await fillValidForm(wrapper);
    await wrapper.find("form").trigger("submit");

    expect(wrapper.emitted("submit")?.[0][0]).toMatchObject({ citation_style: "gbt7714" });
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
    await wrapper.find("form").trigger("submit");

    expect(wrapper.emitted("submit")?.[0][0]).toMatchObject({ verify_references: false });
  });

  it("submits verify_references: true when the switch is enabled", async () => {
    const wrapper = mountForm();
    await wrapper.find('[data-test="verify-references"]').setValue(true);
    await fillValidForm(wrapper);
    await wrapper.find("form").trigger("submit");

    expect(wrapper.emitted("submit")?.[0][0]).toMatchObject({ verify_references: true });
  });

  it("explains the external comparison and the extra latency", () => {
    const wrapper = mountForm();
    const label = wrapper.find('[data-test="verify-references-label"]').text();

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
        return Promise.resolve({ data: { styles: [apiStyleRow()], default_style_id: "gbt7714-2025-numeric" } });
      }
      if (url === "/reference-library/summary") {
        return Promise.resolve({ data: { total: 7, usable_as_source: 7 } });
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

  it("引用库为空时给出引导", async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url === "/citation-styles") {
        return Promise.resolve({ data: { styles: [apiStyleRow()], default_style_id: "gbt7714-2025-numeric" } });
      }
      return Promise.resolve({ data: { total: 0, usable_as_source: 0 } });
    });

    const wrapper = mountForm();
    await flushPromises();

    expect(wrapper.find('[data-test="reference-library-label"]').text()).toMatch(/导入文献/);
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

    await wrapper.find('[data-test="topic"]').setValue("检索增强生成的质量评估方法");
    await wrapper.find('[data-test="keyword-input"]').setValue("RAG");
    await wrapper.find('[data-test="keyword-input"]').trigger("keydown.enter");
    await wrapper.find('[data-test="reference-library"]').setValue(true);
    await wrapper.find("form").trigger("submit");

    const payload = wrapper.emitted("submit")?.[0]?.[0] as Record<string, unknown>;
    expect(payload.reference_library).toBe(true);
  });

  it("不勾选时 payload 里 reference_library 为 false", async () => {
    const wrapper = mountForm();
    await flushPromises();

    await wrapper.find('[data-test="topic"]').setValue("检索增强生成的质量评估方法");
    await wrapper.find('[data-test="keyword-input"]').setValue("RAG");
    await wrapper.find('[data-test="keyword-input"]').trigger("keydown.enter");
    await wrapper.find("form").trigger("submit");

    const payload = wrapper.emitted("submit")?.[0]?.[0] as Record<string, unknown>;
    expect(payload.reference_library).toBe(false);
  });

  it("摘要接口失败不影响表单（局部失败不连坐）", async () => {
    vi.mocked(api.get).mockImplementation((url: string) => {
      if (url === "/citation-styles") {
        return Promise.resolve({ data: { styles: [apiStyleRow()], default_style_id: "gbt7714-2025-numeric" } });
      }
      return Promise.reject(new Error("summary 500"));
    });

    const wrapper = mountForm();
    await flushPromises();

    // 表单仍可提交
    expect(wrapper.find('[data-test="submit"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="reference-library"]').exists()).toBe(true);
  });

  it("启用引用库时禁用离线语料（两者互斥，避免配置冲突）", async () => {
    const wrapper = mountForm();
    await flushPromises();

    await wrapper.find('[data-test="reference-library"]').setValue(true);
    const offline = wrapper.find('[data-test="offline"]');
    expect((offline.element as HTMLInputElement).disabled).toBe(true);
  });
});
