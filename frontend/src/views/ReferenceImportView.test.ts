import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

import { saveLibraryItems } from "@/api/reference-library";
import { importReferences } from "@/api/references";
import type { ReferenceImportResponse } from "@/api/types";
import ReferenceImportView from "@/views/ReferenceImportView.vue";

vi.mock("@/api/references", () => ({
  importReferences: vi.fn(),
}));

vi.mock("@/api/reference-library", () => ({
  saveLibraryItems: vi.fn(),
}));

vi.mock("vue-router", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

function importResult(overrides: Partial<ReferenceImportResponse> = {}): ReferenceImportResponse {
  return {
    format: "bibtex",
    count: 2,
    items: [
      {
        id: "doi:10.18653/v1/2023.evalnlp-1.16",
        type: "article-journal",
        title: "Ragas: Automated Evaluation of RAG",
        authors: ["Es, Shahul", "James, Jithin"],
        year: 2023,
        container_title: "arXiv preprint",
        volume: null,
        issue: null,
        page: null,
        publisher: null,
        doi: "10.18653/v1/2023.evalnlp-1.16",
        url: null,
      },
      {
        id: "doi:10.1234/zh.2024.001",
        type: "article-journal",
        title: "面向中文文献综述的自动化方法",
        authors: ["牛耕田", "李泽"],
        year: 2024,
        container_title: "中文信息学报",
        volume: "38",
        issue: "4",
        page: "100-110",
        publisher: null,
        doi: "10.1234/zh.2024.001",
        url: null,
      },
    ],
    ...overrides,
  };
}

async function mountView() {
  const wrapper = mount(ReferenceImportView);
  await flushPromises();
  return wrapper;
}

async function fillContent(wrapper: ReturnType<typeof mount>, text: string) {
  const textarea = wrapper.find('[data-test="import-content"]');
  await textarea.setValue(text);
}

describe("ReferenceImportView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("粘贴 BibTeX 后导入并展示条目预览", async () => {
    vi.mocked(importReferences).mockResolvedValue(importResult());

    const wrapper = await mountView();
    await fillContent(wrapper, "@article{a, title={T}}");
    await wrapper.find('[data-test="import-submit"]').trigger("submit");
    await flushPromises();

    expect(importReferences).toHaveBeenCalledTimes(1);
    const preview = wrapper.find('[data-test="import-preview"]');
    expect(preview.exists()).toBe(true);
    expect(preview.text()).toContain("Ragas");
    // 中文原样保留
    expect(preview.text()).toContain("面向中文文献综述的自动化方法");
    expect(preview.text()).toContain("牛耕田");
  });

  it("展示实际采用的格式与条目数", async () => {
    vi.mocked(importReferences).mockResolvedValue(importResult({ format: "ris", count: 2 }));

    const wrapper = await mountView();
    await fillContent(wrapper, "TY  - JOUR\nER  - ");
    await wrapper.find('[data-test="import-submit"]').trigger("submit");
    await flushPromises();

    const summary = wrapper.find('[data-test="import-summary"]');
    expect(summary.text()).toContain("RIS");
    expect(summary.text()).toContain("2");
  });

  it("空内容时前端拦住，不发请求", async () => {
    const wrapper = await mountView();
    await wrapper.find('[data-test="import-submit"]').trigger("submit");
    await flushPromises();

    expect(importReferences).not.toHaveBeenCalled();
    expect(wrapper.find('[data-test="import-error"]').text().length).toBeGreaterThan(0);
  });

  it("可显式切换格式", async () => {
    vi.mocked(importReferences).mockResolvedValue(importResult({ format: "ris" }));

    const wrapper = await mountView();
    await fillContent(wrapper, "TY  - JOUR\nER  - ");
    await wrapper.find('[data-test="import-format"]').setValue("ris");
    await wrapper.find('[data-test="import-submit"]').trigger("submit");
    await flushPromises();

    const payload = vi.mocked(importReferences).mock.calls[0][0];
    expect(payload.format).toBe("ris");
  });

  it("默认格式为 auto（交给后端探测）", async () => {
    vi.mocked(importReferences).mockResolvedValue(importResult());

    const wrapper = await mountView();
    await fillContent(wrapper, "@article{a, title={T}}");
    await wrapper.find('[data-test="import-submit"]').trigger("submit");
    await flushPromises();

    const payload = vi.mocked(importReferences).mock.calls[0][0];
    expect(payload.format).toBe("auto");
  });

  it("后端拒绝时展示可读原因", async () => {
    vi.mocked(importReferences).mockRejectedValue(
      new Error("无法识别内容格式：请显式指定 format=bibtex 或 format=ris")
    );

    const wrapper = await mountView();
    await fillContent(wrapper, "垃圾内容");
    await wrapper.find('[data-test="import-submit"]').trigger("submit");
    await flushPromises();

    expect(wrapper.find('[data-test="import-error"]').text()).toContain("无法识别内容格式");
  });

  it("解析出 0 条时给出明确空态（不是错误）", async () => {
    vi.mocked(importReferences).mockResolvedValue(importResult({ count: 0, items: [] }));

    const wrapper = await mountView();
    // 内容非空（前端不拦），但后端解析出 0 条（如只有注释行）
    await fillContent(wrapper, "% 只有注释，没有条目");
    await wrapper.find('[data-test="import-submit"]').trigger("submit");
    await flushPromises();

    expect(wrapper.find('[data-test="import-empty"]').exists()).toBe(true);
    // 空态不应同时报错
    expect(wrapper.find('[data-test="import-error"]').exists()).toBe(false);
  });

  it("可清空已导入结果重新开始", async () => {
    vi.mocked(importReferences).mockResolvedValue(importResult());

    const wrapper = await mountView();
    await fillContent(wrapper, "@article{a, title={T}}");
    await wrapper.find('[data-test="import-submit"]').trigger("submit");
    await flushPromises();
    expect(wrapper.find('[data-test="import-preview"]').exists()).toBe(true);

    await wrapper.find('[data-test="import-reset"]').trigger("click");
    await flushPromises();

    expect(wrapper.find('[data-test="import-preview"]').exists()).toBe(false);
  });
});

