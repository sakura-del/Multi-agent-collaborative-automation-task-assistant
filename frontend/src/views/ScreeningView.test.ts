import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";

import ScreeningView from "@/views/ScreeningView.vue";

vi.mock("@/api/screening", () => ({
  recordSearch: vi.fn(),
  listScreeningRecords: vi.fn(),
  createDecision: vi.fn(),
  getPrismaCounts: vi.fn(),
}));

import {
  createDecision,
  getPrismaCounts,
  listScreeningRecords,
} from "@/api/screening";

function recordRow(overrides: Record<string, unknown> = {}) {
  return {
    record_id: "r1",
    source: "arxiv",
    source_id: "a1",
    title: "RAG 综述",
    authors: ["张三"],
    abstract: "这是一篇综述摘要",
    doi: "10.1234/rag",
    fulltext_status: "unavailable",
    current_stage: "title_abstract",
    ...overrides,
  };
}

const PRISMA = {
  identified_total: 50,
  duplicates_removed: 49,
  records_screened: 1,
  included: 0,
  identified_by_source: { arxiv: 30, openalex: 20 },
  title_abstract: { screened: 0, excluded: 0 },
  fulltext: { assessed: 0, excluded: 0 },
  exclusion_reasons: {},
  markdown: "# PRISMA 2020 流程图数据",
  mermaid: "flowchart TD\n    A[\"识别 n=50\"] --> E[\"纳入 n=0\"]",
};

describe("ScreeningView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listScreeningRecords).mockResolvedValue([recordRow()]);
    vi.mocked(getPrismaCounts).mockResolvedValue(PRISMA);
  });

  it("展示候选记录与当前阶段", async () => {
    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();

    const row = wrapper.find('[data-test="screening-row-r1"]');
    expect(row.exists()).toBe(true);
    expect(row.text()).toContain("RAG 综述");
    expect(row.text()).toContain("标题/摘要");
  });

  it("展示 DOI 与来源标识", async () => {
    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();

    const row = wrapper.find('[data-test="screening-row-r1"]');
    expect(row.text()).toContain("10.1234/rag");
    expect(row.text()).toContain("arxiv:a1");
  });

  it("纳入按钮调用 createDecision 并刷新", async () => {
    vi.mocked(createDecision).mockResolvedValue({
      record_id: "r1",
      current_stage: "fulltext",
    });

    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();
    await wrapper.find('[data-test="screening-include-r1"]').trigger("click");
    await flushPromises();

    expect(createDecision).toHaveBeenCalledWith("r1", {
      stage: "title_abstract",
      verdict: "include",
    });
    expect(wrapper.text()).toContain("已纳入");
  });

  it("纳入失败时展示后端 detail（而非吞掉）", async () => {
    vi.mocked(createDecision).mockRejectedValue(new Error("网络错误"));

    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();
    await wrapper.find('[data-test="screening-include-r1"]').trigger("click");
    await flushPromises();

    expect(wrapper.find('[data-test="screening-error"]').text()).toContain("网络错误");
  });

  it("排除必须填写理由后才提交", async () => {
    vi.mocked(createDecision).mockResolvedValue({
      record_id: "r1",
      current_stage: "title_abstract",
    });

    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();

    await wrapper.find('[data-test="screening-exclude-r1"]').trigger("click");
    await flushPromises();

    expect(createDecision).not.toHaveBeenCalled();
    expect(wrapper.find('[data-test="screening-exclude-form-r1"]').exists()).toBe(true);

    await wrapper.find('[data-test="screening-exclude-reason-r1"]').setValue("不相关");
    await wrapper.find('[data-test="screening-exclude-submit-r1"]').trigger("click");
    await flushPromises();

    expect(createDecision).toHaveBeenCalledWith("r1", {
      stage: "title_abstract",
      verdict: "exclude",
      exclusion_reason: "不相关",
    });
  });

  it("排除理由为空时前端先拦（不发请求）", async () => {
    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();

    await wrapper.find('[data-test="screening-exclude-r1"]').trigger("click");
    await flushPromises();
    // 只输入空白字符
    await wrapper.find('[data-test="screening-exclude-reason-r1"]').setValue("   ");
    await wrapper.find('[data-test="screening-exclude-submit-r1"]').trigger("click");
    await flushPromises();

    expect(createDecision).not.toHaveBeenCalled();
    expect(wrapper.find('[data-test="screening-error"]').exists()).toBe(true);
  });

  it("maybe 按钮调用 createDecision 且不推进阶段", async () => {
    vi.mocked(createDecision).mockResolvedValue({
      record_id: "r1",
      current_stage: "title_abstract",
    });

    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();
    await wrapper.find('[data-test="screening-maybe-r1"]').trigger("click");
    await flushPromises();

    expect(createDecision).toHaveBeenCalledWith("r1", {
      stage: "title_abstract",
      verdict: "maybe",
    });
  });

  it("展示 PRISMA 计数（识别/去重后/纳入）", async () => {
    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();

    const prisma = wrapper.find('[data-test="screening-prisma"]');
    expect(prisma.exists()).toBe(true);
    expect(prisma.text()).toContain("50");
    expect(prisma.text()).toContain("1");
  });

  it("展示 Mermaid 源码（可复制进论文）", async () => {
    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();

    const block = wrapper.find('[data-test="screening-mermaid"]');
    expect(block.text()).toContain("flowchart TD");
  });

  it("已纳入的记录不再显示决策按钮", async () => {
    vi.mocked(listScreeningRecords).mockResolvedValue([
      recordRow({ record_id: "r2", current_stage: "included" }),
    ]);

    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();

    expect(wrapper.find('[data-test="screening-include-r2"]').exists()).toBe(false);
    expect(wrapper.find('[data-test="screening-exclude-r2"]').exists()).toBe(false);
  });

  it("按阶段过滤候选记录", async () => {
    vi.mocked(listScreeningRecords).mockResolvedValue([
      recordRow({ record_id: "r1", current_stage: "title_abstract" }),
      recordRow({ record_id: "r2", current_stage: "fulltext", title: "另一篇" }),
    ]);

    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();

    expect(wrapper.find('[data-test="screening-row-r1"]').exists()).toBe(true);
    expect(wrapper.find('[data-test="screening-row-r2"]').exists()).toBe(true);

    await wrapper.find('[data-test="screening-stage-filter"]').setValue("fulltext");
    await flushPromises();

    expect(listScreeningRecords).toHaveBeenLastCalledWith("task-1", "fulltext");
  });

  it("空记录时展示明确引导", async () => {
    vi.mocked(listScreeningRecords).mockResolvedValue([]);

    const wrapper = mount(ScreeningView, { props: { taskId: "task-1" } });
    await flushPromises();

    const empty = wrapper.find('[data-test="screening-empty"]');
    expect(empty.exists()).toBe(true);
    expect(empty.text()).not.toContain("暂无数据");
  });
});