/**
 * 保存到引用库（2026-10-26 引用库计划）。
 *
 * ## 为什么加这个
 *
 * 取证发现导入页解析完全正常，但**解析完什么也做不了**：
 * 可点元素只有「解析预览」和「重新开始」（`AFTER_CLICKABLE`），
 * 后端没有引用库表，任务文献只来自检索源。
 *
 * 所以本页必须有真实出口：保存到引用库。
 */
describe("ReferenceImportView 保存到引用库", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(importReferences).mockResolvedValue(importResult());
    vi.mocked(saveLibraryItems).mockResolvedValue({ created: 2, skipped: 0, merged: 0, rejected: [] });
  });

  async function importFirst(wrapper: ReturnType<typeof mount>) {
    await wrapper.find('[data-test="import-content"]').setValue("@article{a, title={T}}");
    await wrapper.find('[data-test="import-submit"]').trigger("submit");
    await flushPromises();
  }

  it("解析后出现保存按钮", async () => {
    const wrapper = mount(ReferenceImportView);
    await flushPromises();

    // 解析前没有保存按钮
    expect(wrapper.find('[data-test="import-save"]').exists()).toBe(false);

    await importFirst(wrapper);
    expect(wrapper.find('[data-test="import-save"]').exists()).toBe(true);
  });

  it("保存时提交 CslItem 形态的载荷（不是展示结构）", async () => {
    const wrapper = mount(ReferenceImportView);
    await flushPromises();
    await importFirst(wrapper);

    await wrapper.find('[data-test="import-save"]').trigger("click");
    await flushPromises();

    expect(saveLibraryItems).toHaveBeenCalledTimes(1);
    const payload = vi.mocked(saveLibraryItems).mock.calls[0][0];
    expect(payload).toHaveLength(2);
    // 必须是 CslItem 形态：author 是对象数组、字段名 issued_year
    expect(payload[0]).toHaveProperty("author");
    expect(payload[0]).toHaveProperty("issued_year");
    expect(payload[0]).not.toHaveProperty("authors");
    expect(payload[0]).not.toHaveProperty("year");
  });

  it("保存成功后展示新增条数并给出去引用库的入口", async () => {
    const wrapper = mount(ReferenceImportView);
    await flushPromises();
    await importFirst(wrapper);

    await wrapper.find('[data-test="import-save"]').trigger("click");
    await flushPromises();

    const result = wrapper.find('[data-test="import-save-result"]');
    expect(result.exists()).toBe(true);
    expect(result.text()).toContain("新增 2 条");
    expect(wrapper.find('[data-test="import-goto-library"]').exists()).toBe(true);
  });

  it("部分成功时逐条展示拒绝原因", async () => {
    // 这是本功能最重要的展示：用户要知道哪些没进去、为什么
    vi.mocked(saveLibraryItems).mockResolvedValue({
      created: 1,
      skipped: 0,
      merged: 0,
      rejected: [
        { ref_id: "doi:10.1/x", title: "某预印本", reason: "缺出版年份：无法确定发表时间" },
      ],
    });

    const wrapper = mount(ReferenceImportView);
    await flushPromises();
    await importFirst(wrapper);
    await wrapper.find('[data-test="import-save"]').trigger("click");
    await flushPromises();

    const result = wrapper.find('[data-test="import-save-result"]');
    expect(result.text()).toContain("新增 1 条");
    expect(result.text()).toContain("未能入库 1 条");
    expect(result.text()).toContain("缺出版年份");
    expect(result.text()).toContain("某预印本");
  });

  it("已存在的条目计入 skipped 并展示", async () => {
    vi.mocked(saveLibraryItems).mockResolvedValue({ created: 0, skipped: 2, merged: 0, rejected: [] });

    const wrapper = mount(ReferenceImportView);
    await flushPromises();
    await importFirst(wrapper);
    await wrapper.find('[data-test="import-save"]').trigger("click");
    await flushPromises();

    expect(wrapper.find('[data-test="import-save-result"]').text()).toContain("已存在 2 条");
  });

  it("保存失败时展示可读原因", async () => {
    vi.mocked(saveLibraryItems).mockRejectedValue(
      new Error("全部条目都无法入库：缺出版年份")
    );

    const wrapper = mount(ReferenceImportView);
    await flushPromises();
    await importFirst(wrapper);
    await wrapper.find('[data-test="import-save"]').trigger("click");
    await flushPromises();

    expect(wrapper.find('[data-test="import-save-error"]').text()).toContain("缺出版年份");
  });

  it("页面说明要提到「创建任务时可用引用库」（用户需要知道出口在哪）", async () => {
    const wrapper = mount(ReferenceImportView);
    await flushPromises();

    const text = wrapper.text();
    expect(text).toContain("引用库");
    expect(text).toMatch(/创建任务/);
  });
});
